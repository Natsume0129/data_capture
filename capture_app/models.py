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
    instruction: str = ""
    utterance: str = ""
    image_caption: bool = False

    @property
    def category(self) -> str:
        return self.purpose.split("｜", 1)[0].strip()

    @property
    def is_dialogue(self) -> bool:
        return bool(self.instruction and self.utterance)

    @property
    def stimulus_format(self) -> str:
        if self.image_caption:
            return "image_caption"
        return "instruction_utterance" if self.is_dialogue else "legacy"

    @property
    def tts_texts(self) -> tuple[str, ...]:
        if self.is_dialogue:
            return (self.instruction, self.utterance)
        return (self.text,)


@dataclass(frozen=True)
class Scene:
    question_set_id: str
    question_set_name: str
    scene_id: str
    ordinal: int
    text: str
    segments: tuple[Segment, ...]
    image_path: Path | None = None
    image_prompt: str = ""
    scenario_description: str = ""


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
