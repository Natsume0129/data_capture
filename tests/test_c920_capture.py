import numpy as np
import pytest

from capture_app import camera


class SynchronousCaptureThread(camera.CaptureThread):
    """Exercise run() without starting a Qt thread or opening hardware."""

    def __init__(self, device: camera.CameraDevice):
        super().__init__(device, 1920, 1080, 30.0)
        self._stop_requested = False
        self.set_preview_enabled(False)

    def requestInterruption(self) -> None:
        self._stop_requested = True

    def isInterruptionRequested(self) -> bool:
        return self._stop_requested


class FakeCapture:
    def __init__(
        self,
        worker: SynchronousCaptureThread,
        *,
        first_frame_ok: bool = True,
        fourcc_set_ok: bool = True,
        reported_fourcc: str = "MJPG",
        reported_fps: float = 30.0,
    ):
        self.worker = worker
        self.first_frame_ok = first_frame_ok
        self.fourcc_set_ok = fourcc_set_ok
        self.frame = np.zeros((24, 32, 3), dtype=np.uint8)
        self.properties = {
            camera.cv2.CAP_PROP_FRAME_WIDTH: 640.0,
            camera.cv2.CAP_PROP_FRAME_HEIGHT: 480.0,
            camera.cv2.CAP_PROP_FPS: reported_fps,
            camera.cv2.CAP_PROP_FOURCC: camera.cv2.VideoWriter_fourcc(*reported_fourcc),
        }
        self.set_calls: list[tuple[int, float]] = []
        self.read_count = 0
        self.release_count = 0

    def isOpened(self) -> bool:
        return True

    def set(self, property_id: int, value: float) -> bool:
        self.set_calls.append((property_id, value))
        if property_id == camera.cv2.CAP_PROP_FOURCC:
            return self.fourcc_set_ok
        return True

    def get(self, property_id: int) -> float:
        return self.properties[property_id]

    def read(self):
        self.read_count += 1
        if self.read_count == 1 and not self.first_frame_ok:
            return False, None
        if self.read_count > 1:
            self.worker.requestInterruption()
        return True, self.frame

    def release(self) -> None:
        self.release_count += 1


def make_worker(name: str = "Logitech HD Pro Webcam C920") -> SynchronousCaptureThread:
    return SynchronousCaptureThread(
        camera.CameraDevice(7, name, "fake-camera-path", camera.cv2.CAP_DSHOW)
    )


def install_captures(monkeypatch, *captures: FakeCapture) -> list[tuple[int, int]]:
    open_calls: list[tuple[int, int]] = []

    def open_capture(index: int, backend: int) -> FakeCapture:
        open_calls.append((index, backend))
        assert len(open_calls) <= len(captures), "unexpected capture reopen"
        return captures[len(open_calls) - 1]

    monkeypatch.setattr(camera.cv2, "VideoCapture", open_capture)
    return open_calls


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Logitech HD Pro Webcam C920", True),
        ("logitech hd pro webcam c920", True),
        ("USB LOGITECH C920s CAMERA", True),
        ("Logitech C922", False),
        ("Integrated Camera", False),
    ],
)
def test_mjpeg_preference_is_limited_to_c920_names(name: str, expected: bool) -> None:
    device = camera.CameraDevice(7, name, "fake-camera-path", camera.cv2.CAP_DSHOW)
    assert camera.prefers_mjpeg(device) is expected


