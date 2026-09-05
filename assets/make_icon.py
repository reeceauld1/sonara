"""Regenerate assets/icon.png and assets/icon.ico (the app / window icon).

A simple audio-equalizer glyph on a rounded dark tile - generic 'music' mark,
no wordmark. Run from the repo root:

    .venv\\Scripts\\python.exe assets\\make_icon.py
"""
from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication

ASSETS = Path(__file__).resolve().parent
BASE = 512  # master size; downscaled for the .ico frames

BG_TOP = QColor("#26262B")
BG_BOTTOM = QColor("#141416")
BORDER = QColor("#3A3A40")
BAR_TOP = QColor("#F5F5F7")
BAR_BOTTOM = QColor("#9C9CA2")

# (x-centre fraction, height fraction) for each equalizer bar
BARS = [(0.235, 0.42), (0.412, 0.72), (0.588, 0.54), (0.765, 0.86)]


def render(size: int) -> QPixmap:
    scale = size / BASE
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    # rounded tile
    inset = 8 * scale
    tile = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    radius = 96 * scale
    path = QPainterPath()
    path.addRoundedRect(tile, radius, radius)

    grad = QLinearGradient(0, tile.top(), 0, tile.bottom())
    grad.setColorAt(0.0, BG_TOP)
    grad.setColorAt(1.0, BG_BOTTOM)
    p.fillPath(path, QBrush(grad))

    p.setPen(QColor(BORDER))
    p.drawPath(path)

    # equalizer bars
    bar_w = 66 * scale
    bar_radius = 26 * scale
    mid = size / 2
    span = size * 0.66
    bar_grad = QLinearGradient(0, size * 0.12, 0, size * 0.88)
    bar_grad.setColorAt(0.0, BAR_TOP)
    bar_grad.setColorAt(1.0, BAR_BOTTOM)

    for cx_frac, h_frac in BARS:
        h = span * h_frac
        x = size * cx_frac - bar_w / 2
        y = mid - h / 2
        bar = QPainterPath()
        bar.addRoundedRect(QRectF(x, y, bar_w, h), bar_radius, bar_radius)
        p.fillPath(bar, QBrush(bar_grad))

    p.end()
    return pm


def _png_bytes(pm: QPixmap) -> bytes:
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    pm.save(buf, "PNG")
    return bytes(buf.data())


def _write_ico(path: Path, sizes: list[int]) -> None:
    """Assemble a multi-frame .ico by hand (each frame stored as PNG, which
    Windows Vista+ supports), so no Pillow dependency is needed."""
    images = [(s, _png_bytes(render(s))) for s in sizes]
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)

    entries = b""
    offset = 6 + 16 * count
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)

    path.write_bytes(header + entries + b"".join(d for _, d in images))


def main() -> None:
    app = QApplication([])  # noqa: F841 - QPixmap/QPainter need a QApplication

    png_path = ASSETS / "icon.png"
    render(BASE).scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(
        str(png_path), "PNG"
    )
    print("wrote", png_path)

    ico_path = ASSETS / "icon.ico"
    _write_ico(ico_path, [16, 24, 32, 48, 64, 128, 256])
    print("wrote", ico_path, "(multi-size)")


if __name__ == "__main__":
    main()
