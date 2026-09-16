from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QSettings, Qt, QThread, Signal
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QImageReader,
    QKeySequence,
    QPalette,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .audio import LocalWavPlayer
from .camera import CameraController
from .i18n import LANGUAGES, Translator
from .models import ExperimentSettings, QuestionSet, Scene, TTSSettings
from .question_sets import QuestionSetError, load_question_set
from .sampler import build_sampling_plan_with_practice
from .session import (
    EventLogger,
    ManifestStore,
    SegmentTiming,
    SessionPaths,
    atomic_write_json,
    build_manifest_rows,
    settings_to_json,
    utc_now,
)
from .splitter import SplitManager, SplitTask, ensure_ffmpeg
from .tts import TTSCache, TTSConfig


APP_STYLE = """
QWidget {
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 14px; color: #0f172a;
}
QMainWindow { background: #f4f7fb; }
QScrollArea, QWidget#setupContent { background: #f4f7fb; }
QDialog, QMessageBox { background: #f4f7fb; color: #0f172a; }
QMessageBox QLabel { background: transparent; color: #0f172a; }
QAbstractItemView {
    background: #ffffff; color: #0f172a;
    selection-background-color: #dbeafe;
    selection-color: #0f172a;
    border: 1px solid #94a3b8;
    outline: 0;
}
QComboBox QAbstractItemView::item { min-height: 34px; padding: 4px 10px; }
QToolTip {
    background: #ffffff; color: #0f172a;
    border: 1px solid #94a3b8; padding: 4px;
}
QGroupBox {
    background: white;
    border: 1px solid #d9e2ef;
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget {
    min-height: 34px; border: 1px solid #cbd5e1; border-radius: 6px;
    background: white; padding: 2px 8px;
}
QPushButton {
    min-height: 38px; border: 0; border-radius: 7px;
    padding: 0 16px; background: #e2e8f0; color: #0f172a;
}
QPushButton:hover { background: #cbd5e1; }
QPushButton#primaryButton { background: #1769aa; color: white; font-weight: 600; }
QPushButton#primaryButton:hover { background: #12568d; }
QPushButton#dangerButton { background: #fee2e2; color: #991b1b; }
QLabel#recordingLabel { color: #b91c1c; font-weight: 700; }
QLabel#recoveryHint {
    color: #334155; background: #eef6ff; border: 1px solid #bfdbfe;
    border-radius: 7px; padding: 8px 14px; font-weight: 600;
}
QLabel#pageTitle { font-size: 25px; font-weight: 700; color: #0f2742; }
QTextBrowser {
    background: white; border: 1px solid #d9e2ef; border-radius: 12px;
    padding: 28px; font-size: 22px; line-height: 1.55;
}
"""


TTS_LANGUAGE_BY_UI = {
    "zh_CN": "cmn-CN",
    "en": "en-US",
    "ja": "ja-JP",
}


def default_ffmpeg_path(project_root: Path) -> str:
    bundled = project_root / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("ffmpeg") or str(Path("C:/ffmpeg/bin/ffmpeg.exe"))


class TTSGenerationThread(QThread):
    progress = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, cache: TTSCache, texts: list[str]):
        super().__init__()
        self.cache = cache
        self.texts = texts

    def run(self) -> None:
        try:
            paths = self.cache.generate_missing(
                self.texts,
                lambda done, total, _text: self.progress.emit(done, total),
            )
            self.succeeded.emit(paths)
        except Exception as exc:
            self.failed.emit(str(exc))


class SplitBridge(QWidget):
    result = Signal(object, bool, str)

    def report(self, task: SplitTask, success: bool, error: str) -> None:
        self.result.emit(task, success, error)


