from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from capture_app.splitter import (
    SplitTask,
    build_ffmpeg_command,
    ensure_ffmpeg,
    run_split,
)


def test_build_command_reencodes_without_audio(tmp_path: Path) -> None:
    task = SplitTask(
        "key",
        tmp_path / "raw.mp4",
        tmp_path / "clip.mp4",
        500,
        1750,
    )
    command = build_ffmpeg_command(Path("ffmpeg.exe"), task)
    assert "-an" in command
    assert "libx264" in command
    assert command[command.index("-fps_mode") + 1] == "vfr"
    assert command[command.index("-ss") + 1] == "0.500"
    assert command[command.index("-t") + 1] == "1.250"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_real_ffmpeg_split(tmp_path: Path) -> None:
    ffmpeg = ensure_ffmpeg(shutil.which("ffmpeg") or "")
    raw = tmp_path / "raw.mp4"
    subprocess.run(
        [
            str(ffmpeg),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=30:d=2",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(raw),
        ],
        check=True,
    )
    output = tmp_path / "clip.mp4"
    run_split(ffmpeg, SplitTask("key", raw, output, 500, 1500))
    assert output.is_file()
    assert output.stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_vfr_neutral_split_preserves_real_timing_and_frames(tmp_path: Path) -> None:
    import av
    import numpy as np

    from capture_app.video_recorder import TimestampedVideoWriter

    raw = tmp_path / "slow-camera.mp4"
    writer = TimestampedVideoWriter(raw, 32, 24, 30.0, 100.0)
    for index in range(50):
        writer.write_frame(np.full((24, 32, 3), index * 4, dtype=np.uint8), 100 + index * 0.2)
    writer.finish(110.0)
    clip = tmp_path / "neutral.mp4"
    ffmpeg = ensure_ffmpeg(shutil.which("ffmpeg") or "")
    run_split(ffmpeg, SplitTask("neutral", raw, clip, 3000, 10_000))
    with av.open(str(clip)) as video:
        assert video.duration / 1_000_000 == pytest.approx(7.0, abs=0.1)
        frames = list(video.decode(video=0))
        assert len(frames) == 35
        assert frames[0].to_ndarray(format="bgr24").mean() == pytest.approx(60, abs=3)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_success_with_no_decodable_frames_is_rejected(tmp_path: Path) -> None:
    import numpy as np

    from capture_app.video_recorder import TimestampedVideoWriter

    raw = tmp_path / "short.mp4"
    writer = TimestampedVideoWriter(raw, 32, 24, 30.0, 0.0)
    for index in range(3):
        writer.write_frame(np.full((24, 32, 3), 80, dtype=np.uint8), index * 0.1)
    writer.finish(0.3)
    clip = tmp_path / "no-frames.mp4"
    ffmpeg = ensure_ffmpeg(shutil.which("ffmpeg") or "")
    with pytest.raises(RuntimeError, match="Video clip validation failed"):
        run_split(ffmpeg, SplitTask("empty", raw, clip, 3000, 7000))
    assert not clip.exists()
