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

