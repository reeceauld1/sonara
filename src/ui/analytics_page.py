"""Analytics tab: lists the signed-in channel's uploads with thumbnails,
sortable by any column, so you can click "Views" or "Engagement" to see which
titles did best. Private videos are hidden by default (most channels have a
pile of private drafts/works-in-progress that would otherwise dominate the
list) with a toggle to reveal them."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import youtube_auth
from core.youtube_discovery import Discovery
from core.youtube_stats import VideoStats
from ui.workers import AnalyticsWorker, run_worker_in_thread

COLUMNS = [
    "",
    "Title",
    "Published",
    "Views",
    "Likes",
    "Comments",
    "Engagement",
    "Discovered via",
    "Privacy",
]
THUMB_SIZE = QSize(80, 45)  # 16:9, matches the render's own canvas shape
ROW_HEIGHT = 54

# QSS's `color` on QTableWidget::item is unreliable in Qt (a documented
# limitation of item-view styling) — text color has to be set per item
# directly instead, or it silently falls back to a mismatched default.
TEXT_COLOR = QColor("#ECECEF")
MUTED_COLOR = QColor("#7A7A80")


def _text_item(text: str, muted: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setForeground(MUTED_COLOR if muted else TEXT_COLOR)
    return item


class _NumericItem(QTableWidgetItem):
    """Sorts by an underlying number instead of the displayed string."""

    def __init__(self, value: float, text: str):
        super().__init__(text)
        self._value = value
        self.setForeground(TEXT_COLOR)

    def __lt__(self, other) -> bool:  # noqa: D105
        if isinstance(other, _NumericItem):
            return self._value < other._value
        return super().__lt__(other)


def _pixmap_from_bytes(data: bytes | None, size: QSize) -> QPixmap:
    pixmap = QPixmap()
    if data:
        pixmap.loadFromData(data)
    if pixmap.isNull():
        pixmap = QPixmap(size)
        pixmap.fill(QColor("#26262B"))
        return pixmap
    return pixmap.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)


class AnalyticsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._thread = None
        self._worker = None
        self._stats: list[VideoStats] = []
        self._thumbnails: dict[str, bytes] = {}
        self._discovery: dict[str, Discovery] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Your videos")
        title.setObjectName("sectionDivider")
        header.addWidget(title)
        header.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        header.addWidget(self.status_label)
        self.show_private_check = QCheckBox("Show private videos")
        self.show_private_check.toggled.connect(self._apply_filter)
        header.addWidget(self.show_private_check)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.highlight_card = self._build_highlight_card()
        self.highlight_card.setVisible(False)
        layout.addWidget(self.highlight_card)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setIconSize(THUMB_SIZE)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, THUMB_SIZE.width() + 16)
        header_view.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in range(2, len(COLUMNS)):
            header_view.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        hint = QLabel(
            "Sorted by views by default. Click any column header to re-sort, "
            "for example by Engagement to see which titles did best relative to their views."
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _build_highlight_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("highlightCard")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 16, 12)
        card_layout.setSpacing(14)

        self.highlight_thumb = QLabel()
        self.highlight_thumb.setFixedSize(120, 68)
        self.highlight_thumb.setScaledContents(True)
        card_layout.addWidget(self.highlight_thumb)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        eyebrow = QLabel("Top performer")
        eyebrow.setObjectName("sectionDivider")
        text_col.addWidget(eyebrow)
        self.highlight_title = QLabel("")
        self.highlight_title.setObjectName("highlightTitle")
        self.highlight_title.setWordWrap(True)
        text_col.addWidget(self.highlight_title)
        self.highlight_stats = QLabel("")
        self.highlight_stats.setObjectName("hintLabel")
        text_col.addWidget(self.highlight_stats)
        text_col.addStretch(1)
        card_layout.addLayout(text_col, 1)

        return card

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._loaded:
            self.refresh()

    def refresh(self) -> None:
        if not youtube_auth.load_cached_credentials():
            self.status_label.setText("Sign in on the Publish tab first")
            return

        self._loaded = True
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("Loading…")

        worker = AnalyticsWorker()
        thread = run_worker_in_thread(worker, self)
        worker.finished.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.start()
        self._thread = thread
        self._worker = worker

    def _on_loaded(
        self,
        stats: list[VideoStats],
        thumbnails: dict[str, bytes],
        discovery: dict[str, Discovery],
    ) -> None:
        self.refresh_btn.setEnabled(True)
        self._stats = stats
        self._thumbnails = thumbnails
        self._discovery = discovery
        self._apply_filter()

    def _apply_filter(self) -> None:
        show_private = self.show_private_check.isChecked()
        visible = [v for v in self._stats if show_private or v.privacy_status != "private"]

        hidden_count = len(self._stats) - len(visible)
        if not self._stats:
            self.status_label.setText("No videos found yet")
        elif hidden_count:
            self.status_label.setText(f"{len(visible)} shown, {hidden_count} private hidden")
        else:
            self.status_label.setText(f"{len(visible)} video{'s' if len(visible) != 1 else ''}")

        self._populate_table(visible)
        self._update_highlight(visible)

    def _populate_table(self, videos: list[VideoStats]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(videos))
        for row, v in enumerate(videos):
            thumb_item = QTableWidgetItem()
            thumb_item.setIcon(QIcon(_pixmap_from_bytes(self._thumbnails.get(v.video_id), THUMB_SIZE)))
            thumb_item.setFlags(thumb_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, thumb_item)

            published_text, published_value = self._format_published(v.published_at)
            engagement = v.engagement_rate
            discovery = self._discovery.get(v.video_id)
            if discovery is None:
                discovered_text, discovered_muted = "-", True
            elif discovery.search_term:
                discovered_text, discovered_muted = f'{discovery.source_label}: "{discovery.search_term}"', False
            else:
                discovered_text, discovered_muted = discovery.source_label, False

            self.table.setItem(row, 1, _text_item(v.title))
            self.table.setItem(row, 2, _NumericItem(published_value, published_text))
            self.table.setItem(row, 3, _NumericItem(v.views, f"{v.views:,}"))
            self.table.setItem(row, 4, _NumericItem(v.likes, f"{v.likes:,}"))
            self.table.setItem(row, 5, _NumericItem(v.comments, f"{v.comments:,}"))
            self.table.setItem(row, 6, _NumericItem(engagement, f"{engagement:.1f}%"))
            self.table.setItem(row, 7, _text_item(discovered_text, muted=discovered_muted))
            self.table.setItem(row, 8, _text_item(v.privacy_status.title(), muted=v.privacy_status == "private"))

        self.table.setSortingEnabled(True)
        self.table.sortItems(3, Qt.DescendingOrder)  # most views first, by default

    def _update_highlight(self, videos: list[VideoStats]) -> None:
        best = max(videos, key=lambda v: v.views, default=None)
        if best is None or best.views == 0:
            self.highlight_card.setVisible(False)
            return

        self.highlight_card.setVisible(True)
        self.highlight_thumb.setPixmap(_pixmap_from_bytes(self._thumbnails.get(best.video_id), QSize(120, 68)))
        self.highlight_title.setText(best.title)
        self.highlight_stats.setText(
            f"{best.views:,} views, {best.likes:,} likes, "
            f"{best.engagement_rate:.1f}% engagement"
        )

    @staticmethod
    def _format_published(published_at: str) -> tuple[str, float]:
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return published_at, 0.0
        return dt.strftime("%d %b %Y"), dt.timestamp()

    def _on_failed(self, message: str) -> None:
        self.refresh_btn.setEnabled(True)
        self.status_label.setText("Failed to load")
        QMessageBox.critical(self, "Analytics failed", message)
