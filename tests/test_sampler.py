from __future__ import annotations

from pathlib import Path

from capture_app.models import QuestionSet, Scene, Segment
from capture_app.sampler import build_sampling_plan


def make_set(name: str, scene_sizes: list[int]) -> QuestionSet:
    question_set_id = "qs_" + name
    scenes = []
    categories = ("愉悦型笑", "苦涩型笑", "社交型笑")
    for scene_index, size in enumerate(scene_sizes, start=1):
        scene_id = f"S{scene_index:03d}"
        segments = tuple(
            Segment(
                question_set_id=question_set_id,
                scene_id=scene_id,
                segment_id=f"P{index:03d}",
                ordinal=index,
                text=f"{name}-{scene_id}-{index}",
                purpose=categories[(scene_index + index) % len(categories)] + "｜说明",
            )
            for index in range(1, size + 1)
        )
        scenes.append(
            Scene(
                question_set_id=question_set_id,
                question_set_name=name,
                scene_id=scene_id,
                ordinal=scene_index,
                text=f"scene-{name}-{scene_index}",
                segments=segments,
            )
        )
    return QuestionSet(question_set_id, name, Path(name + ".csv"), tuple(scenes))


def test_sampling_keeps_scenes_complete_and_never_repeats() -> None:
    sets = [make_set("a", [3, 3, 3]), make_set("b", [3, 3, 3])]
    plan = build_sampling_plan(sets, target_segments=5, seed=42)
    assert plan.actual_segments == 6
    assert len(plan.scenes) == 2
    assert len({(s.question_set_id, s.scene_id) for s in plan.scenes}) == 2
    assert {scene.question_set_id for scene in plan.scenes} == {"qs_a", "qs_b"}


def test_sampling_is_reproducible() -> None:
    sets = [make_set("a", [2, 4, 3]), make_set("b", [3, 2, 4])]
    first = build_sampling_plan(sets, target_segments=10, seed=7)
    second = build_sampling_plan(sets, target_segments=10, seed=7)
    assert [
        (scene.question_set_id, scene.scene_id) for scene in first.scenes
    ] == [
        (scene.question_set_id, scene.scene_id) for scene in second.scenes
    ]


def test_sampling_stops_when_sets_are_exhausted() -> None:
    sets = [make_set("a", [2]), make_set("b", [3])]
    plan = build_sampling_plan(sets, target_segments=100, seed=1)
    assert plan.actual_segments == 5
    assert plan.exhausted is True

