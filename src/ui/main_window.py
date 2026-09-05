from __future__ import annotations

import sys
import tempfile
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import updater, youtube_auth
from core.audio_tag import AudioTagSpec
from core.config import load_config, update_config
from core.version import APP_VERSION
from core.video import WATERMARK_MAX_SIZE, WATERMARK_MIN_SIZE, Watermark
from core.youtube_upload import UploadRequest
from ui.analytics_page import AnalyticsPage
from ui.market_page import MarketPage
from ui.settings_dialog import SettingsDialog
from ui.suggestions_page import SuggestionsPage
from ui.widgets import NoScrollComboBox, NoScrollDateEdit, PresetBar, VideoPreview
from ui.workers import (
    PublishJob,
    PublishWorker,
    SignInWorker,
    UpdateCheckWorker,
    UpdateDownloadWorker,
    run_worker_in_thread,
)

CATEGORIES = [
    ("Music", "10"),
    ("Entertainment", "24"),
    ("People & Blogs", "22"),
    ("Film & Animation", "1"),
    ("Comedy", "23"),
    ("Education", "27"),
]

WATERMARK_CORNERS = [
    ("Top left", "top-left"),
    ("Top right", "top-right"),
    ("Bottom left", "bottom-left"),
    ("Bottom right", "bottom-right"),
]

AUDIO_FILTER = "Audio files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg)"
IMAGE_FILTER = "Image files (*.png *.jpg *.jpeg *.bmp)"

TIME_SLOTS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]


