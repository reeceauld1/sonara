"""Interactive square crop selector: the whole source image is shown fit to
the widget with a draggable, resizable square marquee on top. Drag inside the
square to move it, drag a corner to resize it (it always stays square), scroll
to zoom it in/out. `export_square` reads exactly what's inside the square back
out of the full-resolution source at any target size."""
from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui.image_convert import pil_to_qpixmap

# The on-screen pixmap is downscaled for smooth redraws; export() always reads
# back from the full-resolution source, never this preview.
DISPLAY_MAX_DIM = 1400
HANDLE_VISUAL = 10   # side of a drawn corner handle, px
HANDLE_HIT = 22      # how close to a corner counts as grabbing it, px
MIN_SIDE_SRC = 16    # smallest the crop square may get, in source px
_CORNERS = ("nw", "ne", "sw", "se")


class CropCanvas(QWidget):
    crop_changed = Signal(float)  # crop side as a fraction of the largest square

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._source: Image.Image | None = None
        self._display_pixmap = None
        self._crop = QRectF()          # source-pixel coords, always square
        self._mode: str | None = None  # None | "move" | one of _CORNERS
        self._drag_last = QPointF()

    # ---------------------------------------------------------------- image
    def set_image(self, image: Image.Image | None) -> None:
        self._source = image.convert("RGB") if image is not None else None
        if self._source is None:
            self._display_pixmap = None
            self._crop = QRectF()
            self.update()
            return
        w, h = self._source.size
        longest = max(w, h)
        if longest > DISPLAY_MAX_DIM:
            factor = DISPLAY_MAX_DIM / longest
            display_img = self._source.resize(
                (max(1, round(w * factor)), max(1, round(h * factor))), Image.LANCZOS
            )
        else:
            display_img = self._source
        self._display_pixmap = pil_to_qpixmap(display_img)
        side = min(w, h)
        self._crop = QRectF((w - side) / 2, (h - side) / 2, side, side)
        self.update()

    def has_image(self) -> bool:
        return self._source is not None

    # ------------------------------------------------------------ crop size
    def crop_fraction(self) -> float:
        """Crop side / largest square that fits the source (1.0 == biggest)."""
        if self._source is None:
            return 1.0
        w, h = self._source.size
        longest_square = min(w, h)
        return self._crop.width() / longest_square if longest_square else 1.0

    def set_crop_fraction(self, fraction: float) -> None:
        if self._source is None:
            return
        w, h = self._source.size
        max_side = min(w, h)
        side = max(MIN_SIDE_SRC, min(max_side, fraction * max_side))
        rect = QRectF(0, 0, side, side)
        rect.moveCenter(self._crop.center())
        self._crop = self._clamp_into_image(rect)
        self.update()

    # ------------------------------------------------------------ geometry
    def _scale(self) -> float:
        """source px -> widget px for the fit-and-centered image."""
        if self._source is None:
            return 1.0
        w, h = self._source.size
        return min(self.width() / w, self.height() / h)

    def _image_rect(self) -> QRectF:
        """Where the whole source image is drawn: fit, centered, aspect kept."""
        if self._source is None:
            return QRectF()
        w, h = self._source.size
        scale = self._scale()
        rect = QRectF(0, 0, w * scale, h * scale)
        rect.moveCenter(QRectF(self.rect()).center())
        return rect

    def _src_to_widget(self, rect: QRectF) -> QRectF:
        origin = self._image_rect().topLeft()
        s = self._scale()
        return QRectF(
            origin.x() + rect.x() * s,
            origin.y() + rect.y() * s,
            rect.width() * s,
            rect.height() * s,
        )

    def _widget_to_src(self, pos: QPointF) -> QPointF:
        origin = self._image_rect().topLeft()
        s = self._scale() or 1.0
        return QPointF((pos.x() - origin.x()) / s, (pos.y() - origin.y()) / s)

    def _clamp_into_image(self, rect: QRectF) -> QRectF:
        w, h = self._source.size
        side = min(rect.width(), w, h)
        x = min(max(rect.x(), 0.0), w - side)
        y = min(max(rect.y(), 0.0), h - side)
        return QRectF(x, y, side, side)

    def _corner_at(self, pos: QPointF) -> str | None:
        wr = self._src_to_widget(self._crop)
        pts = {
            "nw": wr.topLeft(), "ne": wr.topRight(),
            "sw": wr.bottomLeft(), "se": wr.bottomRight(),
        }
        for name, pt in pts.items():
            if abs(pos.x() - pt.x()) <= HANDLE_HIT and abs(pos.y() - pt.y()) <= HANDLE_HIT:
                return name
        return None

    # ----------------------------------------------------------------- draw
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#121214"))

        if self._display_pixmap is None or self._source is None:
            painter.setPen(QColor("#8A8A90"))
            font = painter.font()
            font.setFamilies(["Inter", "Segoe UI", "Arial"])
            font.setPointSize(11)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignCenter, "No image loaded")
            return

        image_rect = self._image_rect()
        painter.drawPixmap(image_rect, self._display_pixmap, QRectF(self._display_pixmap.rect()))

        crop_rect = self._src_to_widget(self._crop)

        # Dim everything outside the crop square (even-odd fill punches the hole).
        shade = QPainterPath()
        shade.addRect(image_rect)
        shade.addRect(crop_rect)
        shade.setFillRule(Qt.OddEvenFill)
        painter.fillPath(shade, QColor(0, 0, 0, 120))

        # Rule-of-thirds guides inside the crop.
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        for i in (1, 2):
            x = crop_rect.left() + crop_rect.width() * i / 3
            y = crop_rect.top() + crop_rect.height() * i / 3
            painter.drawLine(QPointF(x, crop_rect.top()), QPointF(x, crop_rect.bottom()))
            painter.drawLine(QPointF(crop_rect.left(), y), QPointF(crop_rect.right(), y))

        # Border + corner handles.
        painter.setPen(QPen(QColor("#F2F2F5"), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(crop_rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#F2F2F5"))
        for pt in (crop_rect.topLeft(), crop_rect.topRight(),
                   crop_rect.bottomLeft(), crop_rect.bottomRight()):
            painter.drawRect(
                QRectF(pt.x() - HANDLE_VISUAL / 2, pt.y() - HANDLE_VISUAL / 2,
                       HANDLE_VISUAL, HANDLE_VISUAL)
            )

    # ------------------------------------------------------------- mouse
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or self._source is None:
            return
        pos = event.position()
        self._drag_last = pos
        corner = self._corner_at(pos)
        if corner:
            self._mode = corner
        elif self._src_to_widget(self._crop).contains(pos):
            self._mode = "move"
        else:
            self._mode = None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._source is None:
            return
        pos = event.position()
        if self._mode is None:
            self._update_hover_cursor(pos)
            return
        if self._mode == "move":
            delta = pos - self._drag_last
            s = self._scale() or 1.0
            moved = QRectF(self._crop)
            moved.translate(delta.x() / s, delta.y() / s)
            self._crop = self._clamp_into_image(moved)
        else:
            self._resize_to(pos)
            self.crop_changed.emit(self.crop_fraction())
        self._drag_last = pos
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._mode = None
            self._update_hover_cursor(event.position())

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._source is None:
            event.ignore()
            return
        steps = event.angleDelta().y() / 120
        self.set_crop_fraction(self.crop_fraction() * (0.9 ** steps))
        self.crop_changed.emit(self.crop_fraction())

    def _resize_to(self, pos: QPointF) -> None:
        w, h = self._source.size
        cur = self._widget_to_src(pos)
        left, top, right, bottom = (
            self._crop.left(), self._crop.top(), self._crop.right(), self._crop.bottom()
        )
        mode = self._mode
        if mode == "se":       # anchored at the top-left corner
            anchor_x, anchor_y = left, top
            side = max(cur.x() - left, cur.y() - top)
            max_side = min(w - left, h - top)
        elif mode == "nw":     # anchored at the bottom-right corner
            anchor_x, anchor_y = right, bottom
            side = max(right - cur.x(), bottom - cur.y())
            max_side = min(right, bottom)
        elif mode == "ne":     # anchored at the bottom-left corner
            anchor_x, anchor_y = left, bottom
            side = max(cur.x() - left, bottom - cur.y())
            max_side = min(w - left, bottom)
        else:                  # "sw", anchored at the top-right corner
            anchor_x, anchor_y = right, top
            side = max(right - cur.x(), cur.y() - top)
            max_side = min(right, h - top)

        side = max(MIN_SIDE_SRC, min(side, max_side))
        if mode == "se":
            self._crop = QRectF(anchor_x, anchor_y, side, side)
        elif mode == "nw":
            self._crop = QRectF(anchor_x - side, anchor_y - side, side, side)
        elif mode == "ne":
            self._crop = QRectF(anchor_x, anchor_y - side, side, side)
        else:  # sw
            self._crop = QRectF(anchor_x - side, anchor_y, side, side)

    def _update_hover_cursor(self, pos: QPointF) -> None:
        corner = self._corner_at(pos)
        if corner in ("nw", "se"):
            self.setCursor(Qt.SizeFDiagCursor)
        elif corner in ("ne", "sw"):
            self.setCursor(Qt.SizeBDiagCursor)
        elif self._src_to_widget(self._crop).contains(pos):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # ---------------------------------------------------------------- export
    def export_square(self, size: int) -> Image.Image:
        """Crops the current square out of the full-resolution source and
        resizes it to size x size."""
        if self._source is None:
            raise ValueError("No image loaded.")
        w, h = self._source.size
        side = round(min(self._crop.width(), w, h))
        left = round(min(max(self._crop.left(), 0.0), w - side))
        top = round(min(max(self._crop.top(), 0.0), h - side))
        cropped = self._source.crop((left, top, min(w, left + side), min(h, top + side)))
        if cropped.size != (size, size):
            cropped = cropped.resize((size, size), Image.LANCZOS)
        return cropped
