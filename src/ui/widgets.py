"""Custom widgets for Sonara's UI."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from core.config import load_config, update_config
from core.video import CANVAS_WIDTH, WATERMARK_MARGIN

RADIUS = 10.0


class NoScrollMixin:
    """Ignores mouse-wheel events so scrolling the page never silently
    changes the selected value; the event bubbles up to the scroll area."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoScrollComboBox(NoScrollMixin, QComboBox):
    pass


class NoScrollDateEdit(NoScrollMixin, QDateEdit):
    pass


class PresetBar(QWidget):
    """A 'saved templates' combo + Save/Delete row that persists named text
    snippets to a config.json list key, and drops a chosen one into the field
    it manages. Sits under the Title and Description inputs on the Publish tab.

    `get_text` / `set_text` are the target field's read / write callables
    (e.g. ``line_edit.text`` / ``line_edit.setText``)."""

    def __init__(self, config_key, get_text, set_text, noun="entry", parent=None):
        super().__init__(parent)
        self._key = config_key
        self._get_text = get_text
        self._set_text = set_text
        self._noun = noun
        self._presets = [
            p
            for p in load_config().get(config_key, [])
            if isinstance(p, dict) and p.get("name")
        ]

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.combo = NoScrollComboBox()
        self.combo.setToolTip(f"Load a saved {noun}")
        self.combo.currentIndexChanged.connect(self._apply_selected)
        row.addWidget(self.combo, 1)
        save_btn = QPushButton("Save…")
        save_btn.setToolTip(f"Save the current {noun} as a template to reuse later")
        save_btn.clicked.connect(self._save)
        row.addWidget(save_btn)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._delete)
        row.addWidget(self._delete_btn)
        self._refresh()

    def _refresh(self) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(f"Saved {self._noun}s", None)
        for preset in self._presets:
            self.combo.addItem(preset["name"], preset["name"])
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)
        self._delete_btn.setEnabled(bool(self._presets))

    def _apply_selected(self, _index: int) -> None:
        name = self.combo.currentData()
        if not name:
            return
        for preset in self._presets:
            if preset["name"] == name:
                self._set_text(preset["text"])
                break

    def _save(self) -> None:
        text = (self._get_text() or "").strip()
        if not text:
            QMessageBox.information(self, "Nothing to save", f"Type a {self._noun} first.")
            return
        name, ok = QInputDialog.getText(
            self,
            f"Save {self._noun}",
            f"Name this {self._noun} template:",
            text=self.combo.currentData() or "",
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        self._presets = [p for p in self._presets if p["name"] != name]
        self._presets.append({"name": name, "text": text})
        self._presets.sort(key=lambda p: p["name"].lower())
        update_config(**{self._key: self._presets})
        self._refresh()
        index = self.combo.findData(name)
        if index >= 0:
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(index)
            self.combo.blockSignals(False)

    def _delete(self) -> None:
        name = self.combo.currentData()
        if not name:
            QMessageBox.information(
                self, "Pick one first", f"Select a saved {self._noun} to delete."
            )
            return
        self._presets = [p for p in self._presets if p["name"] != name]
        update_config(**{self._key: self._presets})
        self._refresh()


class VideoPreview(QLabel):
    """A scaled-down 1920x1080 canvas previewing exactly how the rendered
    video will look: the cover image is fit into a centered 1080x1080 square
    (pillarboxed with black on the sides to fill the 16:9 frame), and
    letterboxed with black within that square if the image itself isn't
    square — matching the actual ffmpeg render (see core/video.py)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pixmap: QPixmap | None = None
        self._wm_text = ""
        self._wm_corner = "bottom-right"
        self._wm_size = 16

    def heightForWidth(self, width: int) -> int:
        return int(width * 9 / 16)

    def hasHeightForWidth(self) -> bool:
        return True

    def set_image(self, path: str | None) -> None:
        if path:
            pixmap = QPixmap(path)
            self._pixmap = pixmap if not pixmap.isNull() else None
        else:
            self._pixmap = None
        self.update()

    def set_watermark(self, text: str, corner: str, size: int) -> None:
        self._wm_text = text
        self._wm_corner = corner
        self._wm_size = size
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # Keep a 16:9 frame centered within whatever box the layout actually
        # gave us — heightForWidth is only a hint, not a hard guarantee.
        frame_w, frame_h = outer.width(), outer.width() * 9 / 16
        if frame_h > outer.height():
            frame_h = outer.height()
            frame_w = outer.height() * 16 / 9
        frame = QRectF(0, 0, frame_w, frame_h)
        frame.moveCenter(outer.center())

        path = QPainterPath()
        path.addRoundedRect(frame, RADIUS, RADIUS)
        painter.setClipPath(path)
        painter.fillRect(frame, QColor("#000000"))

        # The 1080x1080 square sits centered in the 1920x1080 frame, i.e. its
        # side equals the frame's height.
        square = QRectF(0, 0, frame_h, frame_h)
        square.moveCenter(frame.center())

        if self._pixmap is not None:
            scaled = self._pixmap.scaled(
                square.size().toSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            x = square.left() + (square.width() - scaled.width()) / 2
            y = square.top() + (square.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)
        else:
            painter.setPen(QColor("#8A8A90"))
            font = painter.font()
            font.setFamilies(["Inter", "Segoe UI", "Arial"])
            font.setPointSize(11)
            painter.setFont(font)
            painter.drawText(frame, Qt.AlignCenter, "No cover image selected")

        if self._wm_text.strip():
            self._draw_watermark(painter, frame)

        painter.setClipping(False)
        painter.setPen(QColor("#3A3A40"))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(frame, RADIUS, RADIUS)

    def _draw_watermark(self, painter: QPainter, frame: QRectF) -> None:
        # Scaled down from the real 1920-wide render canvas so the preview's
        # watermark size/position matches the actual video exactly.
        scale = frame.width() / CANVAS_WIDTH
        font = QFont("Segoe UI", -1)
        font.setPixelSize(max(1, round(self._wm_size * scale)))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text = self._wm_text.strip()
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.ascent()
        margin = WATERMARK_MARGIN * scale

        x = frame.right() - margin - text_w if "right" in self._wm_corner else frame.left() + margin
        y = frame.top() + margin + text_h if "top" in self._wm_corner else frame.bottom() - margin

        painter.setPen(QColor(0, 0, 0, 160))
        painter.drawText(QPointF(x + 1, y + 1), text)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QPointF(x, y), text)
