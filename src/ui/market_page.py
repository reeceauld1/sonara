"""Trends tab: what other channels in the scene are posting right now: which
artist names keep showing up together in titles, and which names show up most.
Scan-only; uses no account data beyond the API token.

(More sections to come; page layout is intentionally simple for now.)"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from core import type_beat, youtube_auth
from core.config import load_config, update_config
from ui.widgets import NoScrollComboBox
from ui.workers import MarketWorker, run_worker_in_thread


class MarketPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._thread = None
        self._worker = None
        self._scene = load_config().get("suggestions_scene") or type_beat.DEFAULT_SCENE
        self._build_ui()

    # ------------------------------------------------------------------ ui
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("What others are posting")
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
            "Recent public type-beat uploads from other channels in the scene. "
            "\"Seen together\" counts how often two artist names share one title."
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(12)

        pairs_col = QVBoxLayout()
        pairs_col.setSpacing(6)
        pairs_label = QLabel("Seen together on other channels")
        pairs_label.setObjectName("sectionDivider")
        pairs_col.addWidget(pairs_label)
        self.pairs_table = self._make_table(["Name", "With", "Titles"])
        pairs_col.addWidget(self.pairs_table, 1)
        body.addLayout(pairs_col, 3)

        artists_col = QVBoxLayout()
        artists_col.setSpacing(6)
        artists_label = QLabel("Most posted")
        artists_label.setObjectName("sectionDivider")
        artists_col.addWidget(artists_label)
        self.artists_table = self._make_table(["Artist", "Uploads"])
        artists_col.addWidget(self.artists_table, 1)
        body.addLayout(artists_col, 2)

        layout.addLayout(body, 1)

    def _make_table(self, columns: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)
        head = table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(columns)):
            head.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        return table

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
        self.status_label.setText("Scanning other channels…")

        worker = MarketWorker(self._scene)
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

    def _on_loaded(self, scan: type_beat.MarketScan) -> None:
        self.status_label.setText(
            f"{scan.scanned} recent upload{'s' if scan.scanned != 1 else ''} scanned"
        )
        self._fill(self.pairs_table, [(a, b, n) for a, b, n in scan.pairs], numeric_cols=(2,))
        self._fill(self.artists_table, scan.artists, numeric_cols=(1,))

    def _on_failed(self, message: str) -> None:
        self.status_label.setText("Failed to scan")
        QMessageBox.critical(self, "Trends failed", message)

    def _fill(self, table: QTableWidget, rows: list[tuple], numeric_cols: tuple) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem()
                if c in numeric_cols:
                    item.setData(Qt.DisplayRole, int(value))
                else:
                    item.setText(str(value))
                table.setItem(r, c, item)
        table.setSortingEnabled(True)
        if rows and numeric_cols:
            table.sortItems(numeric_cols[0], Qt.DescendingOrder)
