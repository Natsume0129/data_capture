from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def neutral_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from PySide6.QtCore import QObject, QSettings, Signal

    from capture_app import ui
    from capture_app.models import (
        ExperimentSettings,
        SamplingPlan,
        Scene,
        Segment,
        TTSSettings,
    )

    def unexpected_side_effect(*_args, **_kwargs) -> None:
        pytest.fail("Neutral-rest UI tests must not use real camera, TTS, or FFmpeg")

    class FakeCamera(QObject):
        error = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.is_active = False
            self.is_recording = False
            self.duration_ms = 10_000
            self.started_paths: list[Path] = []
            self.stopped_paths: list[Path] = []
            self.current_path: Path | None = None

        @staticmethod
        def devices():
            return []

        def open(self, *_args, **_kwargs) -> None:
            unexpected_side_effect()

        def set_preview(self, _preview) -> None:
            pass

        def start_recording(self, requested_path: Path) -> Path:
            assert not self.is_recording
            path = Path(requested_path)
            assert path.is_relative_to(tmp_path)
            path.write_bytes(b"fake recording; not a playable video")
            self.started_paths.append(path)
            self.current_path = path
            self.is_recording = True
            return path

        def timestamp_ms(self) -> int:
            return self.duration_ms

        def stop_recording(self) -> tuple[Path, int]:
            assert self.is_recording and self.current_path is not None
            path = self.current_path
            self.stopped_paths.append(path)
            self.current_path = None
            self.is_recording = False
            return path, self.duration_ms

        def close(self) -> None:
            self.is_active = False
            self.is_recording = False
            self.current_path = None

    class FakeElapsedClock:
        def __init__(self):
            self.elapsed_ms = 0

        def start(self) -> None:
            self.elapsed_ms = 0

        def elapsed(self) -> int:
            return self.elapsed_ms

    class FakeSplitManager:
        def __init__(self, *_args):
            self.queued = []
            self.shutdown_calls: list[bool] = []

        def enqueue(self, task) -> None:
            self.queued.append(task)

        def shutdown(self, wait: bool = False) -> None:
            self.shutdown_calls.append(wait)

    settings_file = tmp_path / "settings.ini"
    monkeypatch.setattr(
        ui,
        "QSettings",
        lambda *_args: QSettings(str(settings_file), QSettings.Format.IniFormat),
    )
    monkeypatch.setattr(ui, "CameraController", FakeCamera)
    monkeypatch.setattr(ui, "QElapsedTimer", FakeElapsedClock)
    monkeypatch.setattr(ui, "SplitManager", FakeSplitManager)
    monkeypatch.setattr(
        ui,
        "LocalWavPlayer",
        lambda: SimpleNamespace(stop=lambda: None, play=unexpected_side_effect),
    )
    monkeypatch.setattr(ui, "ensure_ffmpeg", unexpected_side_effect)
    monkeypatch.setattr(ui, "TTSGenerationThread", unexpected_side_effect)
    critical_errors: list[str] = []
    monkeypatch.setattr(
        ui.QMessageBox,
        "critical",
        lambda _parent, _title, message: critical_errors.append(str(message)),
    )
    monkeypatch.setattr(ui.QMessageBox, "information", unexpected_side_effect)

    def scene(scene_id: str, ordinal: int) -> Scene:
        return Scene(
            "test-set",
            "Test set",
            scene_id,
            ordinal,
            f"Scene {scene_id}",
            (Segment("test-set", scene_id, "S001", 1, "Stimulus", "smile"),),
        )

    scenes = (scene("formal-1", 1), scene("formal-2", 2))
    practice_scene = scene("practice", 0)
    settings = ExperimentSettings(
        participant_id="neutral-ui-test",
        save_root=tmp_path / "data",
        target_segments=2,
        random_seed=42,
        split_mode="immediate",
        ffmpeg_path=tmp_path / "unused-ffmpeg.exe",
        ui_language="zh_CN",
        camera_device_id=b"",
        camera_description="",
        requested_width=1920,
        requested_height=1080,
        tts=TTSSettings(False, None, "cmn-CN", "", 1.0),
        practice_enabled=False,
        neutral_rest_seconds=10,
    )
    # An empty project root avoids loading unrelated real stimulus files.
    app, window = ui.create_application(tmp_path)
    harness = SimpleNamespace(
        app=app,
        window=window,
        camera=window.camera,
        scenes=scenes,
        settings=settings,
        settings_file=settings_file,
        critical_errors=critical_errors,
    )

    def begin(
        split_mode: str = "immediate", *, practice_enabled: bool = False
    ) -> None:
        harness.settings = replace(
            settings, split_mode=split_mode, practice_enabled=practice_enabled
        )
        window.settings = harness.settings
        window.plan = SamplingPlan(scenes, 2, 2, 42, False)
        window.practice_scene = practice_scene if practice_enabled else None
        window._begin_session()
        assert not critical_errors
        assert window.session_paths is not None
        assert not window.isVisible()

    harness.begin = begin
    try:
        yield harness
    finally:
        window._cleanup_runtime(wait_for_splitter=True)
        window.stack.setCurrentWidget(window.setup)
        window.close()
        app.processEvents()


