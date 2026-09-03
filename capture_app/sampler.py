from __future__ import annotations

import random
from collections import Counter

from .models import QuestionSet, SamplingPlan, Scene


def _category_score(
    scene: Scene,
    current: Counter[str],
    categories: tuple[str, ...],
) -> tuple[int, int]:
    projected = current.copy()
    projected.update(segment.category for segment in scene.segments)
    counts = [projected[category] for category in categories]
    spread = max(counts) - min(counts) if counts else 0
    squared_error = sum((count * len(counts) - sum(counts)) ** 2 for count in counts)
    return spread, squared_error


def build_sampling_plan(
    question_sets: list[QuestionSet],
    target_segments: int,
    seed: int,
) -> SamplingPlan:
    if target_segments <= 0:
        raise ValueError("target_segments must be positive")
    if not question_sets:
        raise ValueError("at least one question set is required")

    rng = random.Random(seed)
    remaining: dict[str, list[Scene]] = {
        item.question_set_id: list(item.scenes) for item in question_sets
    }
    for scenes in remaining.values():
        rng.shuffle(scenes)

    all_categories = tuple(
        sorted(
            {
                segment.category
                for item in question_sets
                for scene in item.scenes
                for segment in scene.segments
            }
        )
    )
    set_segment_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    selected: list[Scene] = []
    total_segments = 0

    while total_segments < target_segments:
        available_sets = [key for key, scenes in remaining.items() if scenes]
        if not available_sets:
            break

        minimum_set_count = min(set_segment_counts[key] for key in available_sets)
        least_used_sets = [
            key
            for key in available_sets
            if set_segment_counts[key] == minimum_set_count
        ]
        chosen_set = rng.choice(least_used_sets)
        candidates = remaining[chosen_set]
        rng.shuffle(candidates)
        chosen_scene = min(
            candidates,
            key=lambda scene: _category_score(
                scene, category_counts, all_categories
            ),
        )
        candidates.remove(chosen_scene)
        selected.append(chosen_scene)

        count = len(chosen_scene.segments)
        total_segments += count
        set_segment_counts[chosen_set] += count
        category_counts.update(
            segment.category for segment in chosen_scene.segments
        )

    available_total = sum(
        item.segment_count for item in question_sets
    )
    return SamplingPlan(
        scenes=tuple(selected),
        requested_segments=target_segments,
        actual_segments=total_segments,
        seed=seed,
        exhausted=total_segments < target_segments and total_segments == available_total,
    )


def build_sampling_plan_with_practice(
    question_sets: list[QuestionSet],
    target_segments: int,
    seed: int,
) -> tuple[Scene, SamplingPlan]:
    if not question_sets:
        raise ValueError("at least one question set is required")
    largest_scene = max(
        len(scene.segments)
        for question_set in question_sets
        for scene in question_set.scenes
    )
    expanded = build_sampling_plan(
        question_sets,
        target_segments=target_segments + largest_scene,
        seed=seed,
    )
    practice_scene = expanded.scenes[0]
    formal_scenes: list[Scene] = []
    formal_segments = 0
    for scene in expanded.scenes[1:]:
        if formal_segments >= target_segments:
            break
        formal_scenes.append(scene)
        formal_segments += len(scene.segments)
    return practice_scene, SamplingPlan(
        scenes=tuple(formal_scenes),
        requested_segments=target_segments,
        actual_segments=formal_segments,
        seed=seed,
        exhausted=formal_segments < target_segments,
    )
