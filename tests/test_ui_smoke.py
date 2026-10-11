from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_main_window_can_be_created() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette
    from PySide6.QtTest import QTest

    from capture_app.ui import create_application

    root = Path(__file__).resolve().parents[1]
    app, window = create_application(root)
    assert window.setup.sets_list.count() >= 1
    assert window.windowTitle()
    assert (
        app.palette().color(QPalette.ColorRole.Base).name().lower()
        == "#ffffff"
    )
    assert (
        app.palette().color(QPalette.ColorRole.Text).name().lower()
        == "#0f172a"
    )
    triggered: list[bool] = []
    window.experiment.advance_clicked.connect(lambda: triggered.append(True))
    window.stack.setCurrentWidget(window.experiment)
    window.experiment.show_instructions()
    window.show()
    window.activateWindow()
    window.experiment.setFocus()
    app.processEvents()
    QTest.keyClick(window.experiment, Qt.Key.Key_Space)
    app.processEvents()
    assert triggered == [True]
    assert window.experiment.crosshair.isVisible()
    scene = next(iter(window.setup.question_sets_by_path.values())).scenes[0]
    window.experiment.show_segment(scene, 0, practice=True)
    assert window.experiment.recovery_hint.isVisible()
    window.stack.setCurrentWidget(window.setup)
    window.close()
    app.processEvents()


@pytest.mark.parametrize(
    "practice_enabled", [True, False], ids=["practice", "no-practice"]
)
def test_begin_session_respects_practice_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, practice_enabled: bool
) -> None:
    from PySide6.QtCore import QSettings

    from capture_app import ui
    from capture_app.models import (
        ExperimentSettings,
        SamplingPlan,
        Scene,
        Segment,
        TTSSettings,
    )

    settings_file = tmp_path / "settings.ini"
    monkeypatch.setattr(
        ui,
        "QSettings",
        lambda *_args: QSettings(str(settings_file), QSettings.Format.IniFormat),
    )
    monkeypatch.setattr(ui.CameraController, "devices", staticmethod(lambda: []))
    monkeypatch.setattr(
        ui,
        "SplitManager",
        lambda *_args: SimpleNamespace(shutdown=lambda **_kwargs: None),
    )

    def unexpected_side_effect(*_args, **_kwargs) -> None:
        pytest.fail(
            "Session initialization must not open/record a camera or run TTS/FFmpeg"
        )

    monkeypatch.setattr(ui.CameraController, "open", unexpected_side_effect)
    monkeypatch.setattr(ui.CameraController, "start_recording", unexpected_side_effect)
    monkeypatch.setattr(ui, "ensure_ffmpeg", unexpected_side_effect)
    monkeypatch.setattr(ui, "TTSGenerationThread", unexpected_side_effect)
    monkeypatch.setattr(ui.QMessageBox, "critical", unexpected_side_effect)

    root = Path(__file__).resolve().parents[1]
    app, window = ui.create_application(root)
    try:
        assert window.setup.practice_checkbox.isChecked()
        assert window.setup.camera_combo.count() == 0
        settings = ExperimentSettings(
            participant_id="practice-test",
            save_root=tmp_path / "data",
            target_segments=1,
            random_seed=42,
            split_mode="deferred",
            ffmpeg_path=tmp_path / "unused-ffmpeg.exe",
            ui_language="zh_CN",
            camera_device_id=b"",
            camera_description="",
            requested_width=1920,
            requested_height=1080,
            tts=TTSSettings(False, None, "cmn-CN", "", 1.0),
            practice_enabled=practice_enabled,
        )
        window.setup.practice_checkbox.setChecked(practice_enabled)
        window._save_setup_settings(settings)
        persisted = QSettings(str(settings_file), QSettings.Format.IniFormat)
        assert persisted.value("practice_enabled", type=bool) is practice_enabled
        window.setup.practice_checkbox.setChecked(not practice_enabled)
        window.qt_settings = persisted
        window._restore_setup_settings()
        assert window.setup.practice_checkbox.isChecked() is practice_enabled

        def scene(scene_id: str, ordinal: int) -> Scene:
            return Scene(
                "test-set",
                "Test set",
                scene_id,
                ordinal,
                f"Scene {scene_id}",
                (Segment("test-set", scene_id, "s1", 1, "Stimulus", "Test"),),
            )

        practice_scene = scene("practice", 1)
        formal_scene = scene("formal", 2)
        window.settings = settings
        window.practice_scene = practice_scene if practice_enabled else None
        window.plan = SamplingPlan((formal_scene,), 1, 1, 42, False)
        window._begin_session()

        assert window.stack.currentWidget() is window.experiment
        assert window.flow_state == (
            "instructions" if practice_enabled else "summary"
        )
        assert window.is_practice is practice_enabled
        assert window.current_scene is (
            practice_scene if practice_enabled else formal_scene
        )
        assert (
            window.scene_index, window.segment_index, window.captured_segments
        ) == (0, -1, 0)
        assert window.experiment.stage_label.text() == window.translator.text(
            "experiment_instructions" if practice_enabled else "scene_overview"
        )
        assert not window.camera.is_active
        assert not window.camera.is_recording
        assert window.session_paths is not None
        session = json.loads(
            window.session_paths.session_json.read_text(encoding="utf-8")
        )
        assert session["settings"]["practice_enabled"] is practice_enabled
        assert session["sampling"]["practice_scene_id"] == (
            "test-set:practice" if practice_enabled else None
        )
        assert session["sampling"]["scene_ids"] == ["test-set:formal"]
        stimuli = json.loads(
            (window.session_paths.root / "stimuli.json").read_text(encoding="utf-8")
        )
        assert [item["practice"] for item in stimuli] == (
            [True, False] if practice_enabled else [False]
        )
        assert stimuli[-1]["scene_id"] == "formal"
        events = [
            json.loads(line)
            for line in (window.session_paths.logs / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert events[0]["event"] == "session_started"
        assert events[0]["practice_scenes"] == int(practice_enabled)
        assert events[-1]["event"] == (
            "experiment_instructions_shown"
            if practice_enabled else "scene_summary_shown"
        )
        if not practice_enabled:
            assert events[-1]["scene_id"] == "formal"
            assert events[-1]["practice"] is False
    finally:
        window._cleanup_runtime(wait_for_splitter=True)
        window.stack.setCurrentWidget(window.setup)
        window.close()
        app.processEvents()
