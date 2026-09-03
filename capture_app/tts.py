from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class TTSConfig:
    credentials_path: Path
    language_code: str
    voice_name: str = ""
    speaking_rate: float = 1.0


class TTSCache:
    def __init__(self, cache_root: Path, config: TTSConfig):
        self.cache_root = cache_root.resolve()
        self.config = config
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def key_for(self, text: str) -> str:
        payload = json.dumps(
            {
                "text": text,
                "language_code": self.config.language_code,
                "voice_name": self.config.voice_name,
                "speaking_rate": self.config.speaking_rate,
                "encoding": "LINEAR16",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def path_for(self, text: str) -> Path:
        return self.cache_root / (self.key_for(text) + ".wav")

    def missing_texts(self, texts: Iterable[str]) -> list[str]:
        unique = dict.fromkeys(text.strip() for text in texts if text.strip())
        return [text for text in unique if not self.path_for(text).is_file()]

    def generate_missing(
        self,
        texts: Iterable[str],
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Path]:
        credentials = self.config.credentials_path.resolve()
        if not credentials.is_file():
            raise FileNotFoundError(f"Google Cloud credentials not found: {credentials}")
        if not self.config.language_code.strip():
            raise ValueError("TTS language code is empty")
        if not 0.25 <= self.config.speaking_rate <= 4.0:
            raise ValueError("TTS speaking rate must be between 0.25 and 4.0")

        all_texts = list(dict.fromkeys(text.strip() for text in texts if text.strip()))
        missing = self.missing_texts(all_texts)
        if missing:
            from google.cloud import texttospeech

            client = texttospeech.TextToSpeechClient.from_service_account_file(
                str(credentials)
            )
            total = len(missing)
            for index, text in enumerate(missing, start=1):
                voice_arguments = {"language_code": self.config.language_code}
                if self.config.voice_name.strip():
                    voice_arguments["name"] = self.config.voice_name.strip()
                response = client.synthesize_speech(
                    input=texttospeech.SynthesisInput(text=text),
                    voice=texttospeech.VoiceSelectionParams(**voice_arguments),
                    audio_config=texttospeech.AudioConfig(
                        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                        speaking_rate=self.config.speaking_rate,
                    ),
                )
                path = self.path_for(text)
                temporary = path.with_suffix(".wav.tmp")
                with temporary.open("wb") as handle:
                    handle.write(response.audio_content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                if progress:
                    progress(index, total, text)
        return {text: self.path_for(text) for text in all_texts}

