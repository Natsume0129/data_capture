from __future__ import annotations

import winsound
from pathlib import Path
from typing import Callable


class LocalWavPlayer:
    """Play complete local WAV files through the Windows audio API."""

    def __init__(
        self,
        play_sound: Callable[[str | None, int], None] = winsound.PlaySound,
    ):
        self._play_sound = play_sound
        self.current_path: Path | None = None

    def play(self, path: Path) -> None:
        wav_path = Path(path).resolve()
        if not wav_path.is_file():
            raise FileNotFoundError(wav_path)
        self.stop()
        flags = (
            winsound.SND_FILENAME
            | winsound.SND_ASYNC
            | winsound.SND_NODEFAULT
        )
        self._play_sound(str(wav_path), flags)
        self.current_path = wav_path

    def stop(self) -> None:
        if self.current_path is not None:
            self._play_sound(None, 0)
            self.current_path = None

