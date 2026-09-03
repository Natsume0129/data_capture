from __future__ import annotations

import csv
from pathlib import Path

import pytest

from capture_app.question_sets import QuestionSetError, load_question_set


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["情景", "问题", "目的", "配图"]
        )
        writer.writeheader()
        writer.writerows(rows)


def test_load_groups_rows_and_preserves_text(tmp_path: Path) -> None:
    image = tmp_path / "scene.png"
    image.write_bytes(b"not-decoded-in-parser")
    csv_path = tmp_path / "set.csv"
    rows = [
        {"情景": "场景原文", "问题": "片段1原文", "目的": "愉悦型笑｜说明", "配图": "scene.png"},
        {"情景": "场景原文", "问题": "片段2原文", "目的": "社交型笑｜说明", "配图": ""},
        {"情景": "另一个场景", "问题": "唯一片段", "目的": "苦涩型笑｜说明", "配图": ""},
    ]
    write_csv(csv_path, rows)

    result = load_question_set(csv_path)

    assert len(result.scenes) == 2
    assert result.segment_count == 3
    assert result.scenes[0].text == "场景原文"
    assert [item.text for item in result.scenes[0].segments] == [
        "片段1原文",
        "片段2原文",
    ]
    assert result.scenes[0].image_path == image.resolve()
    assert result.scenes[0].segments[0].category == "愉悦型笑"


def test_rejects_conflicting_images_in_one_scene(tmp_path: Path) -> None:
    for name in ("a.png", "b.png"):
        (tmp_path / name).write_bytes(b"x")
    csv_path = tmp_path / "set.csv"
    write_csv(
        csv_path,
        [
            {"情景": "场景", "问题": "一", "目的": "类别｜说明", "配图": "a.png"},
            {"情景": "场景", "问题": "二", "目的": "类别｜说明", "配图": "b.png"},
        ],
    )
    with pytest.raises(QuestionSetError, match="只能对应一张配图"):
        load_question_set(csv_path)


def test_project_question_set_shape() -> None:
    csv_path = (
        Path(__file__).resolve().parents[1]
        / "question_set"
        / "笑容情景模拟实验_刺激问题库_v2_真实细化.csv"
    )
    result = load_question_set(csv_path)
    assert len(result.scenes) == 26
    assert result.segment_count == 172
    assert [item.ordinal for item in result.scenes[0].segments] == list(
        range(1, 8)
    )