class SetupPage(QWidget):
    start_clicked = Signal()
    preview_clicked = Signal()
    stop_preview_clicked = Signal()
    language_changed = Signal(str)

    def __init__(self, translator: Translator, project_root: Path):
        super().__init__()
        self.translator = translator
        self.project_root = project_root
        self.question_sets_by_path: dict[str, QuestionSet] = {}
        self._build()
        self._load_default_question_sets()
        self.retranslate()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        outer.addWidget(self.title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("setupContent")
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QGridLayout(content)
        layout.setContentsMargins(0, 8, 8, 8)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(12)

        general_group = QGroupBox()
        general_form = QFormLayout(general_group)
        self.language_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self.language_combo.addItem(name, code)
        self.language_combo.currentIndexChanged.connect(
            lambda: self.language_changed.emit(self.language_combo.currentData())
        )
        self.participant_edit = QLineEdit()
        self.save_edit = QLineEdit(str(self.project_root / "data"))
        self.save_button = QPushButton()
        self.save_button.clicked.connect(self._choose_save_path)
        save_row = QHBoxLayout()
        save_row.addWidget(self.save_edit, 1)
        save_row.addWidget(self.save_button)
        self.target_spin = QSpinBox()
        self.target_spin.setRange(1, 1_000_000)
        self.target_spin.setValue(100)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 2_147_483_647)
        self.seed_spin.setValue(20260903)
        self.split_combo = QComboBox()
        self.ffmpeg_edit = QLineEdit(
            default_ffmpeg_path(self.project_root)
        )
        self.ffmpeg_button = QPushButton()
        self.ffmpeg_button.clicked.connect(self._choose_ffmpeg)
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        ffmpeg_row.addWidget(self.ffmpeg_button)
        general_form.addRow(self._label("ui_language"), self.language_combo)
        general_form.addRow(self._label("participant_id"), self.participant_edit)
        general_form.addRow(self._label("save_path"), save_row)
        general_form.addRow(self._label("target_segments"), self.target_spin)
        general_form.addRow(self._label("random_seed"), self.seed_spin)
        general_form.addRow(self._label("split_mode"), self.split_combo)
        general_form.addRow(self._label("ffmpeg_path"), ffmpeg_row)
        self.general_group = general_group
        self.general_form = general_form
        layout.addWidget(general_group, 0, 0)

        camera_group = QGroupBox()
        camera_layout = QVBoxLayout(camera_group)
        camera_top = QHBoxLayout()
        self.camera_combo = QComboBox()
        self.refresh_camera_button = QPushButton()
        self.refresh_camera_button.clicked.connect(self.refresh_cameras)
        camera_top.addWidget(self.camera_combo, 1)
        camera_top.addWidget(self.refresh_camera_button)
        camera_layout.addLayout(camera_top)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            "background: #0f172a; border-radius: 8px;"
        )
        self.preview.setMinimumSize(480, 270)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        camera_layout.addWidget(self.preview)
        camera_buttons = QHBoxLayout()
        self.preview_button = QPushButton()
        self.preview_button.clicked.connect(self.preview_clicked)
        self.stop_preview_button = QPushButton()
        self.stop_preview_button.clicked.connect(self.stop_preview_clicked)
        camera_buttons.addWidget(self.preview_button)
        camera_buttons.addWidget(self.stop_preview_button)
        camera_layout.addLayout(camera_buttons)
        self.camera_status = QLabel()
        camera_layout.addWidget(self.camera_status)
        self.camera_group = camera_group
        layout.addWidget(camera_group, 0, 1)

        sets_group = QGroupBox()
        sets_layout = QVBoxLayout(sets_group)
        self.sets_list = QListWidget()
        self.sets_list.setMinimumHeight(180)
        sets_layout.addWidget(self.sets_list)
        set_buttons = QHBoxLayout()
        self.add_set_button = QPushButton()
        self.add_set_button.clicked.connect(self._add_question_sets)
        self.remove_set_button = QPushButton()
        self.remove_set_button.clicked.connect(self._remove_selected_sets)
        set_buttons.addWidget(self.add_set_button)
        set_buttons.addWidget(self.remove_set_button)
        set_buttons.addStretch()
        sets_layout.addLayout(set_buttons)
        self.sets_group = sets_group
        layout.addWidget(sets_group, 1, 0, 1, 2)

        tts_group = QGroupBox()
        tts_group.setCheckable(True)
        tts_group.setChecked(False)
        tts_form = QFormLayout(tts_group)
        self.credentials_edit = QLineEdit()
        self.credentials_button = QPushButton()
        self.credentials_button.clicked.connect(self._choose_credentials)
        credential_row = QHBoxLayout()
        credential_row.addWidget(self.credentials_edit, 1)
        credential_row.addWidget(self.credentials_button)
        self.tts_language_edit = QLineEdit("cmn-CN")
        self.voice_edit = QLineEdit()
        self.rate_spin = QDoubleSpinBox()
        self.rate_spin.setRange(0.25, 4.0)
        self.rate_spin.setSingleStep(0.05)
        self.rate_spin.setValue(1.0)
        tts_form.addRow(self._label("credentials"), credential_row)
        tts_form.addRow(self._label("tts_language"), self.tts_language_edit)
        tts_form.addRow(self._label("voice_name"), self.voice_edit)
        tts_form.addRow(self._label("speaking_rate"), self.rate_spin)
        self.tts_group = tts_group
        self.tts_form = tts_form
        layout.addWidget(tts_group, 2, 0, 1, 2)

        bottom = QHBoxLayout()
        self.status_label = QLabel()
        bottom.addWidget(self.status_label, 1)
        self.start_button = QPushButton()
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_clicked)
        bottom.addWidget(self.start_button)
        outer.addLayout(bottom)
        self.refresh_cameras()

    def _label(self, key: str) -> QLabel:
        label = QLabel()
        label.setProperty("translationKey", key)
        return label

    def retranslate(self) -> None:
        t = self.translator.text
        self.title.setText(t("app_title"))
        self.general_group.setTitle(t("settings"))
        self.camera_group.setTitle(t("camera"))
        self.sets_group.setTitle(t("question_sets"))
        self.tts_group.setTitle(t("tts_settings"))
        for label in self.findChildren(QLabel):
            key = label.property("translationKey")
            if key:
                label.setText(t(key))
        for row in range(self.sets_list.count()):
            item = self.sets_list.item(row)
            question_set = self.question_sets_by_path[
                item.data(Qt.ItemDataRole.UserRole)
            ]
            item.setText(
                t(
                    "question_set_summary",
                    name=question_set.name,
                    scenes=len(question_set.scenes),
                    segments=question_set.segment_count,
                )
            )
        current_split = self.split_combo.currentData()
        self.split_combo.clear()
        self.split_combo.addItem(t("split_immediate"), "immediate")
        self.split_combo.addItem(t("split_deferred"), "deferred")
        index = self.split_combo.findData(current_split)
        if index >= 0:
            self.split_combo.setCurrentIndex(index)
        self.save_button.setText(t("browse"))
        self.ffmpeg_button.setText(t("browse"))
        self.credentials_button.setText(t("browse"))
        self.refresh_camera_button.setText(t("refresh"))
        self.preview_button.setText(t("start_preview"))
        self.stop_preview_button.setText(t("stop_preview"))
        self.add_set_button.setText(t("add_question_set"))
        self.remove_set_button.setText(t("remove_question_set"))
        self.start_button.setText(t("start_experiment"))
        self.status_label.setText(t("status_ready"))
        if not self.camera_combo.count():
            self.camera_status.setText(t("camera_inactive"))

    def _choose_save_path(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, self.translator.text("save_path"), self.save_edit.text()
        )
        if path:
            self.save_edit.setText(path)

    def _choose_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.translator.text("ffmpeg_path"), self.ffmpeg_edit.text(),
            "Executable (*.exe);;All files (*)",
        )
        if path:
            self.ffmpeg_edit.setText(path)

    def _choose_credentials(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.translator.text("credentials"), "", "JSON (*.json)"
        )
        if path:
            self.credentials_edit.setText(path)

    def _load_default_question_sets(self) -> None:
        question_dir = self.project_root / "question_set"
        if question_dir.is_dir():
            for path in sorted([*question_dir.glob("*.csv"), *question_dir.glob("*.json")]):
                self._load_one_question_set(path, checked=False)

    def _add_question_sets(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.translator.text("add_question_set"),
            str(self.project_root / "question_set"),
            "Question sets (*.csv *.json)",
        )
        for path in paths:
            self._load_one_question_set(Path(path), checked=True)

    def _load_one_question_set(self, path: Path, checked: bool) -> None:
        key = str(path.resolve())
        if key in self.question_sets_by_path:
            return
        try:
            question_set = load_question_set(path)
        except QuestionSetError as exc:
            QMessageBox.critical(
                self,
                self.translator.text("question_set_invalid"),
                str(exc),
            )
            return
        self.question_sets_by_path[key] = question_set
        item = QListWidgetItem(
            self.translator.text(
                "question_set_summary",
                name=question_set.name,
                scenes=len(question_set.scenes),
                segments=question_set.segment_count,
            )
        )
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        self.sets_list.addItem(item)

    def _remove_selected_sets(self) -> None:
        for item in list(self.sets_list.selectedItems()):
            key = item.data(Qt.ItemDataRole.UserRole)
            self.question_sets_by_path.pop(key, None)
            self.sets_list.takeItem(self.sets_list.row(item))

    def selected_question_sets(self) -> list[QuestionSet]:
        selected: list[QuestionSet] = []
        for row in range(self.sets_list.count()):
            item = self.sets_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(
                    self.question_sets_by_path[item.data(Qt.ItemDataRole.UserRole)]
                )
        return selected

    def refresh_cameras(self) -> None:
        previous = self.camera_combo.currentData()
        self.camera_combo.clear()
        for device in CameraController.devices():
            self.camera_combo.addItem(
                device.description(), bytes(device.id())
            )
        index = self.camera_combo.findData(previous)
        if index >= 0:
            self.camera_combo.setCurrentIndex(index)

    def set_camera_status(self, width: int | None, height: int | None) -> None:
        if width and height:
            self.camera_status.setText(
                self.translator.text(
                    "camera_active", width=width, height=height
                )
            )
        else:
            self.camera_status.setText(
                self.translator.text("camera_inactive")
            )


