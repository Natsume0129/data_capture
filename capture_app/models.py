from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Segment:
    question_set_id: str
    scene_id: str
    segment_id: str
    ordinal: int
    text: str
    purpose: str

    @property
    def category(self) -> str:
        return self.purpose.split("｜", 1)[0].strip()


@dataclass(frozen=True)
class Scene:
    question_set_id: str
    question_set_name: str
    scene_id: str
    ordinal: int
    text: str
    segments: tuple[Segment, ...]
    image_path: Path | None = None


@dataclass(frozen=True)
class QuestionSet:
    question_set_id: str
    name: str
    path: Path
    scenes: tuple[Scene, ...]

    @property
    def segment_count(self) -> int:
        return sum(len(scene.segments) for scene in self.scenes)


@dataclass(frozen=True)
class SamplingPlan:
    scenes: tuple[Scene, ...]
    requested_segments: int
    actual_segments: int
    seed: int
    exhausted: bool


@dataclass(frozen=True)
class TTSSettings:
    enabled: bool
    credentials_path: Path | None
    language_code: str
    voice_name: str
    speaking_rate: float


@dataclass(frozen=True)
class ExperimentSettings:
    participant_id: str
    save_root: Path
    target_segments: int
    random_seed: int
    split_mode: str
    ffmpeg_path: Path
    ui_language: str
    camera_device_id: bytes
    camera_description: str
    requested_width: int
    requested_height: int
    tts: TTSSettings

