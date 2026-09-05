"""Suggestions tab: reads the type-beat titles you've uploaded and proposes
which artists to make a beat for next, with ready-to-paste titles in your own
format. "Use" drops a title straight into the Publish tab."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import type_beat, youtube_auth
from core.config import load_config, update_config
from ui.widgets import NoScrollComboBox
from ui.workers import SuggestionsWorker, run_worker_in_thread

COVERAGE_COLUMNS = ["Artist", "Beats", "Views", "Best-performing title"]


class SuggestionsPage(QWidget):
    use_title = Signal(str)  # a suggested title the user picked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._thread = None
        self._worker = None
        self._scene = (load_config().get("suggestions_scene") or type_beat.DEFAULT_SCENE)
        self._build_ui()

    # ------------------------------------------------------------------ ui
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Who to make next")
        title.setObjectName("sectionDivider")
        header.addWidget(title)
        header.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        header.addWidget(self.status_label)
        header.addWidget(QLabel("Scene"))
        self.scene_combo = NoScrollComboBox()
        for key in type_beat.SCENES:
            self.scene_combo.addItem(key.upper() if len(key) <= 3 else key.title(), key)
        idx = self.scene_combo.findData(self._scene)
        if idx >= 0:
            self.scene_combo.setCurrentIndex(idx)
        self.scene_combo.currentIndexChanged.connect(self._on_scene_changed)
        header.addWidget(self.scene_combo)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(lambda: self.refresh(force=True))
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        hint = QLabel(
            "Reads the type-beat titles on your public uploads and cross-checks "
            "recent uploads from other channels in the scene to spot artists you "
            "have not covered. Suggested titles copy your own format: tag, "
            "separator and quoted hook. See the Trends tab for the full breakdown "
            "of what others are posting."
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(12)

        made_col = QVBoxLayout()
        made_col.setSpacing(6)
        made_label = QLabel("What you've made")
        made_label.setObjectName("sectionDivider")
        made_col.addWidget(made_label)
        self.table = QTableWidget(0, len(COVERAGE_COLUMNS))
        self.table.setHorizontalHeaderLabels(COVERAGE_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.Stretch)
        made_col.addWidget(self.table, 1)
        body.addLayout(made_col, 4)

        next_col = QVBoxLayout()
        next_col.setSpacing(6)
        next_label = QLabel("Try these next")
        next_label.setObjectName("sectionDivider")
        next_col.addWidget(next_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        cards_host = QWidget()
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setContentsMargins(0, 0, 6, 0)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch(1)
        scroll.setWidget(cards_host)
        next_col.addWidget(scroll, 1)
        body.addLayout(next_col, 5)

        layout.addLayout(body, 1)

    # ------------------------------------------------------------- loading
    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._loaded:
            self.refresh()

    def _on_scene_changed(self) -> None:
        self._scene = self.scene_combo.currentData()
        update_config(suggestions_scene=self._scene)
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        if self._thread is not None:
            return
        if not youtube_auth.load_cached_credentials():
            self.status_label.setText("Sign in on the Publish tab first")
            return

        self._loaded = True
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("Reading your uploads…")

        worker = SuggestionsWorker(self._scene)
        thread = run_worker_in_thread(worker, self)
        worker.finished.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_done)
        thread.start()
        self._thread = thread
        self._worker = worker

    def _on_thread_done(self) -> None:
        self._thread = None
        self._worker = None
        self.refresh_btn.setEnabled(True)

    def _on_loaded(self, report: type_beat.SuggestionReport) -> None:
        if report.parsed_count == 0:
            base = "No public type-beat titles found, showing standard suggestions"
        else:
            base = (
                f"{report.parsed_count} public type-beat title"
                f"{'s' if report.parsed_count != 1 else ''}, "
                f"{len(report.coverage)} artist"
                f"{'s' if len(report.coverage) != 1 else ''} covered"
            )
        if report.market_scanned:
            base += f", {report.market_scanned} other uploads scanned"
        else:
            base += ", could not scan other channels"
        self.status_label.setText(base)

        self._populate_table(report.coverage)
        self._populate_cards(report.suggestions)

    def _on_failed(self, message: str) -> None:
        self.status_label.setText("Failed to load")
        QMessageBox.critical(self, "Suggestions failed", message)

    # --------------------------------------------------------------- views
    def _populate_table(self, coverage: list[type_beat.ArtistCoverage]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(coverage))
        for row, c in enumerate(coverage):
            beats = QTableWidgetItem()
            beats.setData(Qt.DisplayRole, c.beat_count)
            views = QTableWidgetItem()
            views.setData(Qt.DisplayRole, c.total_views)
            self.table.setItem(row, 0, QTableWidgetItem(c.name))
            self.table.setItem(row, 1, beats)
            self.table.setItem(row, 2, views)
            self.table.setItem(row, 3, QTableWidgetItem(c.best_title))
        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.DescendingOrder)

    def _populate_cards(self, suggestions: list[type_beat.Suggestion]) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not suggestions:
            empty = QLabel("Nothing to suggest. You've covered the whole seed list.")
            empty.setObjectName("hintLabel")
            empty.setWordWrap(True)
            self._cards_layout.addWidget(empty)
        else:
            for suggestion in suggestions:
                self._cards_layout.addWidget(self._suggestion_card(suggestion))
        self._cards_layout.addStretch(1)

    def _suggestion_card(self, suggestion: type_beat.Suggestion) -> QFrame:
        card = QFrame()
        card.setObjectName("highlightCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 12)
        card_layout.setSpacing(4)

        name = QLabel(suggestion.artist)
        name.setObjectName("highlightTitle")
        name.setWordWrap(True)
        card_layout.addWidget(name)

        reason = QLabel(suggestion.reason)
        reason.setObjectName("hintLabel")
        reason.setWordWrap(True)
        card_layout.addWidget(reason)

        for text in suggestion.titles:
            row = QHBoxLayout()
            row.setSpacing(6)
            label = QLabel(text)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row.addWidget(label, 1)

            copy_btn = QPushButton("Copy")
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.clicked.connect(
                lambda _=False, value=text: QApplication.clipboard().setText(value)
            )
            row.addWidget(copy_btn)

            use_btn = QPushButton("Use")
            use_btn.setCursor(Qt.PointingHandCursor)
            use_btn.clicked.connect(lambda _=False, value=text: self.use_title.emit(value))
            row.addWidget(use_btn)

            card_layout.addLayout(row)

        return card
