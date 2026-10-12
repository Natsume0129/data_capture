from __future__ import annotations

import threading
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
from time import monotonic

import cv2
from cv2_enumerate_cameras import enumerate_cameras
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

from .video_recorder import TimestampedVideoWriter


MIN_CAPTURE_FPS = 25.0


@dataclass(frozen=True)
class CameraDevice:
    index: int
    name: str
    path: str
    backend: int

    def id(self) -> bytes:
        identity = self.path or f"{self.backend}:{self.index}:{self.name}"
        return identity.encode("utf-8")

    def description(self) -> str:
        return self.name


def prefers_mjpeg(device: CameraDevice) -> bool:
    return "c920" in device.name.casefold()


class CaptureThread(QThread):
    frame_ready = Signal(QImage)
    error = Signal(str)

    def __init__(
        self,
        device: CameraDevice,
        target_width: int,
        target_height: int,
        target_fps: float,
    ):
        super().__init__()
        self.device = device
        self.target_width = target_width
        self.target_height = target_height
        self.target_fps = target_fps
        self.ready = threading.Event()
        self.open_error = ""
        self.width = 0
        self.height = 0
        self.fps = target_fps
        self._preview_enabled = True
        self._lock = threading.RLock()
        self._writer: TimestampedVideoWriter | None = None
        self._last_frame = None
        self._last_frame_at: float | None = None
        self._frame_times: deque[float] = deque(maxlen=120)
        self.capture_configuration: dict = {
            "device_name": self.device.name,
            "backend": self.device.backend,
            "requested_width": self.target_width,
            "requested_height": self.target_height,
            "requested_fps": self.target_fps,
            "preferred_fourcc": "MJPG" if prefers_mjpeg(self.device) else None,
            "actual_firstframe_dims": None,
            "reported_width": None,
            "reported_height": None,
            "reported_fps": None,
            "reported_fourcc": None,
            "reported_fourcc_code": None,
            "fallback_reason": None,
            "attempts": [],
        }

    def _request_capture_properties(self, capture, request_mjpeg: bool) -> dict:
        width_ok = bool(capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width))
        height_ok = bool(capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height))
        fps_ok = bool(capture.set(cv2.CAP_PROP_FPS, self.target_fps))
        # DirectShow can renegotiate the input format when dimensions/FPS change.
        fourcc_ok = (
            bool(capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")))
            if request_mjpeg else None
        )
        return {
            "requested_fourcc": "MJPG" if request_mjpeg else None,
            "fourcc_set_ok": fourcc_ok,
            "width_set_ok": width_ok,
            "height_set_ok": height_ok,
            "fps_set_ok": fps_ok,
            "first_frame_ok": False,
            "fallback_reason": (
                "Driver declined MJPG request; continuing with its default input."
                if request_mjpeg and not fourcc_ok else None
            ),
        }

    def get_capture_info(self) -> dict:
        with self._lock:
            info = deepcopy(self.capture_configuration)
            count = len(self._frame_times)
            span = self._frame_times[-1] - self._frame_times[0] if count > 1 else 0.0
            measured_fps = (count - 1) / span if span > 0 else None
            intervals = [b - a for a, b in zip(self._frame_times, list(self._frame_times)[1:])]
            info.update({
                "measured_capture_fps": measured_fps,
                "measured_window_seconds": span,
                "observed_frames": count,
                "minimum_capture_fps": MIN_CAPTURE_FPS,
                "capture_rate_status": (
                    "warming_up" if span < 2.0
                    else "ok" if measured_fps >= MIN_CAPTURE_FPS else "low"
                ),
                "max_frame_interval_ms": max(intervals) * 1000 if intervals else None,
            })
            return info

    def run(self) -> None:
        capture = cv2.VideoCapture(self.device.index, self.device.backend)
        try:
            if not capture.isOpened():
                self.open_error = (
                    f"无法打开摄像头：{self.device.name} "
                    f"(DirectShow index {self.device.index})"
                )
                self.ready.set()
                return

            request_mjpeg = prefers_mjpeg(self.device)
            attempts = [self._request_capture_properties(capture, request_mjpeg)]
            with self._lock:
                self.capture_configuration["attempts"] = attempts
                self.capture_configuration["fallback_reason"] = attempts[0]["fallback_reason"]
            ok, first_frame = capture.read()
            captured_at = monotonic()
            attempts[-1]["first_frame_ok"] = bool(ok and first_frame is not None)
            if not attempts[-1]["first_frame_ok"] and attempts[-1]["fourcc_set_ok"]:
                attempts[-1]["fallback_reason"] = (
                    "MJPG request produced no frame; retrying driver default."
                )
                with self._lock:
                    self.capture_configuration["fallback_reason"] = attempts[-1]["fallback_reason"]
                capture.release()
                capture = cv2.VideoCapture(self.device.index, self.device.backend)
                if not capture.isOpened():
                    self.open_error = f"无法重新打开摄像头默认输入：{self.device.name}"
                    self.ready.set()
                    return
                attempts.append(self._request_capture_properties(capture, False))
                ok, first_frame = capture.read()
                captured_at = monotonic()
                attempts[-1]["first_frame_ok"] = bool(ok and first_frame is not None)
            if not ok or first_frame is None:
                self.open_error = f"摄像头没有返回画面：{self.device.name}"
                with self._lock:
                    self.capture_configuration["error"] = self.open_error
                self.ready.set()
                return

            self.height, self.width = first_frame.shape[:2]

            def driver_value(property_id: int) -> float | None:
                value = float(capture.get(property_id))
                return value if math.isfinite(value) else None

            reported_fps = driver_value(cv2.CAP_PROP_FPS)
            fourcc_value = driver_value(cv2.CAP_PROP_FOURCC)
            fourcc_code = int(fourcc_value) & 0xFFFFFFFF if fourcc_value is not None else None
            if (
                attempts[-1]["requested_fourcc"] == "MJPG"
                and attempts[-1]["fourcc_set_ok"]
                and fourcc_code != cv2.VideoWriter_fourcc(*"MJPG")
            ):
                attempts[-1]["fallback_reason"] = (
                    "Driver accepted MJPG request but reported a different or unknown final input format."
                )
            self.fps = (
                min(reported_fps, self.target_fps)
                if reported_fps is not None and reported_fps > 0
                else self.target_fps
            )
            with self._lock:
                self.capture_configuration = {
                    "device_name": self.device.name,
                    "backend": self.device.backend,
                    "requested_width": self.target_width,
                    "requested_height": self.target_height,
                    "requested_fps": self.target_fps,
                    "preferred_fourcc": "MJPG" if request_mjpeg else None,
                    "actual_firstframe_dims": {"width": self.width, "height": self.height},
                    "reported_width": driver_value(cv2.CAP_PROP_FRAME_WIDTH),
                    "reported_height": driver_value(cv2.CAP_PROP_FRAME_HEIGHT),
                    "reported_fps": reported_fps,
                    "reported_fourcc": (
                        fourcc_code.to_bytes(4, "little").decode("ascii", errors="replace").rstrip("\x00")
                        if fourcc_code is not None else None
                    ),
                    "reported_fourcc_code": fourcc_code,
                    "fallback_reason": next(
                        (item["fallback_reason"] for item in attempts if item["fallback_reason"]), None
                    ),
                    "attempts": attempts,
                }
                self._last_frame = first_frame
                self._last_frame_at = captured_at
            self.ready.set()
            frame = first_frame

            while not self.isInterruptionRequested():
                self._process_frame(frame, captured_at)
                ok, frame = capture.read()
                captured_at = monotonic()
                if not ok or frame is None:
                    self.error.emit(
                        f"摄像头画面中断：{self.device.name}"
                    )
                    return
        except Exception as exc:
            message = f"摄像头采集或录像失败：{self.device.name}：{exc}"
            if not self.ready.is_set():
                self.open_error = message
            else:
                self.error.emit(message)
        finally:
            self.ready.set()
            with self._lock:
                if self._writer is not None:
                    self._writer.abort()
                    self._writer = None
            capture.release()

    def _process_frame(self, frame, captured_at: float | None = None) -> None:
        if captured_at is None:
            captured_at = monotonic()
        with self._lock:
            if not self._frame_times or captured_at > self._frame_times[-1]:
                self._frame_times.append(captured_at)
            self._last_frame = frame
            self._last_frame_at = captured_at
            if self._writer is not None:
                self._writer.write_frame(frame, captured_at)
            preview_enabled = self._preview_enabled
        if preview_enabled:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            image = QImage(
                rgb.data,
                width,
                height,
                rgb.strides[0],
                QImage.Format.Format_RGB888,
            ).copy()
            self.frame_ready.emit(image)

    def set_preview_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._preview_enabled = enabled

    def start_recording(self, path: Path) -> None:
        with self._lock:
            if self._writer is not None:
                raise RuntimeError("a recording is already active")
            started_at = monotonic()
            if self._last_frame is None or self._last_frame_at is None:
                raise RuntimeError("摄像头尚未返回画面，无法开始录像。")
            if started_at - self._last_frame_at > 2.0:
                raise RuntimeError("摄像头超过2秒没有返回新画面，无法开始录像。")
            writer = TimestampedVideoWriter(
                path, self.width, self.height, self.fps, started_at
            )
            try:
                writer.write_frame(self._last_frame, self._last_frame_at, initial=True)
            except Exception:
                writer.abort()
                raise
            self._writer = writer

    def recording_timestamp_ms(self) -> int:
        with self._lock:
            if self._writer is None:
                raise RuntimeError("recording has not started")
            return self._writer.timestamp_ms(monotonic())

    def stop_recording(self) -> int:
        with self._lock:
            if self._writer is None:
                raise RuntimeError("recording is not active")
            writer = self._writer
            self._writer = None
            return writer.finish(monotonic())

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._writer is not None


class CameraController(QObject):
    error = Signal(str)
    active_changed = Signal(bool)
    resolution_changed = Signal(int, int)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.worker: CaptureThread | None = None
        self.device_id: bytes | None = None
        self.width = 0
        self.height = 0
        self.frame_rate = 30.0
        self._preview: QLabel | None = None
        self._requested_path: Path | None = None
        self._last_capture_info: dict = {}

    @staticmethod
    def devices() -> list[CameraDevice]:
        return [
            CameraDevice(
                index=int(info.index),
                name=str(info.name),
                path=str(info.path or ""),
                backend=int(info.backend),
            )
            for info in enumerate_cameras(cv2.CAP_DSHOW)
        ]

    @staticmethod
    def find_device(device_id: bytes) -> CameraDevice | None:
        for device in CameraController.devices():
            if device.id() == device_id:
                return device
        return None

    @property
    def is_active(self) -> bool:
        return bool(self.worker and self.worker.isRunning())

    @property
    def capture_info(self) -> dict:
        return self.worker.get_capture_info() if self.worker else deepcopy(self._last_capture_info)

    @property
    def is_recording(self) -> bool:
        return bool(self.worker and self.worker.is_recording)

    def open(
        self,
        device: CameraDevice,
        preview: QLabel,
        target_width: int = 1920,
        target_height: int = 1080,
    ) -> tuple[int, int]:
        requested_id = device.id()
        if self.worker and self.device_id == requested_id and self.worker.isRunning():
            self.set_preview(preview)
            return self.width, self.height

        self.close()
        self._last_capture_info = {}
        self._preview = preview
        worker = CaptureThread(device, target_width, target_height, 30.0)
        worker.frame_ready.connect(self._display_frame)
        worker.error.connect(self.error.emit)
        self.worker = worker
        self.device_id = requested_id
        worker.start()
        if not worker.ready.wait(timeout=10):
            self.close()
            raise TimeoutError(f"打开摄像头超时：{device.name}")
        if worker.open_error:
            message = worker.open_error
            self.close()
            raise RuntimeError(message)

        self.width = worker.width
        self.height = worker.height
        self.frame_rate = worker.fps
        self.active_changed.emit(True)
        self.resolution_changed.emit(self.width, self.height)
        return self.width, self.height

    def set_preview(self, preview: QLabel | None) -> None:
        self._preview = preview
        if self.worker:
            self.worker.set_preview_enabled(preview is not None)

    def _display_frame(self, image: QImage) -> None:
        if self._preview is None:
            return
        pixmap = QPixmap.fromImage(image)
        self._preview.setPixmap(
            pixmap.scaled(
                self._preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def start_recording(self, output_path: Path) -> Path:
        if not self.worker or not self.worker.isRunning():
            raise RuntimeError("camera is not active")
        if self.worker.is_recording:
            raise RuntimeError("a recording is already active")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._requested_path = output_path.resolve()
        self.worker.start_recording(self._requested_path)
        return self._requested_path

    def timestamp_ms(self) -> int:
        if not self.worker:
            raise RuntimeError("camera is not active")
        return self.worker.recording_timestamp_ms()

    def stop_recording(self) -> tuple[Path, int]:
        if not self.worker or self._requested_path is None:
            raise RuntimeError("recording is not active")
        duration_ms = self.worker.stop_recording()
        path = self._requested_path
        self._requested_path = None
        if path.exists():
            path.chmod(0o600)
        return path, duration_ms

    def close(self) -> None:
        worker = self.worker
        if worker:
            worker.requestInterruption()
            worker.wait(5000)
            self._last_capture_info = worker.get_capture_info()
        self.worker = None
        self.device_id = None
        self._preview = None
        self._requested_path = None
        self.width = 0
        self.height = 0
        self.active_changed.emit(False)
