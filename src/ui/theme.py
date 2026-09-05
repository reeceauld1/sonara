"""Loads the greyscale studio-console theme (assets/style.qss) and the app
icon onto the QApplication."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication


def _assets_dir() -> Path:
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    return base_dir / "assets"


def app_icon() -> QIcon:
    assets = _assets_dir()
    for name in ("icon.ico", "icon.png"):  # .ico carries crisp small sizes
        path = assets / name
        if path.exists():
            return QIcon(str(path))
    return QIcon()


def apply_theme(app: QApplication) -> None:
    # Fusion is Qt's own cross-platform style and fully honors QSS; the native
    # Windows style renders combobox/list popups via OS theming and ignores
    # our stylesheet for them, so we'd otherwise get a white dropdown.
    app.setStyle("Fusion")

    font = QFont()
    font.setFamilies(["Inter", "Segoe UI Variable Text", "Segoe UI", "Arial"])
    font.setPointSize(10)
    app.setFont(font)

    app.setWindowIcon(app_icon())

    assets_dir = _assets_dir()
    qss_path = assets_dir / "style.qss"
    if not qss_path.exists():
        return
    qss = qss_path.read_text(encoding="utf-8")
    icons = [
        "check", "arrow_down", "arrow_down_hover", "arrow_down_disabled",
        "arrow_up", "arrow_up_hover",
    ]
    for name in icons:
        placeholder = "{{" + name.upper() + "_ICON}}"
        qss = qss.replace(placeholder, (assets_dir / f"{name}.png").as_posix())
    app.setStyleSheet(qss)
