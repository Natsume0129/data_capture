from __future__ import annotations

from .models import Scene, Segment
from .session import SegmentTiming


NEUTRAL_TRIM_MS = 3000


def make_neutral_scene(after_scene: Scene, scene_order: int) -> Scene:
    scene_id = "neutral_after_" + after_scene.scene_id
    segment = Segment(
        question_set_id=after_scene.question_set_id,
        scene_id=scene_id,
        segment_id="N001",
        ordinal=1,
        text="Neutral expression during rest",
        purpose="neutral",
        neutral=True,
    )
    return Scene(
        question_set_id=after_scene.question_set_id,
        question_set_name=after_scene.question_set_name,
        scene_id=scene_id,
        ordinal=scene_order,
        text="Neutral expression during rest after scene " + after_scene.scene_id,
        segments=(segment,),
        image_path=None,
    )


def neutral_timing(duration_ms: int) -> SegmentTiming:
    if duration_ms <= NEUTRAL_TRIM_MS:
        raise ValueError("No valid neutral segment remains after trimming the first 3000 ms")
    return SegmentTiming("N001", NEUTRAL_TRIM_MS, duration_ms)