def _complete_current_scene(harness) -> None:
    window = harness.window
    assert window.flow_state == "summary"
    window._advance()
    assert window.flow_state == "segment"
    window._advance()


def _events(log_path: Path) -> list[dict]:
    return [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize("split_mode", ["immediate", "deferred"])
def test_neutral_rest_records_trimmed_clip_and_auto_advances(
    neutral_session, split_mode: str
) -> None:
    harness = neutral_session
    harness.begin(split_mode)
    window = harness.window
    camera = harness.camera
    paths = window.session_paths
    assert window.flow_state == "summary"
    assert window.current_scene is harness.scenes[0]
    assert camera.started_paths == []
    assert not window.neutral_rest_timer.isActive()

    _complete_current_scene(harness)
    assert window.flow_state == "neutral_rest"
    assert window.scene_index == 0
    assert window.captured_segments == 1
    assert window.neutral_clips == 0
    assert window.neutral_rest_timer.isActive()
    assert camera.is_recording
    assert len(camera.started_paths) == 2
    formal_raw, neutral_raw = camera.started_paths
    assert neutral_raw != formal_raw
    assert neutral_raw.parent == paths.raw
    assert neutral_raw.name == "neutral_after_001_test-set_formal-1.mp4"

    # Emit the same signal as the Space shortcut without showing a window.
    advances: list[bool] = []
    window.experiment.advance_clicked.connect(lambda: advances.append(True))
    assert not window.experiment.advance_button.isEnabled()
    window.experiment.advance_shortcut.activated.emit()
    window.experiment.advance_button.click()
    window._advance()
    assert advances == []
    assert window.flow_state == "neutral_rest"
    assert window.scene_index == 0
    assert camera.stopped_paths == [formal_raw]

    window.neutral_rest_clock.elapsed_ms = 9501
    window.neutral_rest_timer.timeout.emit()
    assert window.flow_state == "neutral_rest"
    assert window.experiment.stage_label.text() == window.translator.text(
        "neutral_rest_countdown", seconds=1
    )
    window.neutral_rest_clock.elapsed_ms = 10_000
    window.neutral_rest_timer.timeout.emit()

    assert window.flow_state == "summary"
    assert window.scene_index == 1
    assert window.current_scene is harness.scenes[1]
    assert window.segment_index == -1
    assert window.experiment.advance_button.isEnabled()
    assert not window.neutral_rest_timer.isActive()
    assert not camera.is_recording
    assert camera.stopped_paths == [formal_raw, neutral_raw]
    assert window.neutral_scene is None
    assert window.neutral_raw_path is None
    assert window.neutral_clips == 1
    assert window.captured_segments == 1
    assert window.settings.target_segments == 2
    assert (window.plan.requested_segments, window.plan.actual_segments) == (2, 2)

    rows = window.manifest.rows()
    assert len(rows) == 2
    formal_row, neutral_row = rows
    assert neutral_row["scene_id"] == "neutral_after_formal-1"
    assert neutral_row["segment_id"] == "N001"
    assert neutral_row["stimulus_format"] == "neutral"
    assert neutral_row["purpose"] == "neutral"
    assert neutral_row["image_path"] == ""
    assert neutral_row["start_ms"] == 3000
    assert neutral_row["end_ms"] == 10_000
    assert neutral_row["duration_ms"] == 7000
    assert paths.root / neutral_row["raw_video_path"] == neutral_raw
    assert neutral_row["raw_video_path"] != formal_row["raw_video_path"]
    assert neutral_row["clip_video_path"] != formal_row["clip_video_path"]
    assert window.split_total == 2
    if split_mode == "immediate":
        tasks = window.split_manager.queued
        assert window.deferred_tasks == []
    else:
        tasks = window.deferred_tasks
        assert window.split_manager.queued == []
    assert len(tasks) == 2
    neutral_task = tasks[1]
    assert neutral_task.clip_key == "test-set|neutral_after_formal-1|N001"
    assert neutral_task.raw_path == neutral_raw
    assert neutral_task.output_path == paths.root / neutral_row["clip_video_path"]
    assert (neutral_task.start_ms, neutral_task.end_ms, neutral_task.duration_ms) == (
        3000,
        10_000,
        7000,
    )

    events = _events(paths.logs / "events.jsonl")
    rest_completed = next(
        event for event in events if event["event"] == "neutral_rest_completed"
    )
    assert rest_completed["label"] == "neutral"
    assert rest_completed["duration_ms"] == 7000
    assert events[-1]["event"] == "scene_summary_shown"
    assert events[-1]["scene_id"] == "formal-2"
    session = json.loads(paths.session_json.read_text(encoding="utf-8"))
    assert session["neutral_rest"] == {
        "duration_seconds": 10,
        "discard_start_ms": 3000,
        "planned_clips": 1,
    }
    assert session["sampling"]["requested_segments"] == 2
    assert session["sampling"]["actual_segments"] == 2

    # The last formal scene ends recording without starting a trailing rest.
    _complete_current_scene(harness)
    assert window.finishing
    assert window.captured_segments == 2
    assert window.neutral_clips == 1
    assert window.split_total == 3
    assert len(camera.started_paths) == 3
    assert not camera.is_recording
    assert not window.neutral_rest_timer.isActive()
    assert window.deferred_tasks == []
    assert len(window.split_manager.queued) == 3
    assert not harness.critical_errors


def test_practice_to_first_formal_scene_has_no_neutral_rest(neutral_session) -> None:
    harness = neutral_session
    harness.begin(practice_enabled=True)
    window = harness.window
    assert window.flow_state == "instructions"
    assert harness.camera.started_paths == []
    window._advance()
    assert window.is_practice
    _complete_current_scene(harness)

    assert window.flow_state == "summary"
    assert not window.is_practice
    assert window.current_scene is harness.scenes[0]
    assert window.scene_index == 0
    assert window.captured_segments == 0
    assert window.neutral_clips == 0
    assert window.manifest.rows() == []
    assert window.split_total == 0
    assert len(harness.camera.started_paths) == 1
    assert harness.camera.started_paths[0].parent == window.session_paths.practice
    assert not harness.camera.is_recording
    assert not window.neutral_rest_timer.isActive()
    assert not harness.critical_errors


@pytest.mark.parametrize("duration_ms", [0, 2999, 3000])
def test_short_neutral_recording_errors_without_adding_a_clip(
    neutral_session, duration_ms: int
) -> None:
    harness = neutral_session
    harness.begin()
    window = harness.window
    _complete_current_scene(harness)
    paths = window.session_paths
    manifest = window.manifest
    splitter = window.split_manager
    formal_rows = manifest.rows()
    assert len(formal_rows) == 1
    assert len(splitter.queued) == 1
    harness.camera.duration_ms = duration_ms
    window.neutral_rest_clock.elapsed_ms = 10_000
    window.neutral_rest_timer.timeout.emit()

    assert len(harness.critical_errors) == 1
    assert window.translator.text(
        "neutral_recording_short", duration=duration_ms
    ) in harness.critical_errors[0]
    assert manifest.rows() == formal_rows
    assert len(splitter.queued) == 1
    assert splitter.shutdown_calls == [False]
    assert not harness.camera.is_recording
    assert not window.neutral_rest_timer.isActive()
    assert window.flow_state == "idle"
    assert window.stack.currentWidget() is window.setup
    events = _events(paths.logs / "events.jsonl")
    assert any(event["event"] == "neutral_rest_failed" for event in events)
    assert not any(event["event"] == "neutral_rest_completed" for event in events)
    session = json.loads(paths.session_json.read_text(encoding="utf-8"))
    assert session["status"] == "aborted"


def test_runtime_reset_stops_neutral_rest_timer(neutral_session) -> None:
    window = neutral_session.window
    window.flow_state = "neutral_rest"
    window.neutral_clips = 1
    window.neutral_rest_timer.start()
    assert window.neutral_rest_timer.isActive()

    window._reset_runtime()

    assert not window.neutral_rest_timer.isActive()
    assert window.flow_state == "idle"
    assert window.neutral_scene is None
    assert window.neutral_raw_path is None
    assert window.neutral_clips == 0


def test_neutral_rest_setting_bounds_default_and_persistence(neutral_session) -> None:
    from PySide6.QtCore import QSettings

    harness = neutral_session
    window = harness.window
    spin = window.setup.neutral_rest_spin
    assert (spin.minimum(), spin.maximum(), spin.value()) == (4, 300, 10)
    spin.setValue(3)
    assert spin.value() == 4
    spin.setValue(301)
    assert spin.value() == 300

    window._save_setup_settings(replace(harness.settings, neutral_rest_seconds=17))
    persisted = QSettings(str(harness.settings_file), QSettings.Format.IniFormat)
    assert persisted.value("neutral_rest_seconds", type=int) == 17
    spin.setValue(25)
    window.qt_settings = persisted
    window._restore_setup_settings()
    assert spin.value() == 17
