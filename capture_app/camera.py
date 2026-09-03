from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, QUrl, Signal
from PySide6.QtMultimedia import (
    QCamera,
    QCameraDevice,
    QCameraFormat,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaFormat,
    QMediaRecorder,
)
from PySide6.QtMultimediaWidgets import QVideoWidget


class CameraController(QObject):
    error = Signal(str)
    active_changed = Signal(bool)
    resolution_changed = Signal(int, int)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.capture_session = QMediaCaptureSession(self)
        self.recorder = QMediaRecorder(self)
        self.capture_session.setRecorder(self.recorder)
        self.camera: QCamera | None = None
        self.device_id: bytes | None = None
        self.resolution = QSize()
        self.frame_rate = 30.0
        self._recording_started_ns: int | None = None
        self._requested_path: Path | None = None

        self.recorder.errorOccurred.connect(
            lambda _error, message: self.error.emit(message or "media recorder error")
        )

    @staticmethod
    def devices() -> list[QCameraDevice]:
        return list(QMediaDevices.videoInputs())

    @staticmethod
    def find_device(device_id: bytes) -> QCameraDevice | None:
        for device in CameraController.devices():
            if bytes(device.id()) == device_id:
                return device
        return None

    @staticmethod
    def _choose_format(
        device: QCameraDevice,
        target_width: int,
        target_height: int,
    ) -> QCameraFormat:
        formats = list(device.videoFormats())
        if not formats:
            return QCameraFormat()

        exact = [
            item
            for item in formats
            if item.resolution().width() == target_width
            and item.resolution().height() == target_height
        ]
        if exact:
            return max(exact, key=lambda item: item.maxFrameRate())

        target_ratio = target_width / target_height

        def score(item: QCameraFormat) -> tuple[float, int, float]:
            size = item.resolution()
            ratio = size.width() / max(size.height(), 1)
            dimension_distance = abs(size.width() - target_width) + abs(
                size.height() - target_height
            )
            return (
                abs(ratio - target_ratio),
                dimension_distance,
                -item.maxFrameRate(),
            )

        return min(formats, key=score)

    @property
    def is_active(self) -> bool:
        return bool(self.camera and self.camera.isActive())

    @property
    def is_recording(self) -> bool:
        return (
            self.recorder.recorderState()
            == QMediaRecorder.RecorderState.RecordingState
        )

    def open(
        self,
        device: QCameraDevice,
        preview: QVideoWidget,
        target_width: int = 1920,
        target_height: int = 1080,
    ) -> tuple[int, int]:
        requested_id = bytes(device.id())
        if self.camera and self.device_id == requested_id:
            self.set_preview(preview)
            if not self.camera.isActive():
                self.camera.start()
            return self.resolution.width(), self.resolution.height()

        self.close()
        self.camera = QCamera(device, self)
        self.device_id = requested_id
        camera_format = self._choose_format(device, target_width, target_height)
        if not camera_format.isNull():
            self.camera.setCameraFormat(camera_format)
            self.resolution = camera_format.resolution()
            self.frame_rate = min(max(camera_format.maxFrameRate(), 1.0), 30.0)
        else:
            self.resolution = QSize(target_width, target_height)
            self.frame_rate = 30.0

        media_format = QMediaFormat()
        media_format.setFileFormat(QMediaFormat.FileFormat.MPEG4)
        media_format.setVideoCodec(QMediaFormat.VideoCodec.H264)
        self.recorder.setMediaFormat(media_format)
        self.recorder.setQuality(QMediaRecorder.Quality.VeryHighQuality)
        self.recorder.setVideoResolution(self.resolution)
        self.recorder.setVideoFrameRate(self.frame_rate)

        self.camera.errorOccurred.connect(
            lambda _error, message: self.error.emit(message or "camera error")
        )
        self.camera.activeChanged.connect(self.active_changed.emit)
        self.capture_session.setCamera(self.camera)
        self.set_preview(preview)
        self.camera.start()
        self.resolution_changed.emit(
            self.resolution.width(), self.resolution.height()
        )
        return self.resolution.width(), self.resolution.height()

    def set_preview(self, preview: QVideoWidget | None) -> None:
        self.capture_session.setVideoOutput(preview)

    def start_recording(self, output_path: Path) -> Path:
        if not self.camera or not self.camera.isActive():
            raise RuntimeError("camera is not active")
        if self.is_recording:
            raise RuntimeError("a recording is already active")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._requested_path = output_path.resolve()
        self.recorder.setOutputLocation(
            QUrl.fromLocalFile(str(self._requested_path))
        )
        self._recording_started_ns = time.perf_counter_ns()
        self.recorder.record()
        if not self.is_recording:
            raise RuntimeError(
                self.recorder.errorString() or "recording did not start"
            )
        return self._requested_path

    def timestamp_ms(self) -> int:
        if self._recording_started_ns is None:
            raise RuntimeError("recording has not started")
        wall_ms = (time.perf_counter_ns() - self._recording_started_ns) // 1_000_000
        media_ms = int(self.recorder.duration())
        return media_ms if media_ms > 0 else int(wall_ms)

    def stop_recording(self) -> tuple[Path, int]:
        if not self.is_recording:
            raise RuntimeError("recording is not active")
        final_timestamp = self.timestamp_ms()
        self.recorder.stop()
        actual = self.recorder.actualLocation().toLocalFile()
        path = Path(actual).resolve() if actual else self._requested_path
        if path is None:
            raise RuntimeError("recorder did not provide an output path")
        self.recorder.setOutputLocation(QUrl())
        if path.exists():
            path.chmod(0o600)
        self._recording_started_ns = None
        self._requested_path = None
        return path, final_timestamp

    def close(self) -> None:
        if self.is_recording:
            self.recorder.stop()
        if self.camera:
            self.camera.stop()
            self.capture_session.setCamera(None)
            self.camera.deleteLater()
        self.camera = None
        self.device_id = None
        self._recording_started_ns = None
        self._requested_path = None
