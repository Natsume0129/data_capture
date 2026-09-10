from __future__ import annotations

import csv
import hashlib
import io
from collections import OrderedDict
from pathlib import Path

from .models import QuestionSet, Scene, Segment


BASE_COLUMNS = ("情景", "目的")
LEGACY_COLUMN = "问题"
DIALOGUE_COLUMNS = ("片段序号", "说明", "发言")
COMPACT_SCENE_COLUMN = "場面"
COMPACT_UTTERANCE_COLUMN = "セリフ"
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
    has_legacy = LEGACY_COLUMN in reader.fieldnames
    has_dialogue = all(name in reader.fieldnames for name in DIALOGUE_COLUMNS)
    has_compact = all(
        name in reader.fieldnames
        for name in (COMPACT_SCENE_COLUMN, COMPACT_UTTERANCE_COLUMN)
    )
    if sum((has_legacy, has_dialogue, has_compact)) != 1:
        raise QuestionSetError(
            "问题集必须使用“情景、问题、目的”旧格式、“情景、片段序号、说明、发言、目的”说明发言格式，或“場面、セリフ”紧凑格式"
        )
    if not has_compact:
        missing = [name for name in BASE_COLUMNS if name not in reader.fieldnames]
        if missing:
            raise QuestionSetError("问题集缺少列：" + "、".join(missing))

    grouped: OrderedDict[
        str, list[tuple[int, int | None, str, str, str, str]]
    ] = OrderedDict()
    for row_number, row in enumerate(reader, start=2):
        scene_text = (
            row.get(COMPACT_SCENE_COLUMN)
            if has_compact
            else row.get("情景")
        ) or ""
        scene_text = scene_text.strip()
        purpose = "" if has_compact else (row.get("目的") or "").strip()
        image_value = (row.get(IMAGE_COLUMN) or "").strip()
        if has_dialogue:
            instruction = (row.get("说明") or "").strip()
            utterance = (row.get("发言") or "").strip()
            ordinal_text = (row.get("片段序号") or "").strip()
            try:
                declared_ordinal = int(ordinal_text)
            except ValueError as exc:
                raise QuestionSetError(
                    f"第 {row_number} 行的片段序号必须是正整数"
                ) from exc
            segment_text = utterance
            if (
                not scene_text
                or not instruction
                or not utterance
                or not purpose
                or declared_ordinal <= 0
            ):
                raise QuestionSetError(
                    f"第 {row_number} 行的情景、片段序号、说明、发言或目的为空或无效"
                )
        elif has_legacy:
            instruction = ""
            utterance = ""
            declared_ordinal = None
            segment_text = (row.get(LEGACY_COLUMN) or "").strip()
            if not scene_text or not segment_text or not purpose:
                raise QuestionSetError(
                    f"第 {row_number} 行的情景、问题或目的为空"
                )
        else:
            instruction = ""
            utterance = ""
            declared_ordinal = None
            segment_text = (row.get(COMPACT_UTTERANCE_COLUMN) or "").strip()
            if not scene_text or not segment_text:
                raise QuestionSetError(
                    f"第 {row_number} 行的場面或セリフ为空"
                )
        grouped.setdefault(scene_text, []).append(
            (
                row_number,
                declared_ordinal,
                segment_text,
                purpose,
                image_value,
                instruction,
            )
        )

    if not grouped:
        raise QuestionSetError(f"问题集没有数据：{csv_path.name}")

    question_set_id = "qs_" + hashlib.sha256(raw).hexdigest()[:12]
    scenes: list[Scene] = []
    for scene_ordinal, (scene_text, entries) in enumerate(grouped.items(), start=1):
        scene_hash = hashlib.sha1(scene_text.encode("utf-8")).hexdigest()[:8]
        scene_id = f"S{scene_ordinal:03d}_{scene_hash}"
        image_paths = {
            _resolve_image(csv_path, entry[4])
            for entry in entries
            if entry[4]
        }
        if len(image_paths) > 1:
            rows = ", ".join(str(entry[0]) for entry in entries if entry[4])
            raise QuestionSetError(
                f"同一情景只能对应一张配图；冲突行：{rows}"
            )
        image_path = next(iter(image_paths), None)
        if has_dialogue:
            declared = [entry[1] for entry in entries]
            expected = list(range(1, len(entries) + 1))
            if declared != expected:
                rows = ", ".join(str(entry[0]) for entry in entries)
                raise QuestionSetError(
                    f"同一情景的片段序号必须从 1 开始连续递增；相关行：{rows}"
                )
        segments = tuple(
            Segment(
                question_set_id=question_set_id,
                scene_id=scene_id,
                segment_id=f"P{segment_ordinal:03d}",
                ordinal=segment_ordinal,
                text=segment_text,
                purpose=purpose,
                instruction=instruction,
                utterance=segment_text if has_dialogue else "",
            )
            for segment_ordinal, (
                _,
                _,
                segment_text,
                purpose,
                _,
                instruction,
            ) in enumerate(
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
