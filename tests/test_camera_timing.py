from pathlib import Path

import av
import numpy as np
import pytest

from capture_app import camera


def make_worker() -> camera.CaptureThread:
    worker = camera.CaptureThread(camera.CameraDevice(0, "fake", "", 0), 32, 24, 30.0)
    worker.width, worker.height = 32, 24
    worker.set_preview_enabled(False)
    return worker


def test_slow_capture_timestamps_match_real_video_time(tmp_path: Path, monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(camera, "monotonic", lambda: clock[0])
    worker = make_worker()
    frame = np.full((24, 32, 3), 20, dtype=np.uint8)
    worker._process_frame(frame, captured_at=clock[0])
    path = tmp_path / "slow-camera.mp4"
    worker.start_recording(path)
    for index in range(1, 50):
        clock[0] = 100.0 + index * 0.2
        worker._process_frame(frame, captured_at=clock[0])
    clock[0] = 110.0
    assert worker.recording_timestamp_ms() == 10_000
    assert worker.stop_recording() == 10_000
    assert not worker.is_recording
    with av.open(str(path)) as video:
        assert video.start_time == 0
        assert video.duration == 10_000_000
        frames = list(video.decode(video=0))
        assert len(frames) == 50
        assert [round(frame.pts * frame.time_base * 1000) for frame in frames] == list(
            range(0, 10_000, 200)
        )


def test_frame_pts_use_acquisition_time_before_processing_delay(tmp_path: Path, monkeypatch) -> None:
    clock = [10.0]
    monkeypatch.setattr(camera, "monotonic", lambda: clock[0])
    worker = make_worker()
    frame = np.full((24, 32, 3), 80, dtype=np.uint8)
    worker._process_frame(frame, captured_at=10.0)
    path = tmp_path / "acquisition-time.mp4"
    worker.start_recording(path)
    clock[0] = 11.4
    worker._process_frame(frame, captured_at=11.0)
    assert worker.recording_timestamp_ms() == 1400
    clock[0] = 11.6
    assert worker.stop_recording() == 1600
    with av.open(str(path)) as video:
        assert [round(frame.pts * frame.time_base * 1000) for frame in video.decode(video=0)] == [0, 1000]


def test_recording_rejects_a_stale_preview_before_creating_output(tmp_path: Path, monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(camera, "monotonic", lambda: clock[0])
    worker = make_worker()
    worker._process_frame(np.zeros((24, 32, 3), dtype=np.uint8), captured_at=0.0)
    clock[0] = 3.0
    path = tmp_path / "stale.mp4"
    with pytest.raises(RuntimeError, match="超过2秒"):
        worker.start_recording(path)
    assert not path.exists()
    assert not worker.is_recording
