from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
from cv2_enumerate_cameras import enumerate_cameras
from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel


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
        self._writer: cv2.VideoWriter | None = None
        self._recorded_frames = 0

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

            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)
            capture.set(cv2.CAP_PROP_FPS, self.target_fps)
            ok, first_frame = capture.read()
            if not ok or first_frame is None:
                self.open_error = f"摄像头没有返回画面：{self.device.name}"
                self.ready.set()
                return

            self.height, self.width = first_frame.shape[:2]
            reported_fps = float(capture.get(cv2.CAP_PROP_FPS))
            self.fps = (
                min(reported_fps, self.target_fps)
                if reported_fps > 0
                else self.target_fps
            )
            self.ready.set()
            frame = first_frame

            while not self.isInterruptionRequested():
                self._process_frame(frame)
                ok, frame = capture.read()
                if not ok or frame is None:
                    self.error.emit(
                        f"摄像头画面中断：{self.device.name}"
                    )
                    return
        finally:
            self.ready.set()
            with self._lock:
                if self._writer is not None:
                    self._writer.release()
                    self._writer = None
            capture.release()

    def _process_frame(self, frame) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.write(frame)
                self._recorded_frames += 1
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
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(
                str(path),
                fourcc,
                self.fps,
                (self.width, self.height),
            )
            if not writer.isOpened():
                writer.release()
                raise RuntimeError(f"无法创建录像文件：{path}")
            self._writer = writer
            self._recorded_frames = 0

    def recording_timestamp_ms(self) -> int:
        with self._lock:
            if self._writer is None:
                raise RuntimeError("recording has not started")
            return round(self._recorded_frames * 1000 / self.fps)

    def stop_recording(self) -> int:
        with self._lock:
            if self._writer is None:
                raise RuntimeError("recording is not active")
            duration_ms = round(self._recorded_frames * 1000 / self.fps)
            self._writer.release()
            self._writer = None
            self._recorded_frames = 0
            return duration_ms

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
        self.worker = None
        self.device_id = None
        self._preview = None
        self._requested_path = None
        self.width = 0
        self.height = 0
        self.active_changed.emit(False)