def test_c920_requests_mjpeg_before_resolution_and_fps(monkeypatch) -> None:
    worker = make_worker()
    capture = FakeCapture(worker)
    open_calls = install_captures(monkeypatch, capture)

    worker.run()

    assert not worker.open_error
    assert worker.ready.is_set()
    assert open_calls == [(worker.device.index, worker.device.backend)]
    assert capture.set_calls == [
        (camera.cv2.CAP_PROP_FOURCC, camera.cv2.VideoWriter_fourcc(*"MJPG")),
        (camera.cv2.CAP_PROP_FRAME_WIDTH, 1920),
        (camera.cv2.CAP_PROP_FRAME_HEIGHT, 1080),
        (camera.cv2.CAP_PROP_FPS, 30.0),
    ]
    assert capture.release_count == 1
    configuration = worker.capture_configuration
    assert configuration["preferred_fourcc"] == "MJPG"
    assert configuration["requested_width"] == 1920
    assert configuration["requested_height"] == 1080
    assert configuration["requested_fps"] == 30.0
    assert configuration["attempts"] == [
        {
            "requested_fourcc": "MJPG",
            "fourcc_set_ok": True,
            "width_set_ok": True,
            "height_set_ok": True,
            "fps_set_ok": True,
            "first_frame_ok": True,
            "fallback_reason": None,
        }
    ]


def test_other_camera_preserves_default_input_format(monkeypatch) -> None:
    worker = make_worker("Integrated Camera")
    capture = FakeCapture(worker, reported_fourcc="YUY2")
    install_captures(monkeypatch, capture)

    worker.run()

    assert not worker.open_error
    assert capture.set_calls == [
        (camera.cv2.CAP_PROP_FRAME_WIDTH, 1920),
        (camera.cv2.CAP_PROP_FRAME_HEIGHT, 1080),
        (camera.cv2.CAP_PROP_FPS, 30.0),
    ]
    configuration = worker.capture_configuration
    assert configuration["preferred_fourcc"] is None
    assert configuration["fallback_reason"] is None
    assert configuration["attempts"][0]["requested_fourcc"] is None
    assert configuration["attempts"][0]["fourcc_set_ok"] is None
    assert configuration["reported_fourcc"] == "YUY2"


def test_declined_mjpeg_keeps_default_format_without_reopening(monkeypatch) -> None:
    worker = make_worker()
    capture = FakeCapture(worker, fourcc_set_ok=False, reported_fourcc="YUY2")
    open_calls = install_captures(monkeypatch, capture)

    worker.run()

    assert not worker.open_error
    assert len(open_calls) == 1
    assert capture.release_count == 1
    info = worker.get_capture_info()
    assert info["preferred_fourcc"] == "MJPG"
    assert info["reported_fourcc"] == "YUY2"
    assert "declined" in info["fallback_reason"]
    assert len(info["attempts"]) == 1
    assert info["attempts"][0]["fourcc_set_ok"] is False
    assert info["attempts"][0]["first_frame_ok"] is True
    assert "declined" in info["attempts"][0]["fallback_reason"]


def test_c920_without_mjpeg_frame_reopens_same_device_with_default_format(monkeypatch) -> None:
    worker = make_worker()
    mjpeg_capture = FakeCapture(worker, first_frame_ok=False)
    default_capture = FakeCapture(worker, reported_fourcc="YUY2")
    open_calls = install_captures(monkeypatch, mjpeg_capture, default_capture)

    worker.run()

    assert not worker.open_error
    assert open_calls == [(worker.device.index, worker.device.backend)] * 2
    assert mjpeg_capture.release_count == 1
    assert default_capture.release_count == 1
    assert mjpeg_capture.set_calls[0] == (
        camera.cv2.CAP_PROP_FOURCC,
        camera.cv2.VideoWriter_fourcc(*"MJPG"),
    )
    assert default_capture.set_calls == [
        (camera.cv2.CAP_PROP_FRAME_WIDTH, 1920),
        (camera.cv2.CAP_PROP_FRAME_HEIGHT, 1080),
        (camera.cv2.CAP_PROP_FPS, 30.0),
    ]
    info = worker.get_capture_info()
    assert info["reported_fourcc"] == "YUY2"
    assert "no frame" in info["fallback_reason"]
    assert len(info["attempts"]) == 2
    assert info["attempts"][0]["requested_fourcc"] == "MJPG"
    assert info["attempts"][0]["fourcc_set_ok"] is True
    assert info["attempts"][0]["first_frame_ok"] is False
    assert "no frame" in info["attempts"][0]["fallback_reason"]
    assert info["attempts"][1]["requested_fourcc"] is None
    assert info["attempts"][1]["fourcc_set_ok"] is None
    assert info["attempts"][1]["first_frame_ok"] is True


