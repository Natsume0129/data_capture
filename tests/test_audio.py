from __future__ import annotations

import winsound
from pathlib import Path

from capture_app.audio import LocalWavPlayer


def test_local_wav_player_uses_windows_async_playback(tmp_path: Path) -> None:
    wav = tmp_path / "complete.wav"
    wav.write_bytes(b"RIFF-test")
    calls: list[tuple[str | None, int]] = []
    player = LocalWavPlayer(lambda sound, flags: calls.append((sound, flags)))

    player.play(wav)
    assert calls[-1][0] == str(wav.resolve())
    assert calls[-1][1] & winsound.SND_ASYNC
    assert calls[-1][1] & winsound.SND_FILENAME

    player.stop()
    assert calls[-1] == (None, 0)
    assert player.current_path is None