def _default_schedule() -> tuple[date, str]:
    """Next 15-minute slot at least 20 minutes out, so it's never a past time."""
    buffered = datetime.now() + timedelta(minutes=20)
    total_minutes = buffered.hour * 60 + buffered.minute
    rounded = ((total_minutes // 15) + 1) * 15
    extra_days, rounded = divmod(rounded, 24 * 60)
    slot_date = buffered.date() + timedelta(days=extra_days)
    slot_time = f"{rounded // 60:02d}:{rounded % 60:02d}"
    return slot_date, slot_time


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sonara")
        self.setMinimumSize(920, 560)
        self._size_to_screen_fraction(0.7)

        self._thread = None
        self._worker = None
        self._work_dir = Path(tempfile.mkdtemp(prefix="sonara_"))

        self._build_ui()
        self._restore_last_used()
        self._refresh_account_state()
        self._fit_to_content_height()
        self._check_for_updates()

    def _size_to_screen_fraction(self, fraction: float) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1080, 900)
            return
        available = screen.availableGeometry()
        width = int(available.width() * fraction)
        height = int(available.height() * fraction)
        self.resize(width, height)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + (available.height() - height) // 2,
        )

    def _fit_to_content_height(self) -> None:
        """Grow the window's height (capped at the screen) until the form
        fits without needing to scroll."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        scroll = self._scroll_area
        self.show()
        QApplication.processEvents()
        max_height = available.height()
        height = self.height()
        while height < max_height and scroll.verticalScrollBar().maximum() > 0:
            height = min(height + 30, max_height)
            self.resize(self.width(), height)
            QApplication.processEvents()
        self.move(
            available.x() + max(0, (available.width() - self.width()) // 2),
            available.y() + max(0, (available.height() - self.height()) // 2),
        )

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    # ------------------------------------------------------------------ ui
    def _build_ui(self) -> None:
        tabs = QTabWidget()
        tabs.setDocumentMode(True)  # flat tab bar, no light Fusion base strip
        self._tabs = tabs
        self.setCentralWidget(tabs)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.clicked.connect(self._open_settings)
        tabs.setCornerWidget(self.settings_btn, Qt.TopRightCorner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.viewport().setObjectName("scrollViewport")
        self._scroll_area = scroll
        tabs.addTab(scroll, "Publish")

        self.analytics_page = AnalyticsPage()
        tabs.addTab(self.analytics_page, "Analytics")

        self.suggestions_page = SuggestionsPage()
        self.suggestions_page.use_title.connect(self._apply_suggested_title)
        tabs.addTab(self.suggestions_page, "Suggestions")

        self.market_page = MarketPage()
        tabs.addTab(self.market_page, "Trends")

        root = QWidget()
        root.setObjectName("rootCanvas")
        scroll.setWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 14)
        outer.setSpacing(8)

        # --- header: brand on the left, Google account inline on the right
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        brand = QLabel("Sonara")
        brand.setObjectName("brandTitle")
        title_row.addWidget(brand)
        title_row.addStretch(1)
        self.account_label = QLabel("Not signed in")
        self.account_label.setObjectName("accountLabel")
        title_row.addWidget(self.account_label)
        self.account_btn = QPushButton("Connect Account")
        self.account_btn.clicked.connect(self._on_account_button)
        title_row.addWidget(self.account_btn)

        header = QVBoxLayout()
        header.setSpacing(1)
        header.addLayout(title_row)
        subtitle = QLabel("Turn a track into a video and upload it to YouTube")
        subtitle.setObjectName("brandSubtitle")
        header.addWidget(subtitle)
        outer.addLayout(header)

        # --- landscape body: preview+media on the left, details on the right
        body = QHBoxLayout()
        body.setSpacing(10)
        body.addWidget(self._build_media_column(), 5)
        body.addWidget(self._build_details_column(), 6)
        outer.addLayout(body, 1)

        # --- status (full width, only holds text while a publish is running)
        self.stage_label = QLabel("")
        self.stage_label.setObjectName("stageLabel")
        self.stage_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.stage_label)

        # --- publish (centered, 50% of the window's width)
        publish_row = QHBoxLayout()
        publish_row.addStretch(1)
        self.publish_btn = QPushButton("Publish to YouTube")
        self.publish_btn.setObjectName("primaryButton")
        self.publish_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.publish_btn.setMinimumHeight(46)
        self.publish_btn.setCursor(Qt.PointingHandCursor)
        self.publish_btn.clicked.connect(self._on_publish)
        publish_row.addWidget(self.publish_btn, 2)
        publish_row.addStretch(1)
        outer.addLayout(publish_row)

    def _build_media_column(self) -> QWidget:
        box = QGroupBox("Preview and media")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        self.preview = VideoPreview()
        layout.addWidget(self.preview)

        form = QFormLayout()
        form.setSpacing(6)
        self.audio_edit, audio_row = self._file_row(AUDIO_FILTER)
        self.image_edit, image_row = self._file_row(IMAGE_FILTER, on_pick=self.preview.set_image)
        form.addRow("Audio file", audio_row)
        form.addRow("Cover image", image_row)

        divider = QLabel("Watermark")
        divider.setObjectName("sectionDivider")
        form.addRow(divider)

        self.watermark_text_edit = QLineEdit()
        self.watermark_text_edit.setPlaceholderText("optional, e.g. yourchannel.com")
        self.watermark_text_edit.setMaxLength(80)
        self.watermark_text_edit.textChanged.connect(self._update_watermark_preview)
        form.addRow("Text", self.watermark_text_edit)

        self.watermark_corner_combo = NoScrollComboBox()
        for label, _value in WATERMARK_CORNERS:
            self.watermark_corner_combo.addItem(label)
        self.watermark_corner_combo.setCurrentIndex(3)  # bottom-right
        self.watermark_corner_combo.currentIndexChanged.connect(self._update_watermark_preview)
        form.addRow("Position", self.watermark_corner_combo)

        size_row = QWidget()
        size_row_layout = QHBoxLayout(size_row)
        size_row_layout.setContentsMargins(0, 0, 0, 0)
        size_row_layout.setSpacing(8)
        self.watermark_size_spin = QSpinBox()
        self.watermark_size_spin.setRange(WATERMARK_MIN_SIZE, WATERMARK_MAX_SIZE)
        self.watermark_size_spin.setValue(16)
        self.watermark_size_spin.setSuffix(" px")
        self.watermark_size_spin.valueChanged.connect(self._update_watermark_preview)
        size_row_layout.addWidget(self.watermark_size_spin)
        size_hint = QLabel(f"max {WATERMARK_MAX_SIZE}px")
        size_hint.setObjectName("hintLabel")
        size_row_layout.addWidget(size_hint)
        size_row_layout.addStretch(1)
        form.addRow("Size", size_row)

        audio_tag_divider = QLabel("Audio tag")
        audio_tag_divider.setObjectName("sectionDivider")
        form.addRow(audio_tag_divider)

        self.audio_tag_check = QCheckBox("Repeat a tag through the beat (anti-theft)")
        self.audio_tag_check.toggled.connect(self._update_audio_tag_enabled)
        form.addRow("", self.audio_tag_check)

        self.audio_tag_file_edit, audio_tag_file_row = self._file_row(AUDIO_FILTER)
        self.audio_tag_file_label = QLabel("Tag 1 audio")
        form.addRow(self.audio_tag_file_label, audio_tag_file_row)

        tag_timing_row = QWidget()
        tag_timing_layout = QHBoxLayout(tag_timing_row)
        tag_timing_layout.setContentsMargins(0, 0, 0, 0)
        tag_timing_layout.setSpacing(8)
        self.audio_tag_bpm_spin = QSpinBox()
        self.audio_tag_bpm_spin.setRange(40, 300)
        self.audio_tag_bpm_spin.setValue(140)
        self.audio_tag_bpm_spin.setSuffix(" BPM")
        tag_timing_layout.addWidget(self.audio_tag_bpm_spin)
        self.audio_tag_bars_spin = QSpinBox()
        self.audio_tag_bars_spin.setRange(1, 64)
        self.audio_tag_bars_spin.setValue(8)
        self.audio_tag_bars_spin.setSuffix(" bars")
        tag_timing_layout.addWidget(self.audio_tag_bars_spin)
        tag_timing_layout.addStretch(1)
        self.audio_tag_timing_label = QLabel("Every")
        form.addRow(self.audio_tag_timing_label, tag_timing_row)

        self.audio_tag_second_check = QCheckBox(
            "Alternate with a second tag (tag 1, tag 2, tag 1, …)"
        )
        self.audio_tag_second_check.toggled.connect(self._update_audio_tag_enabled)
        form.addRow("", self.audio_tag_second_check)

        self.audio_tag_second_file_edit, audio_tag_second_file_row = self._file_row(AUDIO_FILTER)
        self.audio_tag_second_file_label = QLabel("Tag 2 audio")
        form.addRow(self.audio_tag_second_file_label, audio_tag_second_file_row)

        self._audio_tag_primary_widgets = [
            self.audio_tag_file_label, audio_tag_file_row,
            self.audio_tag_timing_label, tag_timing_row,
            self.audio_tag_second_check,
        ]
        self._audio_tag_secondary_widgets = [
            self.audio_tag_second_file_label, audio_tag_second_file_row,
        ]
        self._update_audio_tag_enabled()

        layout.addLayout(form)
        layout.addStretch(1)
        return box

    def _update_audio_tag_enabled(self) -> None:
        primary_on = self.audio_tag_check.isChecked()
        for widget in self._audio_tag_primary_widgets:
            widget.setEnabled(primary_on)
        secondary_on = primary_on and self.audio_tag_second_check.isChecked()
        for widget in self._audio_tag_secondary_widgets:
            widget.setEnabled(secondary_on)

    def _update_watermark_preview(self) -> None:
        corner = WATERMARK_CORNERS[self.watermark_corner_combo.currentIndex()][1]
        self.preview.set_watermark(
            self.watermark_text_edit.text(), corner, self.watermark_size_spin.value()
        )

    def _build_details_column(self) -> QWidget:
        meta_box = QGroupBox("Video details")
        meta_form = QFormLayout(meta_box)
        meta_form.setSpacing(6)
        meta_form.addRow("Title", self._build_title_field())
        meta_form.addRow("Description", self._build_description_field())

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("comma, separated, tags")
        meta_form.addRow("Tags", self.tags_edit)

        self.category_combo = NoScrollComboBox()
        for name, _id in CATEGORIES:
            self.category_combo.addItem(name)
        meta_form.addRow("Category", self.category_combo)

        self.privacy_combo = NoScrollComboBox()
        self.privacy_combo.addItems(["Public", "Unlisted", "Private"])
        meta_form.addRow("Privacy", self.privacy_combo)

        self.thumbnail_check = QCheckBox("Use cover image as video thumbnail")
        self.thumbnail_check.setChecked(True)
        meta_form.addRow("", self.thumbnail_check)

        divider = QLabel("Schedule")
        divider.setObjectName("sectionDivider")
        meta_form.addRow(divider)
        meta_form.addRow(self._build_schedule_row())
        return meta_box

    # ------------------------------------------- title / description fields
    def _build_title_field(self) -> QWidget:
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(100)
        col.addWidget(self.title_edit)
        self.title_presets = PresetBar(
            "title_presets", self.title_edit.text, self.title_edit.setText, noun="title"
        )
        col.addWidget(self.title_presets)
        return container

    def _build_description_field(self) -> QWidget:
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        self.description_edit = QTextEdit()
        self.description_edit.setMinimumHeight(90)
        self.description_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        col.addWidget(self.description_edit)
        self.description_presets = PresetBar(
            "description_presets",
            self.description_edit.toPlainText,
            self.description_edit.setPlainText,
            noun="description",
        )
        col.addWidget(self.description_presets)
        return container

    def _restore_last_used(self) -> None:
        cfg = load_config()
        if cfg.get("title_last"):
            self.title_edit.setText(cfg["title_last"])
        if cfg.get("description_last"):
            self.description_edit.setPlainText(cfg["description_last"])

    def _remember_last_used(self) -> None:
        update_config(
            title_last=self.title_edit.text(),
            description_last=self.description_edit.toPlainText(),
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._remember_last_used()
        super().closeEvent(event)

    def _build_schedule_row(self) -> QWidget:
        row = QWidget()
        schedule_layout = QHBoxLayout(row)
        schedule_layout.setContentsMargins(0, 4, 0, 0)
        schedule_layout.setSpacing(10)

        self.schedule_check = QCheckBox("Schedule for later")
        self.schedule_check.toggled.connect(self._on_schedule_toggled)
        schedule_layout.addWidget(self.schedule_check)

        schedule_layout.addStretch(1)

        self.schedule_date_label = QLabel("Date")
        self.schedule_date_label.setEnabled(False)
        schedule_layout.addWidget(self.schedule_date_label)
        default_date, default_time = _default_schedule()
        self.schedule_date = NoScrollDateEdit(
            QDate(default_date.year, default_date.month, default_date.day)
        )
        self.schedule_date.setCalendarPopup(True)
        self.schedule_date.setMinimumDate(QDate.currentDate())
        self.schedule_date.setDisplayFormat("dd MMM yyyy")
        self.schedule_date.setMinimumWidth(165)
        self.schedule_date.setEnabled(False)
        schedule_layout.addWidget(self.schedule_date)

        self.schedule_time_label = QLabel("Time")
        self.schedule_time_label.setEnabled(False)
        schedule_layout.addWidget(self.schedule_time_label)
        self.schedule_time = NoScrollComboBox()
        self.schedule_time.addItems(TIME_SLOTS)
        self.schedule_time.setCurrentText(default_time)
        self.schedule_time.setMinimumWidth(85)
        self.schedule_time.setEnabled(False)
        schedule_layout.addWidget(self.schedule_time)

        return row

    def _file_row(self, file_filter: str, on_pick=None):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        edit.setReadOnly(True)
        browse = QPushButton("Browse")
        browse.setMinimumWidth(72)
        browse.clicked.connect(lambda: self._browse_file(edit, file_filter, on_pick))
        row_layout.addWidget(edit, 1)
        row_layout.addWidget(browse)
        return edit, row

    def _browse_file(self, edit: QLineEdit, file_filter: str, on_pick=None) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select file", "", file_filter)
        if path:
            edit.setText(path)
            if on_pick:
                on_pick(path)

    def _on_schedule_toggled(self, checked: bool) -> None:
        self.schedule_date.setEnabled(checked)
        self.schedule_time.setEnabled(checked)
        self.schedule_date_label.setEnabled(checked)
        self.schedule_time_label.setEnabled(checked)
        self.privacy_combo.setEnabled(not checked)
        if checked:
            self.privacy_combo.setCurrentText("Private")

    def _apply_suggested_title(self, title: str) -> None:
        self.title_edit.setText(title[:100])
        self._tabs.setCurrentIndex(0)
        self.title_edit.setFocus()
        self.title_edit.setCursorPosition(len(self.title_edit.text()))

    # ------------------------------------------------------------- account
    def _refresh_account_state(self) -> None:
        creds = youtube_auth.load_cached_credentials()
        if creds:
            try:
                summary = youtube_auth.get_channel_summary(creds)
                self.account_label.setText(f"Signed in as: {summary['title']}")
                self.account_btn.setText("Sign Out")
                return
            except Exception:
                pass
        self.account_label.setText("Not signed in")
        self.account_btn.setText("Connect Account")

    def _on_account_button(self) -> None:
        if self.account_btn.text() == "Sign Out":
            youtube_auth.sign_out()
            self._refresh_account_state()
            return

        if not youtube_auth.has_client_secrets():
            QMessageBox.warning(
                self,
                "Credentials required",
                "Import your Google OAuth client_secret.json in Settings first.",
            )
            self._open_settings()
            return

        self.account_btn.setEnabled(False)
        self.account_label.setText("Signing in… (check your browser)")
        worker = SignInWorker()
        thread = run_worker_in_thread(worker, self)
        worker.finished.connect(self._on_sign_in_finished)
        worker.failed.connect(self._on_sign_in_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.start()
        self._sign_in_thread = thread
        self._sign_in_worker = worker

    def _on_sign_in_finished(self, summary: dict) -> None:
        self.account_btn.setEnabled(True)
        self.account_label.setText(f"Signed in as: {summary['title']}")
        self.account_btn.setText("Sign Out")

    def _on_sign_in_failed(self, message: str) -> None:
        self.account_btn.setEnabled(True)
        self._refresh_account_state()
        QMessageBox.critical(self, "Sign-in failed", message)

    # ------------------------------------------------------------- publish
    def _validate(self) -> str | None:
        if not self.audio_edit.text():
            return "Choose an audio file."
        if not self.image_edit.text():
            return "Choose a cover image."
        if not self.title_edit.text().strip():
            return "Enter a title."
        if not youtube_auth.has_client_secrets():
            return "Import your Google OAuth credentials in Settings first."
        if self.audio_tag_check.isChecked():
            if not self.audio_tag_file_edit.text():
                return "Choose a tag audio file, or uncheck the audio tag option."
            if self.audio_tag_second_check.isChecked() and not self.audio_tag_second_file_edit.text():
                return "Choose a second tag audio file, or uncheck the second tag option."
        return None

    def _on_publish(self) -> None:
        error = self._validate()
        if error:
            QMessageBox.warning(self, "Missing information", error)
            return

        self._remember_last_used()
        category_id = CATEGORIES[self.category_combo.currentIndex()][1]
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]

        publish_at = None
        privacy = self.privacy_combo.currentText().lower()
        if self.schedule_check.isChecked():
            qd = self.schedule_date.date()
            hh, mm = (int(part) for part in self.schedule_time.currentText().split(":"))
            publish_at = datetime(qd.year(), qd.month(), qd.day(), hh, mm)
            if publish_at <= datetime.now() + timedelta(seconds=30):
                QMessageBox.warning(self, "Invalid schedule", "Pick a time in the future.")
                return

        req = UploadRequest(
            video_path="",  # filled in by the worker after rendering
            title=self.title_edit.text().strip(),
            description=self.description_edit.toPlainText(),
            tags=tags,
            category_id=category_id,
            privacy_status=privacy,
            publish_at=publish_at,
        )
        watermark = None
        if self.watermark_text_edit.text().strip():
            corner = WATERMARK_CORNERS[self.watermark_corner_combo.currentIndex()][1]
            watermark = Watermark(
                text=self.watermark_text_edit.text().strip(),
                corner=corner,
                size=self.watermark_size_spin.value(),
            )

        audio_tag = None
        if self.audio_tag_check.isChecked():
            second_tag_path = None
            if self.audio_tag_second_check.isChecked():
                second_tag_path = self.audio_tag_second_file_edit.text()
            audio_tag = AudioTagSpec(
                tag_path=self.audio_tag_file_edit.text(),
                bpm=self.audio_tag_bpm_spin.value(),
                bars_per_tag=self.audio_tag_bars_spin.value(),
                second_tag_path=second_tag_path,
            )

        job = PublishJob(
            audio_path=self.audio_edit.text(),
            image_path=self.image_edit.text(),
            upload_request=req,
            set_custom_thumbnail=self.thumbnail_check.isChecked(),
            watermark=watermark,
            audio_tag=audio_tag,
        )

        self.publish_btn.setEnabled(False)
        self._current_stage = "Starting…"
        self.stage_label.setText(self._current_stage)

        worker = PublishWorker(job, self._work_dir)
        thread = run_worker_in_thread(worker, self)
        worker.stage_changed.connect(self._on_stage_changed)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_publish_finished)
        worker.failed.connect(self._on_publish_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.start()
        self._thread = thread
        self._worker = worker

    def _on_stage_changed(self, stage: str) -> None:
        self._current_stage = stage
        self.stage_label.setText(self._current_stage)

    def _on_progress(self, fraction: float) -> None:
        self.stage_label.setText(f"{self._current_stage} ({int(fraction * 100)}%)")

    def _on_publish_finished(self, video_id: str) -> None:
        self.publish_btn.setEnabled(True)
        self.stage_label.setText("Done")
        url = f"https://youtu.be/{video_id}"
        reply = QMessageBox.information(
            self,
            "Published",
            f"Upload complete.\n\n{url}",
            QMessageBox.Ok | QMessageBox.Open,
        )
        if reply == QMessageBox.Open:
            webbrowser.open(url)

    def _on_publish_failed(self, message: str) -> None:
        self.publish_btn.setEnabled(True)
        self.stage_label.setText("Upload failed")
        QMessageBox.critical(self, "Publish failed", message)

    # -------------------------------------------------------------- update
    def _check_for_updates(self) -> None:
        if not getattr(sys, "frozen", False):
            return  # self-update only makes sense for the packaged .exe
        worker = UpdateCheckWorker()
        thread = run_worker_in_thread(worker, self)
        worker.found.connect(self._on_update_found)
        worker.found.connect(thread.quit)
        worker.none_found.connect(thread.quit)
        worker.failed.connect(thread.quit)  # a failed check is silent, never bothers the user
        thread.start()
        self._update_check_thread = thread
        self._update_check_worker = worker

    def _on_update_found(self, info: updater.UpdateInfo) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Update available")
        notes = f"\n\n{info.notes}" if info.notes else ""
        box.setText(f"Sonara v{info.version} is available (you have v{APP_VERSION}).{notes}")
        update_btn = box.addButton("Update Now", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is update_btn:
            self._start_update_download(info)

    def _start_update_download(self, info: updater.UpdateInfo) -> None:
        dialog = QProgressDialog("Downloading update…", None, 0, 100, self)
        dialog.setWindowTitle("Updating Sonara")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setValue(0)
        dialog.show()
        self._update_progress_dialog = dialog

        worker = UpdateDownloadWorker(info)
        thread = run_worker_in_thread(worker, self)
        worker.progress.connect(lambda fraction: dialog.setValue(int(fraction * 100)))
        worker.ready.connect(self._on_update_ready)
        worker.failed.connect(self._on_update_failed)
        worker.ready.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.start()
        self._update_download_thread = thread
        self._update_download_worker = worker

    def _on_update_ready(self, new_exe_path: Path) -> None:
        self._update_progress_dialog.close()
        try:
            updater.apply_update_and_restart(new_exe_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Update failed", str(exc))
            return
        QApplication.quit()

    def _on_update_failed(self, message: str) -> None:
        self._update_progress_dialog.close()
        QMessageBox.warning(self, "Update failed", message)