class Crosshair(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(42, 42)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#dc2626"))
        pen.setWidth(2)
        painter.setPen(pen)
        center = self.rect().center()
        painter.drawLine(center.x() - 14, center.y(), center.x() + 14, center.y())
        painter.drawLine(center.x(), center.y() - 14, center.x(), center.y() + 14)
        painter.drawEllipse(center, 4, 4)


class ExperimentPage(QWidget):
    advance_clicked = Signal()
    abort_clicked = Signal()

    def __init__(self, translator: Translator):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.translator = translator
        self._image_source: QPixmap | None = None
        self._image_path: Path | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(44, 28, 44, 28)
        header = QHBoxLayout()
        self.stage_label = QLabel()
        self.stage_label.setObjectName("pageTitle")
        self.scene_progress_label = QLabel()
        self.overall_progress_label = QLabel()
        header.addWidget(self.stage_label)
        header.addStretch()
        header.addWidget(self.scene_progress_label)
        header.addSpacing(18)
        header.addWidget(self.overall_progress_label)
        layout.addLayout(header)

        self.recovery_hint = QLabel()
        self.recovery_hint.setObjectName("recoveryHint")
        self.recovery_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recovery_hint.setWordWrap(True)
        self.recovery_hint.hide()
        layout.addWidget(self.recovery_hint)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(220)
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.image_label.installEventFilter(self)
        self.image_label.hide()
        layout.addWidget(self.image_label, 1)

        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(False)
        self.text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.text.setMinimumHeight(150)
        layout.addWidget(self.text, 1)
        layout.setStretchFactor(self.image_label, 5)
        layout.setStretchFactor(self.text, 2)
        self.centered_scene_text = QLabel(self)
        self.centered_scene_text.setTextFormat(Qt.TextFormat.PlainText)
        self.centered_scene_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.centered_scene_text.setWordWrap(True)
        self.centered_scene_text.setStyleSheet(
            "background: white; color: #0f172a; font-size: 22px;"
            "border-radius: 12px; padding: 24px;"
        )
        self.centered_scene_text.hide()

        controls = QHBoxLayout()
        self.recording_label = QLabel()
        self.recording_label.setObjectName("recordingLabel")
        controls.addWidget(self.recording_label)
        self.recording_label.hide()
        controls.addStretch()
        self.abort_button = QPushButton()
        self.abort_button.setObjectName("dangerButton")
        self.abort_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.abort_button.clicked.connect(self.abort_clicked)
        controls.addWidget(self.abort_button)
        self.advance_button = QPushButton()
        self.advance_button.setObjectName("primaryButton")
        self.advance_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.advance_button.clicked.connect(self.advance_clicked)
        controls.addWidget(self.advance_button)
        layout.addLayout(controls)

        self.advance_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Space), self
        )
        self.advance_shortcut.setContext(
            Qt.ShortcutContext.WindowShortcut
        )
        self.advance_shortcut.setAutoRepeat(False)
        self.advance_shortcut.activated.connect(
            lambda: self.advance_clicked.emit()
            if self.advance_button.isEnabled()
            else None
        )
        self.crosshair = Crosshair(self)
        self.crosshair.raise_()
        self._mode = "instructions"
        self._practice = False
        self.retranslate()

    def retranslate(self) -> None:
        self.abort_button.setText(self.translator.text("abort_experiment"))
        self.recovery_hint.setText(
            self.translator.text("recovery_hint")
            + "\n"
            + self.translator.text("space_hint")
        )
        if self._mode == "instructions":
            self.stage_label.setText(
                self.translator.text("experiment_instructions")
            )
            self.advance_button.setText(
                self.translator.text("begin_practice")
            )
        elif self._mode == "summary":
            self.stage_label.setText(
                self.translator.text(
                    "practice_scene" if self._practice else "scene_overview"
                )
            )
            self.advance_button.setText(self.translator.text("start_scene"))
            self.recording_label.setText(
                self.translator.text("not_recording")
            )
        elif self._mode == "segment":
            self.recording_label.setText(self.translator.text("recording"))
        elif self._mode == "dialogue_instruction":
            self.recording_label.setText(self.translator.text("recording_raw"))
            self.recovery_hint.setText(
                self.translator.text("dialogue_instruction_hint")
            )
            self.advance_button.setText(
                self.translator.text("play_utterance")
            )
        elif self._mode == "dialogue_utterance":
            self.recording_label.setText(
                self.translator.text("recording_response")
            )
            self.recovery_hint.setText(
                self.translator.text("dialogue_utterance_hint")
            )
            self.advance_button.setText(
                self.translator.text("end_response")
            )
        elif self._mode == "splitting":
            self.recording_label.clear()

    def show_instructions(self) -> None:
        self.centered_scene_text.hide()
        self.text.show()
        self._mode = "instructions"
        self._practice = True
        self.stage_label.setText(
            self.translator.text("experiment_instructions")
        )
        self.scene_progress_label.setText(
            self.translator.text("practice_not_counted")
        )
        self.overall_progress_label.clear()
        self.text.setPlainText(self.translator.text("instruction_body"))
        self.recovery_hint.hide()
        self.image_label.hide()
        self._configure_content_layout(has_image=False)
        self.advance_button.setText(
            self.translator.text("begin_practice")
        )
        self.advance_button.setEnabled(True)
        self.abort_button.setEnabled(True)
        self.recording_label.setText(
            self.translator.text("not_recording")
        )
        self.crosshair.show()
        self.crosshair.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_progress(
        self, scene_current: int, scene_total: int, done: int, target: int
    ) -> None:
        self.scene_progress_label.setText(
            self.translator.text(
                "scene_progress", current=scene_current, total=scene_total
            )
        )
        self.overall_progress_label.setText(
            self.translator.text("progress", done=done, target=target)
        )

    def show_scene_summary(self, scene: Scene, practice: bool = False) -> None:
        self.text.show()
        self._mode = "summary"
        self._practice = practice
        self.stage_label.setText(
            self.translator.text(
                "practice_scene" if practice else "scene_overview"
            )
        )
        self.text.setPlainText(scene.text)
        self.text.hide()
        self.centered_scene_text.setText(scene.text)
        self._position_scene_text()
        self.centered_scene_text.show()
        self.centered_scene_text.raise_()
        self.recovery_hint.hide()
        self.advance_button.setText(self.translator.text("start_scene"))
        self.advance_button.setEnabled(True)
        self.abort_button.setEnabled(True)
        self.recording_label.setText(self.translator.text("not_recording"))
        if scene.segments[0].image_caption:
            # Decode in advance without revealing the stimulus before recording.
            self._image_path = scene.image_path
            self._image_source = QPixmap(str(scene.image_path))
            self.image_label.clear()
            self.image_label.hide()
            self._configure_content_layout(has_image=False)
        else:
            self._set_image(scene.image_path)
        self.crosshair.show()
        self.crosshair.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_segment(
        self, scene: Scene, index: int, practice: bool = False
    ) -> None:
        self._mode = "segment"
        self.centered_scene_text.hide()
        self._practice = practice
        segment = scene.segments[index]
        self.stage_label.setText(
            f"{segment.segment_id}  ·  {index + 1}/{len(scene.segments)}"
        )
        self.text.setPlainText(segment.text)
        self.recording_label.setText(self.translator.text("recording"))
        self.recovery_hint.show()
        key = (
            "finish_scene"
            if index == len(scene.segments) - 1
            else "next_segment"
        )
        self.advance_button.setText(self.translator.text(key))
        self.advance_button.setEnabled(True)
        self._set_image(scene.image_path)
        self.text.setVisible(not segment.image_caption)
        if segment.image_caption:
            self.stage_label.setText(self.translator.text("picture_stimulus"))
            self.recovery_hint.setText(self.translator.text("dialogue_utterance_hint"))
        else:
            self.recovery_hint.setText(
                self.translator.text("recovery_hint") + "\n"
                + self.translator.text("space_hint")
            )
        self.crosshair.show()
        self.crosshair.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_dialogue_instruction(
        self, scene: Scene, index: int, practice: bool = False
    ) -> None:
        self._mode = "dialogue_instruction"
        self.centered_scene_text.hide()
        self.text.show()
        self._practice = practice
        segment = scene.segments[index]
        self.stage_label.setText(
            self.translator.text(
                "dialogue_instruction_stage",
                current=index + 1,
                total=len(scene.segments),
            )
        )
        self.text.setPlainText(segment.instruction)
        self.recording_label.setText(self.translator.text("recording_raw"))
        self.recovery_hint.setText(
            self.translator.text("dialogue_instruction_hint")
        )
        self.recovery_hint.show()
        self.advance_button.setText(self.translator.text("play_utterance"))
        self.advance_button.setEnabled(True)
        self._set_image(scene.image_path)
        self.crosshair.show()
        self.crosshair.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_dialogue_utterance(
        self, scene: Scene, index: int, practice: bool = False
    ) -> None:
        self._mode = "dialogue_utterance"
        self.centered_scene_text.hide()
        self.text.show()
        self._practice = practice
        segment = scene.segments[index]
        self.stage_label.setText(
            self.translator.text(
                "dialogue_utterance_stage",
                current=index + 1,
                total=len(scene.segments),
            )
        )
        self.text.setPlainText(segment.utterance)
        self.recording_label.setText(
            self.translator.text("recording_response")
        )
        self.recovery_hint.setText(
            self.translator.text("dialogue_utterance_hint")
        )
        self.recovery_hint.show()
        self.advance_button.setText(self.translator.text("end_response"))
        self.advance_button.setEnabled(True)
        self._set_image(scene.image_path)
        self.crosshair.show()
        self.crosshair.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_splitting(self, done: int, total: int) -> None:
        self.centered_scene_text.hide()
        self.text.show()
        self._mode = "splitting"
        self.stage_label.setText(self.translator.text("experiment_complete"))
        self.text.setPlainText(
            self.translator.text("split_progress", done=done, total=total)
        )
        self.advance_button.setEnabled(False)
        self.abort_button.setEnabled(False)
        self.image_label.hide()
        self._configure_content_layout(has_image=False)
        self.recovery_hint.hide()
        self.crosshair.hide()
        self.recording_label.clear()

    def _set_image(self, image_path: Path | None) -> None:
        if not image_path:
            self._image_path = None
            self._image_source = None
            self.image_label.clear()
            self.image_label.hide()
            self._configure_content_layout(has_image=False)
            return
        if image_path == self._image_path and self._image_source is not None:
            self.image_label.show()
            self._configure_content_layout(has_image=True)
            self._rescale_image()
            return
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self._image_source = None
            self.image_label.hide()
            self._configure_content_layout(has_image=False)
            return
        self._image_source = pixmap
        self._image_path = image_path
        self.image_label.show()
        self._configure_content_layout(has_image=True)
        self._rescale_image()

    def _configure_content_layout(self, has_image: bool) -> None:
        if has_image:
            self.image_label.setMinimumHeight(300)
            self.text.setMinimumHeight(150)
            self.text.setMaximumHeight(230)
            self.text.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
        else:
            self.image_label.setMinimumHeight(0)
            self.text.setMinimumHeight(220)
            self.text.setMaximumHeight(16_777_215)
            self.text.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )

    def _rescale_image(self) -> None:
        if self._image_source:
            self.image_label.setPixmap(
                self._image_source.scaled(
                    self.image_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        if watched is self.image_label and event.type() == QEvent.Type.Resize:
            self._rescale_image()
        return super().eventFilter(watched, event)

    def _position_scene_text(self) -> None:
        width = max(200, self.width() - 88)
        self.centered_scene_text.setFixedWidth(width)
        height = max(180, self.centered_scene_text.sizeHint().height())
        self.centered_scene_text.setGeometry(
            (self.width() - width) // 2,
            (self.height() - height) // 2,
            width,
            height,
        )

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._position_scene_text()
        self._rescale_image()
        self.crosshair.move(
            (self.width() - self.crosshair.width()) // 2,
            (self.height() - self.crosshair.height()) // 2,
        )
        self.crosshair.raise_()


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = project_root.resolve()
        self.qt_settings = QSettings("SmileResearch", "ScenarioCapture")
        language = self.qt_settings.value("ui_language", "zh_CN")
        self.translator = Translator(str(language))
        self.camera = CameraController(self)
        self.camera.error.connect(self._camera_error)
        self.audio_player = LocalWavPlayer()

        self.stack = QStackedWidget()
        self.setup = SetupPage(self.translator, self.project_root)
        self.experiment = ExperimentPage(self.translator)
        self.stack.addWidget(self.setup)
        self.stack.addWidget(self.experiment)
        self.setCentralWidget(self.stack)

        self.setup.start_clicked.connect(self._prepare_experiment)
        self.setup.preview_clicked.connect(self._start_preview)
        self.setup.stop_preview_clicked.connect(self._stop_preview)
        self.setup.language_changed.connect(self._change_language)
        self.experiment.advance_clicked.connect(self._advance)
        self.experiment.abort_clicked.connect(self._abort_requested)

        language_index = self.setup.language_combo.findData(
            self.translator.language
        )
        if language_index >= 0:
            self.setup.language_combo.setCurrentIndex(language_index)

        self._restore_setup_settings()
        self._sync_tts_language(self.translator.language)
        self._reset_runtime()
        self.resize(1320, 900)
        self.retranslate()

    def _reset_runtime(self) -> None:
        self.settings: ExperimentSettings | None = None
        self.plan = None
        self.practice_scene: Scene | None = None
        self.is_practice = False
        self.flow_state = "idle"
        self.session_paths: SessionPaths | None = None
        self.event_logger: EventLogger | None = None
        self.manifest: ManifestStore | None = None
        self.split_manager: SplitManager | None = None
        self.split_bridge: SplitBridge | None = None
        self.audio_paths: dict[str, Path] = {}
        self.tts_effective = False
        self.tts_thread: TTSGenerationThread | None = None
        self.tts_progress: QProgressDialog | None = None
        self.scene_index = 0
        self.segment_index = -1
        self.captured_segments = 0
        self.current_segment_start_ms: int | None = None
        self.current_timings: list[SegmentTiming] = []
        self.current_raw_path: Path | None = None
        self.deferred_tasks: list[SplitTask] = []
        self.split_total = 0
        self.split_done = 0
        self.split_failures = 0
        self.finishing = False
        self.session_payload: dict[str, Any] = {}

    def retranslate(self) -> None:
        self.setWindowTitle(self.translator.text("app_title"))
        self.setup.retranslate()
        self.experiment.retranslate()

    def _change_language(self, language: str) -> None:
        self.translator.set_language(language)
        self.qt_settings.setValue("ui_language", language)
        self._sync_tts_language(self.translator.language)
        self.retranslate()

    def _sync_tts_language(self, language: str) -> None:
        language_code = TTS_LANGUAGE_BY_UI.get(language, "cmn-CN")
        self.setup.tts_language_edit.setText(language_code)
        voice_name = self.setup.voice_edit.text().strip()
        if voice_name and not voice_name.startswith(language_code):
            self.setup.voice_edit.clear()

    def _restore_setup_settings(self) -> None:
        saved_root = str(
            self.qt_settings.value(
                "save_root", str(self.project_root / "data")
            )
        )
        if Path(saved_root) == Path("D:/data/_capture/data"):
            saved_root = str(self.project_root / "data")
            self.qt_settings.setValue("save_root", saved_root)
        self.setup.save_edit.setText(saved_root)
        self.setup.ffmpeg_edit.setText(
            str(
                self.qt_settings.value(
                    "ffmpeg_path",
                    default_ffmpeg_path(self.project_root),
                )
            )
        )
        self.setup.target_spin.setValue(
            int(self.qt_settings.value("target_segments", 100))
        )
        self.setup.seed_spin.setValue(
            int(self.qt_settings.value("random_seed", 20260903))
        )
        split_mode = str(
            self.qt_settings.value("split_mode", "immediate")
        )
        split_index = self.setup.split_combo.findData(split_mode)
        if split_index >= 0:
            self.setup.split_combo.setCurrentIndex(split_index)
        self.setup.tts_group.setChecked(
            str(self.qt_settings.value("tts_enabled", "false")).lower()
            == "true"
        )
        self.setup.credentials_edit.setText(
            str(self.qt_settings.value("tts_credentials", ""))
        )
        self.setup.tts_language_edit.setText(
            str(self.qt_settings.value("tts_language", "cmn-CN"))
        )
        self.setup.voice_edit.setText(
            str(self.qt_settings.value("tts_voice", ""))
        )
        self.setup.rate_spin.setValue(
            float(self.qt_settings.value("tts_rate", 1.0))
        )
        saved_camera = str(
            self.qt_settings.value("camera_device_id", "")
        )
        if saved_camera:
            try:
                camera_index = self.setup.camera_combo.findData(
                    bytes.fromhex(saved_camera)
                )
                if camera_index >= 0:
                    self.setup.camera_combo.setCurrentIndex(camera_index)
            except ValueError:
                pass

    def _save_setup_settings(self, settings: ExperimentSettings) -> None:
        self.qt_settings.setValue("save_root", str(settings.save_root))
        self.qt_settings.setValue("target_segments", settings.target_segments)
        self.qt_settings.setValue("random_seed", settings.random_seed)
        self.qt_settings.setValue("split_mode", settings.split_mode)
        self.qt_settings.setValue("ffmpeg_path", str(settings.ffmpeg_path))
        self.qt_settings.setValue(
            "camera_device_id", settings.camera_device_id.hex()
        )
        self.qt_settings.setValue("tts_enabled", settings.tts.enabled)
        self.qt_settings.setValue(
            "tts_credentials",
            str(settings.tts.credentials_path or ""),
        )
        self.qt_settings.setValue(
            "tts_language", settings.tts.language_code
        )
        self.qt_settings.setValue("tts_voice", settings.tts.voice_name)
        self.qt_settings.setValue(
            "tts_rate", settings.tts.speaking_rate
        )
        self.qt_settings.sync()

    def _selected_device(self):
        device_id = self.setup.camera_combo.currentData()
        if device_id is None:
            return None
        return self.camera.find_device(bytes(device_id))

    def _ask_yes_no(self, title: str, text: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        yes_button = box.addButton(
            self.translator.text("yes"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(
            self.translator.text("no"), QMessageBox.ButtonRole.RejectRole
        )
        box.setDefaultButton(yes_button)
        box.exec()
        return box.clickedButton() is yes_button

    def _start_preview(self) -> None:
        device = self._selected_device()
        if device is None:
            QMessageBox.warning(
                self,
                self.translator.text("warning"),
                self.translator.text("no_camera"),
            )
            return
        try:
            width, height = self.camera.open(device, self.setup.preview)
            self.setup.set_camera_status(width, height)
        except Exception as exc:
            QMessageBox.critical(
                self,
                self.translator.text("error"),
                str(exc),
            )

    def _stop_preview(self) -> None:
        self.camera.close()
        self.setup.set_camera_status(None, None)

    def _collect_settings(self, ffmpeg: Path) -> ExperimentSettings:
        participant = self.setup.participant_edit.text().strip()
        if not participant:
            raise ValueError(self.translator.text("participant_required"))
        save_text = self.setup.save_edit.text().strip()
        if not save_text:
            raise ValueError(self.translator.text("save_path_required"))
        save_root = Path(save_text)
        device = self._selected_device()
        if device is None:
            raise ValueError(self.translator.text("no_camera"))

        tts_enabled = self.setup.tts_group.isChecked()
        credentials_text = self.setup.credentials_edit.text().strip()
        credentials = Path(credentials_text) if credentials_text else None
        if tts_enabled and (credentials is None or not credentials.is_file()):
            raise ValueError(self.translator.text("credentials"))
        return ExperimentSettings(
            participant_id=participant,
            save_root=save_root,
            target_segments=self.setup.target_spin.value(),
            random_seed=self.setup.seed_spin.value(),
            split_mode=str(self.setup.split_combo.currentData()),
            ffmpeg_path=ffmpeg,
            ui_language=self.translator.language,
            camera_device_id=bytes(device.id()),
            camera_description=device.description(),
            requested_width=1920,
            requested_height=1080,
            tts=TTSSettings(
                enabled=tts_enabled,
                credentials_path=credentials,
                language_code=self.setup.tts_language_edit.text().strip(),
                voice_name=self.setup.voice_edit.text().strip(),
                speaking_rate=self.setup.rate_spin.value(),
            ),
        )

    def _prepare_experiment(self) -> None:
        question_sets = self.setup.selected_question_sets()
        if not question_sets:
            QMessageBox.warning(
                self,
                self.translator.text("warning"),
                self.translator.text("no_question_set"),
            )
            return
        try:
            ffmpeg = ensure_ffmpeg(self.setup.ffmpeg_edit.text().strip())
            settings = self._collect_settings(ffmpeg)
            practice_scene, plan = build_sampling_plan_with_practice(
                question_sets,
                settings.target_segments,
                settings.random_seed,
            )
            for scene in (practice_scene, *plan.scenes):
                if scene.image_path and not QImageReader(str(scene.image_path)).canRead():
                    raise ValueError(
                        self.translator.text("image_unreadable", path=scene.image_path)
                    )
            device = self.camera.find_device(settings.camera_device_id)
            if device is None:
                raise RuntimeError(self.translator.text("no_camera"))
            width, height = self.camera.open(device, self.setup.preview)
        except Exception as exc:
            QMessageBox.critical(
                self, self.translator.text("error"), str(exc)
            )
            return

        if (width, height) != (
            settings.requested_width,
            settings.requested_height,
        ):
            if not self._ask_yes_no(
                self.translator.text("warning"),
                self.translator.text(
                    "camera_resolution_warning",
                    width=width,
                    height=height,
                ),
            ):
                return

        summary = self.translator.text(
            "plan_summary_with_practice",
            scenes=len(plan.scenes),
            segments=plan.actual_segments,
        )
        if not self._ask_yes_no(
            self.translator.text("confirm"), summary
        ):
            return

        self.settings = settings
        self.plan = plan
        self.practice_scene = practice_scene
        self.tts_effective = settings.tts.enabled
        self._save_setup_settings(settings)
        if settings.tts.enabled:
            self._generate_tts()
        else:
            self._begin_session()

    def _generate_tts(self) -> None:
        assert self.settings and self.plan
        assert self.settings.tts.credentials_path
        config = TTSConfig(
            credentials_path=self.settings.tts.credentials_path,
            language_code=self.settings.tts.language_code,
            voice_name=self.settings.tts.voice_name,
            speaking_rate=self.settings.tts.speaking_rate,
        )
        cache = TTSCache(self.project_root / "tts_cache", config)
        scenes = [self.practice_scene, *self.plan.scenes]
        texts = [
            text
            for scene in scenes
            if scene is not None
            for text in [
                scene.text,
                *(
                    spoken
                    for item in scene.segments
                    for spoken in item.tts_texts
                ),
            ]
        ]
        self.tts_progress = QProgressDialog(
            self.translator.text("preparing_tts"),
            "",
            0,
            max(1, len(cache.missing_texts(texts))),
            self,
        )
        self.tts_progress.setCancelButton(None)
        self.tts_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.tts_progress.show()
        self.tts_thread = TTSGenerationThread(cache, texts)
        self.tts_thread.progress.connect(
            lambda done, total: (
                self.tts_progress.setMaximum(max(1, total)),
                self.tts_progress.setValue(done),
            )
        )
        self.tts_thread.succeeded.connect(self._tts_ready)
        self.tts_thread.failed.connect(self._tts_failed)
        self.tts_thread.start()

    def _tts_ready(self, paths: object) -> None:
        if self.tts_progress:
            self.tts_progress.close()
        self.audio_paths = dict(paths)
        self.tts_effective = True
        self._begin_session()

    def _tts_failed(self, error: str) -> None:
        if self.tts_progress:
            self.tts_progress.close()
        box = QMessageBox(self)
        box.setWindowTitle(self.translator.text("error"))
        box.setText(self.translator.text("tts_failure", error=error))
        retry = box.addButton(
            self.translator.text("tts_retry"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        without = box.addButton(
            self.translator.text("tts_continue_without"),
            QMessageBox.ButtonRole.DestructiveRole,
        )
        box.addButton(
            self.translator.text("tts_cancel"),
            QMessageBox.ButtonRole.RejectRole,
        )
        box.exec()
        if box.clickedButton() is retry:
            self._generate_tts()
        elif box.clickedButton() is without:
            self.audio_paths = {}
            self.tts_effective = False
            self._begin_session()

    def _begin_session(self) -> None:
        assert self.settings and self.plan
        try:
            self.session_paths = SessionPaths.create(
                self.settings.save_root,
                self.settings.participant_id,
            )
            self.event_logger = EventLogger(
                self.session_paths.logs / "events.jsonl"
            )
            self.manifest = ManifestStore(self.session_paths.manifest)
            self.split_bridge = SplitBridge()
            self.split_bridge.result.connect(self._split_result)
            self.split_manager = SplitManager(
                self.settings.ffmpeg_path,
                self.split_bridge.report,
            )
            settings_payload = settings_to_json(self.settings)
            settings_payload["tts"].pop("credentials_path", None)
            self.session_payload = {
                "status": "running",
                "started_at": utc_now(),
                "settings": settings_payload,
                "tts_effective": self.tts_effective,
                "question_sets": sorted(
                    {
                        scene.question_set_name
                        for scene in [
                            self.practice_scene,
                            *self.plan.scenes,
                        ]
                        if scene is not None
                    }
                ),
                "sampling": {
                    "requested_segments": self.plan.requested_segments,
                    "actual_segments": self.plan.actual_segments,
                    "seed": self.plan.seed,
                    "exhausted": self.plan.exhausted,
                    "practice_scene_id": (
                        self.practice_scene.question_set_id
                        + ":"
                        + self.practice_scene.scene_id
                    ),
                    "scene_ids": [
                        scene.question_set_id + ":" + scene.scene_id
                        for scene in self.plan.scenes
                    ],
                },
            }
            atomic_write_json(
                self.session_paths.session_json, self.session_payload
            )
            # Save exactly which stimulus text and image were used in this run.
            stimuli = []
            for index, scene in enumerate((self.practice_scene, *self.plan.scenes)):
                stimuli.append({
                    "question_set_id": scene.question_set_id,
                    "scene_id": scene.scene_id,
                    "practice": index == 0,
                    "scene_text": scene.text,
                    "scenario_description": scene.scenario_description,
                    "image_prompt": scene.image_prompt,
                    "image_path": str(scene.image_path) if scene.image_path else "",
                    "image_sha256": (
                        hashlib.sha256(scene.image_path.read_bytes()).hexdigest()
                        if scene.image_path else ""
                    ),
                    "segments": [
                        {
                            "segment_id": s.segment_id,
                            "stimulus_format": s.stimulus_format,
                            "text": s.text,
                            "instruction": s.instruction,
                            "utterance": s.utterance,
                            "purpose": s.purpose,
                        }
                        for s in scene.segments
                    ],
                })
            atomic_write_json(self.session_paths.root / "stimuli.json", stimuli)
            self.event_logger.write(
                "session_started",
                session_id=self.session_paths.session_id,
                practice_scenes=1,
                planned_scenes=len(self.plan.scenes),
                planned_segments=self.plan.actual_segments,
            )
        except Exception as exc:
            self._cleanup_runtime()
            QMessageBox.critical(
                self, self.translator.text("error"), str(exc)
            )
            return

        self.camera.set_preview(None)
        self.stack.setCurrentWidget(self.experiment)
        self.scene_index = 0
        self.segment_index = -1
        self.captured_segments = 0
        self.is_practice = True
        self.flow_state = "instructions"
        self.experiment.show_instructions()
        self.event_logger.write("experiment_instructions_shown")

    @property
    def current_scene(self) -> Scene:
        assert self.plan
        if self.is_practice:
            assert self.practice_scene
            return self.practice_scene
        return self.plan.scenes[self.scene_index]

    def _show_scene_summary(self) -> None:
        scene = self.current_scene
        self.segment_index = -1
        self.flow_state = "summary"
        if self.is_practice:
            self.experiment.scene_progress_label.setText(
                self.translator.text("practice_not_counted")
            )
            self.experiment.overall_progress_label.setText(
                self.translator.text(
                    "progress",
                    done=self.captured_segments,
                    target=self.plan.requested_segments,
                )
            )
        else:
            self.experiment.set_progress(
                self.scene_index + 1,
                len(self.plan.scenes),
                self.captured_segments,
                self.plan.requested_segments,
            )
        self.experiment.show_scene_summary(
            scene, practice=self.is_practice
        )
        self._play_text(scene.text)
        assert self.event_logger
        self.event_logger.write(
            "scene_summary_shown",
            question_set_id=scene.question_set_id,
            scene_id=scene.scene_id,
            scene_order=0 if self.is_practice else self.scene_index + 1,
            practice=self.is_practice,
        )

    def _advance(self) -> None:
        if not self.plan or self.finishing:
            return
        if self.flow_state == "instructions":
            self._show_scene_summary()
        elif self.flow_state == "summary":
            self._start_scene_recording()
        elif self.flow_state == "dialogue_instruction":
            self._start_dialogue_utterance()
        elif self.flow_state == "dialogue_utterance":
            self._end_dialogue_utterance()
        elif (
            self.flow_state == "segment"
            and self.segment_index < len(self.current_scene.segments) - 1
        ):
            self._next_segment()
        elif self.flow_state == "segment":
            self._finish_scene()

    def _start_scene_recording(self) -> None:
        assert self.session_paths and self.event_logger
        scene = self.current_scene
        if self.is_practice:
            raw_name = (
                f"practice_{scene.question_set_id}_{scene.scene_id}.mp4"
            )
            requested_path = self.session_paths.practice / raw_name
        else:
            raw_name = (
                f"{self.scene_index + 1:03d}_{scene.question_set_id}_"
                f"{scene.scene_id}.mp4"
            )
            requested_path = self.session_paths.raw / raw_name
        try:
            self.current_raw_path = self.camera.start_recording(
                requested_path
            )
        except Exception as exc:
            self._camera_error(str(exc))
            return
        self.current_timings = []
        self.segment_index = 0
        segment = scene.segments[0]
        self.event_logger.write(
            "recording_started",
            question_set_id=scene.question_set_id,
            scene_id=scene.scene_id,
            raw_video=str(self.current_raw_path),
            practice=self.is_practice,
        )
        if segment.is_dialogue:
            self.current_segment_start_ms = None
            self.flow_state = "dialogue_instruction"
            self._show_dialogue_instruction()
        else:
            self.current_segment_start_ms = (
                self.camera.timestamp_ms() if segment.image_caption else 0
            )
            self.flow_state = "segment"
            if segment.image_caption:
                self.audio_player.stop()
            self.experiment.show_segment(
                scene, 0, practice=self.is_practice
            )
            self._play_text(segment.text)
            self.event_logger.write(
                "segment_started",
                scene_id=scene.scene_id,
                segment_id=segment.segment_id,
                media_timestamp_ms=self.current_segment_start_ms,
                practice=self.is_practice,
            )

    def _show_dialogue_instruction(self) -> None:
        assert self.event_logger
        scene = self.current_scene
        segment = scene.segments[self.segment_index]
        timestamp = self.camera.timestamp_ms()
        self.event_logger.write(
            "instruction_started",
            scene_id=scene.scene_id,
            segment_id=segment.segment_id,
            media_timestamp_ms=timestamp,
            practice=self.is_practice,
        )
        self.experiment.show_dialogue_instruction(
            scene, self.segment_index, practice=self.is_practice
        )
        self._play_text(segment.instruction)

    def _start_dialogue_utterance(self) -> None:
        assert self.event_logger
        scene = self.current_scene
        segment = scene.segments[self.segment_index]
        boundary = self.camera.timestamp_ms()
        self.current_segment_start_ms = boundary
        self.flow_state = "dialogue_utterance"
        self.event_logger.write(
            "segment_started",
            scene_id=scene.scene_id,
            segment_id=segment.segment_id,
            media_timestamp_ms=boundary,
            practice=self.is_practice,
        )
        self.event_logger.write(
            "utterance_started",
            scene_id=scene.scene_id,
            segment_id=segment.segment_id,
            media_timestamp_ms=boundary,
            practice=self.is_practice,
        )
        self.experiment.show_dialogue_utterance(
            scene, self.segment_index, practice=self.is_practice
        )
        self._play_text(segment.utterance)

    def _end_dialogue_utterance(self) -> None:
        scene = self.current_scene
        boundary = self.camera.timestamp_ms()
        if not self._store_current_timing(boundary):
            return
        self.event_logger.write(
            "utterance_ended",
            scene_id=scene.scene_id,
            segment_id=scene.segments[self.segment_index].segment_id,
            media_timestamp_ms=boundary,
            practice=self.is_practice,
        )
        if self.segment_index == len(scene.segments) - 1:
            self._finalize_scene_recording(boundary)
            return
        self.segment_index += 1
        self.current_segment_start_ms = None
        self.flow_state = "dialogue_instruction"
        self._show_dialogue_instruction()

    def _store_current_timing(self, boundary: int) -> bool:
        assert self.event_logger
        if (
            self.current_segment_start_ms is None
            or boundary <= self.current_segment_start_ms
        ):
            QMessageBox.warning(
                self,
                self.translator.text("warning"),
                self.translator.text("wait_for_timestamp"),
            )
            return False
        scene = self.current_scene
        current = scene.segments[self.segment_index]
        self.current_timings.append(
            SegmentTiming(
                current.segment_id,
                self.current_segment_start_ms,
                boundary,
            )
        )
        self.event_logger.write(
            "segment_ended",
            scene_id=scene.scene_id,
            segment_id=current.segment_id,
            media_timestamp_ms=boundary,
            practice=self.is_practice,
        )
        return True

    def _next_segment(self) -> None:
        assert self.event_logger
        scene = self.current_scene
        boundary = self.camera.timestamp_ms()
        if not self._store_current_timing(boundary):
            return
        self.segment_index += 1
        self.current_segment_start_ms = boundary
        following = scene.segments[self.segment_index]
        self.event_logger.write(
            "segment_started",
            scene_id=scene.scene_id,
            segment_id=following.segment_id,
            media_timestamp_ms=boundary,
            practice=self.is_practice,
        )
        self.experiment.show_segment(
            scene, self.segment_index, practice=self.is_practice
        )
        self._play_text(following.text)

    def _finish_scene(self) -> None:
        assert self.event_logger and self.manifest and self.session_paths
        assert self.settings and self.current_raw_path
        boundary = self.camera.timestamp_ms()
        if not self._store_current_timing(boundary):
            return
        self._finalize_scene_recording(boundary)

    def _finalize_scene_recording(self, boundary: int) -> None:
        assert self.event_logger and self.manifest and self.session_paths
        assert self.settings and self.current_raw_path
        scene = self.current_scene
        self.audio_player.stop()
        try:
            actual_raw, recorder_duration = self.camera.stop_recording()
        except Exception as exc:
            self._camera_error(str(exc))
            return
        self.current_raw_path = actual_raw
        self.event_logger.write(
            "recording_stopped",
            scene_id=scene.scene_id,
            media_timestamp_ms=boundary,
            recorder_duration_ms=recorder_duration,
            raw_video=str(actual_raw),
            practice=self.is_practice,
        )

        if self.is_practice:
            self.event_logger.write(
                "practice_scene_completed",
                scene_id=scene.scene_id,
                raw_video=str(actual_raw),
                segment_count=len(scene.segments),
            )
            self.current_raw_path = None
            self.current_timings = []
            self.segment_index = -1
            self.is_practice = False
            if not self.plan.scenes:
                self._finish_experiment()
            else:
                self.scene_index = 0
                self._show_scene_summary()
            return

        rows = build_manifest_rows(
            participant_id=self.settings.participant_id,
            session_paths=self.session_paths,
            scene=scene,
            scene_order=self.scene_index + 1,
            raw_video_path=actual_raw,
            timings=self.current_timings,
        )
        self.manifest.add_rows(rows)
        tasks: list[SplitTask] = []
        for row, timing in zip(rows, self.current_timings, strict=True):
            clip_key = (
                str(row["question_set_id"])
                + "|"
                + str(row["scene_id"])
                + "|"
                + str(row["segment_id"])
            )
            tasks.append(
                SplitTask(
                    clip_key=clip_key,
                    raw_path=actual_raw,
                    output_path=self.session_paths.root
                    / str(row["clip_video_path"]),
                    start_ms=timing.start_ms,
                    end_ms=timing.end_ms,
                )
            )
        self.split_total += len(tasks)
        if self.settings.split_mode == "immediate":
            assert self.split_manager
            for task in tasks:
                self.split_manager.enqueue(task)
        else:
            self.deferred_tasks.extend(tasks)

        self.captured_segments += len(scene.segments)
        self.event_logger.write(
            "scene_completed",
            scene_id=scene.scene_id,
            captured_segments=self.captured_segments,
        )
        self.current_raw_path = None
        self.current_timings = []

        if (
            self.captured_segments >= self.plan.requested_segments
            or self.scene_index >= len(self.plan.scenes) - 1
        ):
            self._finish_experiment()
        else:
            self.scene_index += 1
            self._show_scene_summary()

    def _finish_experiment(self) -> None:
        assert self.settings and self.event_logger and self.split_manager
        self.finishing = True
        self.audio_player.stop()
        self.event_logger.write(
            "recording_phase_completed",
            captured_segments=self.captured_segments,
            split_tasks=self.split_total,
        )
        if self.settings.split_mode == "deferred":
            for task in self.deferred_tasks:
                self.split_manager.enqueue(task)
            self.deferred_tasks.clear()
        if self.split_done >= self.split_total:
            self._complete_session()
        else:
            self.experiment.show_splitting(
                self.split_done, self.split_total
            )

    def _split_result(
        self, task: SplitTask, success: bool, error: str
    ) -> None:
        if not self.manifest:
            return
        self.manifest.update_split(
            task.clip_key,
            "complete" if success else "failed",
            error,
        )
        self.split_done += 1
        if not success:
            self.split_failures += 1
        if self.finishing:
            self.experiment.show_splitting(
                self.split_done, self.split_total
            )
            if self.split_done >= self.split_total:
                self._complete_session()

    def _complete_session(self) -> None:
        assert self.session_paths and self.event_logger
        self.session_payload.update(
            {
                "status": (
                    "completed"
                    if self.split_failures == 0
                    else "completed_with_split_errors"
                ),
                "finished_at": utc_now(),
                "captured_segments": self.captured_segments,
                "split_completed": self.split_done,
                "split_failures": self.split_failures,
            }
        )
        atomic_write_json(
            self.session_paths.session_json, self.session_payload
        )
        self.event_logger.write(
            "session_completed",
            captured_segments=self.captured_segments,
            split_failures=self.split_failures,
        )
        session_root = self.session_paths.root
        failures = self.split_failures
        self._cleanup_runtime(wait_for_splitter=True)
        self.stack.setCurrentWidget(self.setup)
        self.camera.close()
        self.setup.set_camera_status(None, None)
        if failures:
            text = self.translator.text(
                "split_failed_detail", count=failures
            )
            QMessageBox.warning(
                self, self.translator.text("experiment_complete"), text
            )
        else:
            QMessageBox.information(
                self,
                self.translator.text("experiment_complete"),
                self.translator.text(
                    "experiment_complete_detail", path=session_root
                ),
            )

    def _play_text(self, text: str) -> None:
        self.audio_player.stop()
        path = self.audio_paths.get(text)
        if path and path.is_file():
            self.audio_player.play(path)

    def _camera_error(self, error: str) -> None:
        QMessageBox.critical(
            self,
            self.translator.text("error"),
            self.translator.text("camera_error", error=error),
        )
        if self.stack.currentWidget() is self.experiment and self.session_paths:
            self._abort_session(require_confirmation=False)

    def _abort_requested(self) -> None:
        self._abort_session(require_confirmation=True)

    def _abort_session(self, require_confirmation: bool) -> bool:
        if require_confirmation and not self._ask_yes_no(
            self.translator.text("confirm"),
            self.translator.text("confirm_abort"),
        ):
            return False
        self.audio_player.stop()
        if self.camera.is_recording:
            try:
                actual, timestamp = self.camera.stop_recording()
                if self.event_logger:
                    self.event_logger.write(
                        "recording_aborted",
                        raw_video=str(actual),
                        media_timestamp_ms=timestamp,
                    )
            except Exception as exc:
                if self.event_logger:
                    self.event_logger.write(
                        "recording_abort_error", error=str(exc)
                    )
        if self.session_paths:
            self.session_payload.update(
                {"status": "aborted", "finished_at": utc_now()}
            )
            atomic_write_json(
                self.session_paths.session_json, self.session_payload
            )
        self._cleanup_runtime()
        self.camera.close()
        self.setup.set_camera_status(None, None)
        self.stack.setCurrentWidget(self.setup)
        return True

    def _cleanup_runtime(self, wait_for_splitter: bool = False) -> None:
        if self.split_manager:
            self.split_manager.shutdown(wait=wait_for_splitter)
        if self.event_logger:
            self.event_logger.close()
        self.audio_player.stop()
        self._reset_runtime()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.stack.currentWidget() is self.experiment:
            if not self._ask_yes_no(
                self.translator.text("confirm"),
                self.translator.text("confirm_abort"),
            ):
                event.ignore()
                return
            self._abort_session(require_confirmation=False)
        self.camera.close()
        event.accept()


def create_application(project_root: Path) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("ScenarioCapture")
    app.setOrganizationName("SmileResearch")
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f4f7fb"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#0f172a"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#0f172a"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#0f172a"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#e2e8f0"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#0f172a"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#dbeafe"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#0f172a"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#64748b"))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLE)
    window = MainWindow(project_root)
    return app, window
