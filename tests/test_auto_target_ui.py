from __future__ import annotations

import csv
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def write_question_set(path: Path, segment_count: int) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["情景", "问题", "目的", "配图"]
        )
        writer.writeheader()
        writer.writerows(
            {
                "情景": f"{path.stem} 场景",
                "问题": f"问题 {index}",
                "目的": "愉悦型笑｜测试",
                "配图": "",
            }
            for index in range(1, segment_count + 1)
        )
    return path


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def offline_ui(monkeypatch: pytest.MonkeyPatch):
    from capture_app import ui

    def unexpected_side_effect(*_args, **_kwargs) -> None:
        pytest.fail("Auto-target tests must not use camera, TTS, FFmpeg, or dialogs")

    monkeypatch.setattr(ui.CameraController, "devices", staticmethod(lambda: []))
    monkeypatch.setattr(ui.CameraController, "open", unexpected_side_effect)
    monkeypatch.setattr(ui.CameraController, "start_recording", unexpected_side_effect)
    monkeypatch.setattr(ui, "ensure_ffmpeg", unexpected_side_effect)
    monkeypatch.setattr(ui, "TTSGenerationThread", unexpected_side_effect)
    monkeypatch.setattr(ui.QMessageBox, "critical", unexpected_side_effect)
    monkeypatch.setattr(
        ui,
        "LocalWavPlayer",
        lambda: SimpleNamespace(stop=lambda: None, play=unexpected_side_effect),
    )
    return ui


@pytest.fixture
def setup_page(qapp, offline_ui, tmp_path: Path):
    page = offline_ui.SetupPage(offline_ui.Translator("zh_CN"), tmp_path)
    try:
        yield page
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_target_starts_zero_and_read_only(setup_page) -> None:
    assert setup_page.target_spin.minimum() == 0
    assert setup_page.target_spin.maximum() == 1_000_000
    assert setup_page.target_spin.isReadOnly()
    assert setup_page.target_spin.value() == 0
    assert not setup_page.isVisible()
    assert setup_page.practice_checkbox.isChecked()
    assert setup_page.camera_combo.count() == 0
    setup_page.practice_checkbox.setChecked(False)
    assert setup_page.target_spin.value() == 0


def test_checked_addition_updates_target(setup_page, tmp_path: Path) -> None:
    from PySide6.QtCore import Qt

    path = write_question_set(tmp_path / "checked.csv", 3)
    setup_page._load_one_question_set(path, checked=True)

    assert setup_page.sets_list.item(0).checkState() == Qt.CheckState.Checked
    assert setup_page.selected_question_sets()[0].segment_count == 3
    assert setup_page.target_spin.value() == 2


def test_check_state_changes_update_target(setup_page, tmp_path: Path) -> None:
    from PySide6.QtCore import Qt

    path = write_question_set(tmp_path / "toggle.csv", 3)
    setup_page._load_one_question_set(path, checked=False)
    item = setup_page.sets_list.item(0)

    assert setup_page.target_spin.value() == 0
    item.setCheckState(Qt.CheckState.Checked)
    assert setup_page.target_spin.value() == 2
    item.setCheckState(Qt.CheckState.Unchecked)
    assert setup_page.selected_question_sets() == []
    assert setup_page.target_spin.value() == 0


def test_multiple_checked_sets_are_summed(setup_page, tmp_path: Path) -> None:
    from PySide6.QtCore import Qt

    for name, count, checked in (
        ("first", 3, True),
        ("second", 4, True),
        ("unchecked", 5, False),
    ):
        setup_page._load_one_question_set(
            write_question_set(tmp_path / f"{name}.csv", count), checked=checked
        )

    assert [item.segment_count for item in setup_page.selected_question_sets()] == [3, 4]
    assert setup_page.target_spin.value() == 6
    setup_page.sets_list.item(2).setCheckState(Qt.CheckState.Checked)
    assert setup_page.target_spin.value() == 11
    setup_page.sets_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert setup_page.target_spin.value() == 7


def test_practice_toggle_updates_target(setup_page, tmp_path: Path) -> None:
    setup_page._load_one_question_set(
        write_question_set(tmp_path / "practice.csv", 3), checked=True
    )

    assert setup_page.target_spin.value() == 2
    setup_page.practice_checkbox.setChecked(False)
    assert setup_page.target_spin.value() == 3
    setup_page.practice_checkbox.setChecked(True)
    assert setup_page.target_spin.value() == 2


def test_removing_selected_sets_updates_target(setup_page, tmp_path: Path) -> None:
    first = write_question_set(tmp_path / "first.csv", 3)
    second = write_question_set(tmp_path / "second.csv", 4)
    setup_page._load_one_question_set(first, checked=True)
    setup_page._load_one_question_set(second, checked=True)

    assert setup_page.target_spin.value() == 6
    setup_page.sets_list.setCurrentRow(0)
    setup_page._remove_selected_sets()
    assert setup_page.target_spin.value() == 3
    assert str(first.resolve()) not in setup_page.question_sets_by_path
    assert setup_page.sets_list.count() == 1
    setup_page.sets_list.setCurrentRow(0)
    setup_page._remove_selected_sets()
    assert setup_page.target_spin.value() == 0
    assert setup_page.question_sets_by_path == {}


def test_single_segment_clamps_practice_target_to_zero(
    setup_page, tmp_path: Path
) -> None:
    setup_page._load_one_question_set(
        write_question_set(tmp_path / "single.csv", 1), checked=True
    )

    assert setup_page.target_spin.value() == 0
    setup_page.practice_checkbox.setChecked(False)
    assert setup_page.target_spin.value() == 1
    setup_page.practice_checkbox.setChecked(True)
    assert setup_page.target_spin.value() == 0


def test_default_question_sets_are_unchecked(setup_page, tmp_path: Path) -> None:
    from PySide6.QtCore import Qt

    question_dir = tmp_path / "question_set"
    question_dir.mkdir()
    write_question_set(question_dir / "default.csv", 3)
    setup_page._load_default_question_sets()

    assert setup_page.sets_list.count() == 1
    assert setup_page.sets_list.item(0).checkState() == Qt.CheckState.Unchecked
    assert setup_page.selected_question_sets() == []
    assert setup_page.target_spin.value() == 0


def test_saved_target_does_not_override_derived_target(
    qapp, offline_ui, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtCore import QSettings

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("target_segments", 999)
    settings.setValue("practice_enabled", False)
    settings.sync()
    monkeypatch.setattr(offline_ui, "QSettings", lambda *_args: settings)

    window = offline_ui.MainWindow(tmp_path)
    try:
        assert not window.isVisible()
        assert window.setup.target_spin.value() == 0
        assert not window.setup.practice_checkbox.isChecked()
        window.setup._load_one_question_set(
            write_question_set(tmp_path / "restored.csv", 3), checked=True
        )
        assert window.setup.target_spin.value() == 3
        window._restore_setup_settings()
        assert window.setup.target_spin.value() == 3
        assert settings.value("target_segments", type=int) == 999
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
