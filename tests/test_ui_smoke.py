from __future__ import annotations

import os
from pathlib import Path

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
