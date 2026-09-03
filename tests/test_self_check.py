from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.mark.skipif(
    not Path("C:/ffmpeg/bin/ffmpeg.exe").is_file(),
    reason="development FFmpeg unavailable",
)
def test_source_self_check(tmp_path: Path) -> None:
    from app import run_self_check

    project_root = Path(__file__).resolve().parents[1]
    output = tmp_path / "self_check.json"
    assert run_self_check(project_root, output) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["segments"] == 172
    assert report["google_tts_client"] == "TextToSpeechClient"
