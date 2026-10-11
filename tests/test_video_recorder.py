from __future__ import annotations

import builtins
import runpy
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from capture_app.video_recorder import TimestampedVideoWriter


def _frame(value: int = 0) -> np.ndarray:
    return np.full((24, 32, 3), value, dtype=np.uint8)


def _inspect(path: Path) -> tuple[list[int], list[int], int]:
    with av.open(str(path)) as container:
        pts = [
            round(frame.pts * frame.time_base * 1000)
            for frame in container.decode(video=0)
        ]
        duration_us = container.duration
    with av.open(str(path)) as container:
        durations = [
            round(packet.duration * packet.time_base * 1000)
            for packet in container.demux(video=0)
            if packet.pts is not None
        ]
    return pts, durations, duration_us


def test_slow_camera_keeps_actual_frames_and_ten_second_duration(tmp_path: Path) -> None:
    path = tmp_path / "slow.mp4"
    writer = TimestampedVideoWriter(path, 32, 24, 30.0, started_at=100.0)
    for index in range(50):
        writer.write_frame(_frame(index * 4), 100.0 + index * 0.2)

    assert writer.frame_count == 50
    assert writer.timestamp_ms(110.0) == 10_000
    assert writer.finish(110.0) == 10_000
    pts, durations, duration_us = _inspect(path)
    assert pts == list(range(0, 10_000, 200))
    assert durations == [200] * 50
    assert duration_us == 10_000_000


def test_irregular_frames_and_last_packet_keep_wall_clock_timing(tmp_path: Path) -> None:
    path = tmp_path / "irregular.mp4"
    writer = TimestampedVideoWriter(path, 32, 24, 30.0, started_at=10.0)
    actual_pts = [0, 37, 128, 129, 406, 940]
    for index, pts in enumerate(actual_pts):
        writer.write_frame(_frame(index * 30), 10.0 + pts / 1000)

    assert writer.finish(11.234) == 1234
    pts, durations, duration_us = _inspect(path)
    assert pts == actual_pts
    assert durations == [37, 91, 1, 277, 534, 294]
    assert duration_us == 1_234_000
    with av.open(str(path)) as container:
        assert container.streams.video[0].time_base == Fraction(1, 1000)
        assert container.streams.video[0].codec_context.has_b_frames is False


def test_cached_frame_and_dropped_frames_share_the_start_origin(tmp_path: Path) -> None:
    path = tmp_path / "cached.mp4"
    writer = TimestampedVideoWriter(path, 32, 24, 30.0, started_at=100.0)
    writer.write_frame(_frame(), 99.9, initial=True)
    writer.write_frame(_frame(), 99.95)  # Acquired before the recording began.
    writer.write_frame(_frame(), 100.0004)  # Rounds to the cached frame's PTS.
    writer.write_frame(_frame(40), 100.08)
    writer.write_frame(_frame(80), 100.0803)  # Rounds to the previous real PTS.
    writer.write_frame(_frame(120), 100.21)

    assert writer.timestamp_ms(100.1234) == 123
    assert writer.frame_count == 3
    assert writer.finish(100.37) == 370
    assert _inspect(path) == ([0, 80, 210], [80, 130, 160], 370_000)


def test_delayed_first_frame_is_not_shifted_to_zero(tmp_path: Path) -> None:
    path = tmp_path / "delayed.mp4"
    writer = TimestampedVideoWriter(path, 32, 24, 30.0, started_at=100.0)
    writer.write_frame(_frame(), 100.45)

    assert writer.finish(101.0) == 1000
    # Without a cached seed, the media starts late. Its span excludes the empty
    # initial interval, but both the first frame and the end retain wall timing.
    assert _inspect(path) == ([450], [550], 550_000)
    with av.open(str(path)) as container:
        assert container.start_time == 450_000
        assert container.start_time + container.duration == 1_000_000


def test_empty_recording_fails_and_closes(tmp_path: Path) -> None:
    writer = TimestampedVideoWriter(tmp_path / "empty.mp4", 32, 24, 30, 100.0)
    with pytest.raises(RuntimeError, match="no camera frames"):
        writer.finish(108.0)
    writer.abort()
    with pytest.raises(RuntimeError, match="already closed"):
        writer.write_frame(_frame(), 108.0)


def test_stale_source_fails_without_padding_and_preserves_partial_file(tmp_path: Path) -> None:
    path = tmp_path / "stale.mp4"
    writer = TimestampedVideoWriter(path, 32, 24, 30, 100.0)
    writer.write_frame(_frame(), 100.0)
    with pytest.raises(RuntimeError, match="stopped arriving"):
        writer.finish(108.0)

    pts, durations, duration_us = _inspect(path)
    assert pts == [0]
    assert durations == [33]
    assert duration_us == 33_000
    writer.abort()


def test_abort_preserves_acquired_frames_without_synthetic_end_frame(tmp_path: Path) -> None:
    path = tmp_path / "aborted.mp4"
    writer = TimestampedVideoWriter(path, 32, 24, 30, 100.0)
    writer.write_frame(_frame(), 100.0)
    writer.write_frame(_frame(50), 100.12)
    writer.abort()
    writer.abort()

    assert _inspect(path) == ([0, 120], [120, 120], 240_000)


def test_invalid_frame_closes_recording_and_preserves_partial_file(tmp_path: Path) -> None:
    path = tmp_path / "invalid.mp4"
    writer = TimestampedVideoWriter(path, 32, 24, 30, 100.0)
    writer.write_frame(_frame(), 100.0)
    with pytest.raises(ValueError, match="dimensions"):
        writer.write_frame(np.zeros((12, 16, 3), dtype=np.uint8), 100.1)

    assert _inspect(path)[0] == [0]
    with pytest.raises(RuntimeError, match="already closed"):
        writer.finish(100.2)


def test_missing_pyav_is_reported_only_when_creating_recorder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_import = builtins.__import__

    def missing_av(name, *args, **kwargs):
        if name == "av":
            raise ModuleNotFoundError("No module named 'av'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_av)
    namespace = runpy.run_path(
        str(Path(__file__).parents[1] / "capture_app" / "video_recorder.py")
    )
    with pytest.raises(RuntimeError, match="requires PyAV.*requirements.txt"):
        namespace["TimestampedVideoWriter"](
            tmp_path / "missing.mp4", 32, 24, 30, 100.0
        )
