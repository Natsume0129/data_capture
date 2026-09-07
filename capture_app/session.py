from __future__ import annotations

import csv
import json
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Scene


MANIFEST_FIELDS = (
    "participant_id",
    "session_id",
    "question_set_id",
    "question_set_name",
    "scene_id",
    "scene_order",
    "segment_id",
    "segment_order",
    "scene_text",
    "segment_text",
    "stimulus_format",
    "instruction_text",
    "utterance_text",
    "purpose",
    "image_path",
    "raw_video_path",
    "clip_video_path",
    "start_ms",
    "end_ms",
    "duration_ms",
    "split_status",
    "split_error",
    "created_at",
)


WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_participant_id(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value.strip())
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        raise ValueError("participant_id is empty")
    if cleaned.upper() in WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@dataclass(frozen=True)
class SessionPaths:
    root: Path
    practice: Path
    raw: Path
    clips: Path
    logs: Path
    manifest: Path
    session_json: Path

    @classmethod
    def create(cls, save_root: Path, participant_id: str) -> "SessionPaths":
        participant = safe_participant_id(participant_id)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = stamp + "_" + uuid.uuid4().hex[:8]
        root = Path(save_root).resolve() / participant / session_id
        practice = root / "practice"
        raw = root / "raw"
        clips = root / "clips"
        logs = root / "logs"
        for directory in (practice, raw, clips, logs):
            directory.mkdir(parents=True, exist_ok=False)
        return cls(
            root=root,
            practice=practice,
            raw=raw,
            clips=clips,
            logs=logs,
            manifest=root / "manifest.csv",
            session_json=root / "session.json",
        )

    @property
    def session_id(self) -> str:
        return self.root.name


class EventLogger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def write(self, event: str, **payload: Any) -> None:
        record = {"event": event, "timestamp_utc": utc_now(), **payload}
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()
            os.fsync(self._handle.fileno())

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()


class ManifestStore:
    def __init__(self, path: Path):
        self.path = path
        self._rows: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            for row in rows:
                key = self._key(row)
                self._rows[key] = {field: row.get(field, "") for field in MANIFEST_FIELDS}
            self._write_locked()

    def update_split(self, clip_key: str, status: str, error: str = "") -> None:
        with self._lock:
            if clip_key not in self._rows:
                raise KeyError(clip_key)
            self._rows[clip_key]["split_status"] = status
            self._rows[clip_key]["split_error"] = error
            self._write_locked()

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._rows.values()]

    @staticmethod
    def _key(row: dict[str, Any]) -> str:
        return (
            str(row["question_set_id"])
            + "|"
            + str(row["scene_id"])
            + "|"
            + str(row["segment_id"])
        )

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(self._rows.values())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)


@dataclass(frozen=True)
class SegmentTiming:
    segment_id: str
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def relative_to_session(path: Path | None, session_root: Path) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(session_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def build_manifest_rows(
    *,
    participant_id: str,
    session_paths: SessionPaths,
    scene: Scene,
    scene_order: int,
    raw_video_path: Path,
    timings: list[SegmentTiming],
) -> list[dict[str, Any]]:
    if len(timings) != len(scene.segments):
        raise ValueError("timings must match scene segments")
    rows: list[dict[str, Any]] = []
    created_at = utc_now()
    for segment, timing in zip(scene.segments, timings, strict=True):
        if timing.segment_id != segment.segment_id:
            raise ValueError("timing order does not match segment order")
        if timing.duration_ms <= 0:
            raise ValueError(f"non-positive duration for {segment.segment_id}")
        clip_name = (
            f"{scene_order:03d}_{scene.question_set_id}_{scene.scene_id}_"
            f"{segment.segment_id}.mp4"
        )
        clip_path = session_paths.clips / clip_name
        rows.append(
            {
                "participant_id": participant_id,
                "session_id": session_paths.session_id,
                "question_set_id": scene.question_set_id,
                "question_set_name": scene.question_set_name,
                "scene_id": scene.scene_id,
                "scene_order": scene_order,
                "segment_id": segment.segment_id,
                "segment_order": segment.ordinal,
                "scene_text": scene.text,
                "segment_text": segment.text,
                "stimulus_format": segment.stimulus_format,
                "instruction_text": segment.instruction,
                "utterance_text": segment.utterance,
                "purpose": segment.purpose,
                "image_path": str(scene.image_path or ""),
                "raw_video_path": relative_to_session(
                    raw_video_path, session_paths.root
                ),
                "clip_video_path": relative_to_session(
                    clip_path, session_paths.root
                ),
                "start_ms": timing.start_ms,
                "end_ms": timing.end_ms,
                "duration_ms": timing.duration_ms,
                "split_status": "pending",
                "split_error": "",
                "created_at": created_at,
            }
        )
    return rows


def settings_to_json(settings: Any) -> dict[str, Any]:
    payload = asdict(settings)
    for key, value in list(payload.items()):
        if isinstance(value, Path):
            payload[key] = str(value)
    if isinstance(payload.get("camera_device_id"), bytes):
        payload["camera_device_id"] = payload["camera_device_id"].hex()
    tts = payload.get("tts")
    if isinstance(tts, dict) and isinstance(tts.get("credentials_path"), Path):
        tts["credentials_path"] = str(tts["credentials_path"])
    return payload
