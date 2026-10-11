import json
from types import SimpleNamespace

from capture_app.ui import MainWindow


def test_stopping_preview_logs_measured_fps_before_closing_camera(tmp_path) -> None:
    actions = []
    camera = SimpleNamespace(
        capture_info={
            "device_name": "HD Pro Webcam C920",
            "reported_fps": 30.0,
            "measured_capture_fps": 29.8,
            "reported_fourcc": "MJPG",
            "actual_firstframe_dims": {"width": 1920, "height": 1080},
        },
        close=lambda: actions.append("closed"),
    )
    owner = SimpleNamespace(
        project_root=tmp_path,
        camera=camera,
        setup=SimpleNamespace(set_camera_status=lambda *args: actions.append(args)),
    )
    owner._log_camera_capture_config = lambda stage: MainWindow._log_camera_capture_config(owner, stage)
    MainWindow._stop_preview(owner)

    record = json.loads((tmp_path / "logs/camera_capture.jsonl").read_text(encoding="utf-8"))
    assert record["event"] == "camera_capture_config"
    assert record["stage"] == "preview_stopped"
    assert record["measured_capture_fps"] == 29.8
    assert record["reported_fps"] == 30.0
    assert record["reported_fourcc"] == "MJPG"
    assert actions == ["closed", (None, None)]


def test_unwritable_diagnostic_folder_does_not_interrupt_capture(tmp_path, caplog) -> None:
    (tmp_path / "logs").write_text("A file blocks this diagnostic directory", encoding="utf-8")
    owner = SimpleNamespace(project_root=tmp_path, camera=SimpleNamespace(capture_info={"device_name": "C920"}))

    MainWindow._log_camera_capture_config(owner, "preview")

    assert "Unable to write camera capture diagnostics" in caplog.text
