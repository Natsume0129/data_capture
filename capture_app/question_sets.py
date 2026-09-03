from __future__ import annotations

import csv
import hashlib
import io
from collections import OrderedDict
from pathlib import Path

from .models import QuestionSet, Scene, Segment


REQUIRED_COLUMNS = ("情景", "问题", "目的")
IMAGE_COLUMN = "配图"


class QuestionSetError(ValueError):
    pass


def _resolve_image(csv_path: Path, value: str) -> Path:
    image = Path(value.strip())
    if not image.is_absolute():
        image = csv_path.parent / image
    image = image.resolve()
    if not image.is_file():
        raise QuestionSetError(f"配图不存在：{image}")
    return image


def load_question_set(path: str | Path) -> QuestionSet:
    csv_path = Path(path).resolve()
    if not csv_path.is_file():
        raise QuestionSetError(f"问题集不存在：{csv_path}")

    raw = csv_path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise QuestionSetError(f"问题集必须使用 UTF-8 编码：{csv_path.name}") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise QuestionSetError(f"问题集没有表头：{csv_path.name}")
    reader.fieldnames = [name.strip() for name in reader.fieldnames]
    missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
    if missing:
        raise QuestionSetError("问题集缺少列：" + "、".join(missing))

    grouped: OrderedDict[str, list[tuple[int, str, str, str]]] = OrderedDict()
    for row_number, row in enumerate(reader, start=2):
        scene_text = (row.get("情景") or "").strip()
        segment_text = (row.get("问题") or "").strip()
        purpose = (row.get("目的") or "").strip()
        image_value = (row.get(IMAGE_COLUMN) or "").strip()
        if not scene_text or not segment_text or not purpose:
            raise QuestionSetError(f"第 {row_number} 行的情景、问题或目的为空")
        grouped.setdefault(scene_text, []).append(
            (row_number, segment_text, purpose, image_value)
        )

    if not grouped:
        raise QuestionSetError(f"问题集没有数据：{csv_path.name}")

    question_set_id = "qs_" + hashlib.sha256(raw).hexdigest()[:12]
    scenes: list[Scene] = []
    for scene_ordinal, (scene_text, entries) in enumerate(grouped.items(), start=1):
        scene_hash = hashlib.sha1(scene_text.encode("utf-8")).hexdigest()[:8]
        scene_id = f"S{scene_ordinal:03d}_{scene_hash}"
        image_paths = {
            _resolve_image(csv_path, entry[3])
            for entry in entries
            if entry[3]
        }
        if len(image_paths) > 1:
            rows = ", ".join(str(entry[0]) for entry in entries if entry[3])
            raise QuestionSetError(
                f"同一情景只能对应一张配图；冲突行：{rows}"
            )
        image_path = next(iter(image_paths), None)
        segments = tuple(
            Segment(
                question_set_id=question_set_id,
                scene_id=scene_id,
                segment_id=f"P{segment_ordinal:03d}",
                ordinal=segment_ordinal,
                text=segment_text,
                purpose=purpose,
            )
            for segment_ordinal, (_, segment_text, purpose, _) in enumerate(
                entries, start=1
            )
        )
        scenes.append(
            Scene(
                question_set_id=question_set_id,
                question_set_name=csv_path.stem,
                scene_id=scene_id,
                ordinal=scene_ordinal,
                text=scene_text,
                segments=segments,
                image_path=image_path,
            )
        )

    return QuestionSet(
        question_set_id=question_set_id,
        name=csv_path.stem,
        path=csv_path,
        scenes=tuple(scenes),
    )


def load_question_sets(paths: list[str | Path]) -> list[QuestionSet]:
    resolved: set[Path] = set()
    question_sets: list[QuestionSet] = []
    for path in paths:
        csv_path = Path(path).resolve()
        if csv_path in resolved:
            continue
        resolved.add(csv_path)
        question_sets.append(load_question_set(csv_path))
    return question_sets

