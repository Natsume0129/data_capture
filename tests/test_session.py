from __future__ import annotations

import csv
import json
from pathlib import Path

from capture_app.models import Scene, Segment
from capture_app.session import (
    EventLogger,
    ManifestStore,
    SegmentTiming,
    SessionPaths,
    build_manifest_rows,
    safe_participant_id,
)


def make_scene() -> Scene:
    segments = tuple(
        Segment(
            "qs_test",
            "S001",
            f"P{index:03d}",
            index,
            f"片段{index}",
            "愉悦型笑｜说明",
        )
        for index in (1, 2)
    )
    return Scene("qs_test", "测试集", "S001", 1, "场景", segments)


def test_participant_id_is_safe_for_windows() -> None:
    assert safe_participant_id(" P/001 ") == "P_001"
    assert safe_participant_id("CON") == "_CON"


def test_manifest_and_event_log_are_written_incrementally(tmp_path: Path) -> None:
    paths = SessionPaths.create(tmp_path, "P001")
    raw = paths.raw / "scene.mp4"
    raw.write_bytes(b"raw")
    rows = build_manifest_rows(
        participant_id="P001",
        session_paths=paths,
        scene=make_scene(),
        scene_order=1,
        raw_video_path=raw,
        timings=[
            SegmentTiming("P001", 0, 1200),
            SegmentTiming("P002", 1200, 2500),
        ],
    )
    store = ManifestStore(paths.manifest)
    store.add_rows(rows)
    key = "qs_test|S001|P001"
    store.update_split(key, "complete")

    with paths.manifest.open(encoding="utf-8-sig", newline="") as handle:
        parsed = list(csv.DictReader(handle))
    assert len(parsed) == 2
    assert parsed[0]["duration_ms"] == "1200"
    assert parsed[0]["split_status"] == "complete"
    assert parsed[0]["raw_video_path"] == "raw/scene.mp4"

    logger = EventLogger(paths.logs / "events.jsonl")
    logger.write("segment_started", segment_id="P001")
    logger.close()
    event = json.loads((paths.logs / "events.jsonl").read_text(encoding="utf-8"))
    assert event["event"] == "segment_started"
    assert event["segment_id"] == "P001"