@pytest.mark.parametrize("name, fourcc_set_ok", [("Integrated Camera", True), ("C920", False)])
def test_default_format_without_frame_is_not_reopened(monkeypatch, name, fourcc_set_ok) -> None:
    worker = make_worker(name)
    capture = FakeCapture(worker, first_frame_ok=False, fourcc_set_ok=fourcc_set_ok)
    open_calls = install_captures(monkeypatch, capture)

    worker.run()

    assert worker.open_error
    assert worker.ready.is_set()
    assert len(open_calls) == 1
    assert capture.release_count == 1
    assert len(worker.get_capture_info()["attempts"]) == 1


def test_actual_dimensions_use_first_frame_not_backend_getters(monkeypatch) -> None:
    worker = make_worker()
    capture = FakeCapture(worker, reported_fps=29.97)
    install_captures(monkeypatch, capture)

    worker.run()

    assert not worker.open_error
    assert (worker.width, worker.height) == (32, 24)
    info = worker.get_capture_info()
    assert info["actual_firstframe_dims"] == {"width": 32, "height": 24}
    assert info["reported_width"] == 640.0
    assert info["reported_height"] == 480.0
    assert info["reported_fps"] == 29.97
    assert info["reported_fourcc_code"] == camera.cv2.VideoWriter_fourcc(*"MJPG")
    assert info["reported_fourcc"] == "MJPG"


def test_measured_fps_uses_frame_arrivals_and_a_120_frame_window(monkeypatch) -> None:
    worker = make_worker()
    capture = FakeCapture(worker, reported_fps=30.0)
    install_captures(monkeypatch, capture)
    monkeypatch.setattr(camera, "monotonic", lambda: 100.0)
    worker.run()
    first_snapshot = worker.get_capture_info()

    for index in range(1, 121):
        worker._process_frame(capture.frame, captured_at=100.0 + index * 0.2)

    info = worker.get_capture_info()
    assert info["reported_fps"] == 30.0
    assert info["measured_capture_fps"] == pytest.approx(5.0)
    assert info["observed_frames"] == 120
    assert info["measured_window_seconds"] == pytest.approx(119 * 0.2)
    assert first_snapshot["observed_frames"] == 1
    assert first_snapshot["measured_capture_fps"] is None
    assert first_snapshot["measured_window_seconds"] == 0.0


def test_capture_info_is_a_detached_snapshot_and_controller_exposes_it(monkeypatch) -> None:
    controller = camera.CameraController()
    assert controller.capture_info == {}
    worker = make_worker()
    capture = FakeCapture(worker)
    install_captures(monkeypatch, capture)
    worker.run()
    controller.worker = worker

    info = controller.capture_info
    assert info == worker.get_capture_info()
    info["actual_firstframe_dims"]["width"] = 999
    info["attempts"][0]["fourcc_set_ok"] = False

    assert worker.capture_configuration["actual_firstframe_dims"]["width"] == 32
    assert worker.capture_configuration["attempts"][0]["fourcc_set_ok"] is True
    assert controller.capture_info["actual_firstframe_dims"]["width"] == 32


def test_failed_capture_diagnostics_survive_controller_close(monkeypatch) -> None:
    worker = make_worker()
    capture = FakeCapture(worker, first_frame_ok=False, fourcc_set_ok=False)
    install_captures(monkeypatch, capture)
    worker.run()
    controller = camera.CameraController()
    controller.worker = worker
    controller.close()

    info = controller.capture_info
    assert controller.worker is None
    assert info["error"] == worker.open_error
    assert len(info["attempts"]) == 1
    assert info["attempts"][0]["first_frame_ok"] is False
    assert "declined" in info["fallback_reason"]
    info["attempts"].clear()
    assert len(controller.capture_info["attempts"]) == 1
