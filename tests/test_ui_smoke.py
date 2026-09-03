from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_main_window_can_be_created() -> None:
    from PySide6.QtGui import QPalette

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
    window.close()
    app.processEvents()
