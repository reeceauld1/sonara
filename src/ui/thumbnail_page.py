"""Thumbnail tab: load a cover image, optionally AI-upscale it, frame a square
crop, then send that square to the Publish tab as the cover image for the
video you're about to upload - it becomes both the uploaded video's picture
and its custom thumbnail. Nothing is written anywhere permanent; the crop is
saved to a temp file the Publish tab reads at render time."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core import thumbnail as thumbnail_core
from ui.crop_canvas import CropCanvas
from ui.widgets import NoScrollComboBox
from ui.workers import UpscaleWorker, run_worker_in_thread

IMAGE_FILTER = "Image files (*.png *.jpg *.jpeg *.bmp)"
# Above this, tiled CPU inference gets slow enough to warrant a confirmation
# (a 1600px-square source is already several hundred tiles).
UPSCALE_WARN_DIM = 1600


class ThumbnailPage(QWidget):
    send_to_publish = Signal(str)  # path to the exported square cover image

    def __init__(self, parent=None):
        super().__init__(parent)
        self._upscale_thread = None
        self._upscale_worker = None

        self._image_path: str | None = None
        self._current_image: Image.Image | None = None
        self._work_dir = Path(tempfile.mkdtemp(prefix="sonara_thumbstudio_"))
        self._export_seq = 0

        self._build_ui()
        self._update_enabled_state()

    # ------------------------------------------------------------------ ui
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Thumbnail studio")
        title.setObjectName("sectionDivider")
        header.addWidget(title)
        header.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        hint = QLabel(
            "Load a cover image, optionally AI-upscale it, drag the square to frame "
            "the crop, then send it to the Publish tab as your video's picture."
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._build_source_column(), 3)
        body.addWidget(self._build_crop_column(), 5)
        body.addWidget(self._build_target_column(), 3)
        layout.addLayout(body, 1)

    def _build_source_column(self) -> QWidget:
        box = QGroupBox("Source image")
        col = QVBoxLayout(box)
        col.setSpacing(8)

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setCursor(Qt.PointingHandCursor)
        self.browse_btn.clicked.connect(self._browse_image)
        col.addWidget(self.browse_btn)

        self.dims_label = QLabel("No image loaded")
        self.dims_label.setObjectName("hintLabel")
        self.dims_label.setWordWrap(True)
        col.addWidget(self.dims_label)

        divider = QLabel("AI upscale")
        divider.setObjectName("sectionDivider")
        col.addWidget(divider)

        self.upscale_btn = QPushButton("AI Upscale (4x)")
        self.upscale_btn.setCursor(Qt.PointingHandCursor)
        self.upscale_btn.clicked.connect(self._on_upscale_clicked)
        col.addWidget(self.upscale_btn)

        self.upscale_status_label = QLabel("")
        self.upscale_status_label.setObjectName("hintLabel")
        self.upscale_status_label.setWordWrap(True)
        col.addWidget(self.upscale_status_label)

        upscale_hint = QLabel(
            "Runs a local AI model on your CPU (no upload, no API key). The model "
            "(~60MB) downloads once, the first time you use this."
        )
        upscale_hint.setObjectName("hintLabel")
        upscale_hint.setWordWrap(True)
        col.addWidget(upscale_hint)

        col.addStretch(1)
        return box

    def _build_crop_column(self) -> QWidget:
        box = QGroupBox("Crop")
        col = QVBoxLayout(box)
        col.setSpacing(8)

        self.canvas = CropCanvas()
        col.addWidget(self.canvas, 1)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Crop size"))
        self.crop_slider = QSlider(Qt.Horizontal)
        self.crop_slider.setRange(20, 100)
        self.crop_slider.setValue(100)
        self.crop_slider.valueChanged.connect(self._on_crop_slider_changed)
        size_row.addWidget(self.crop_slider, 1)
        col.addLayout(size_row)
        self.canvas.crop_changed.connect(self._on_canvas_crop_changed)

        crop_hint = QLabel(
            "Drag the square to move it, drag a corner to resize, scroll to zoom. "
            "Everything inside the square is what gets used."
        )
        crop_hint.setObjectName("hintLabel")
        crop_hint.setWordWrap(True)
        col.addWidget(crop_hint)

        return box

    def _build_target_column(self) -> QWidget:
        box = QGroupBox("Send to Publish")
        col = QVBoxLayout(box)
        col.setSpacing(8)

        col.addWidget(QLabel("Output size"))
        self.size_combo = NoScrollComboBox()
        for size in thumbnail_core.OUTPUT_SIZES:
            self.size_combo.addItem(f"{size} x {size}", size)
        default_index = self.size_combo.findData(thumbnail_core.DEFAULT_OUTPUT_SIZE)
        if default_index >= 0:
            self.size_combo.setCurrentIndex(default_index)
        col.addWidget(self.size_combo)

        info = QLabel(
            "Sends the cropped square to the Publish tab as the cover image. On "
            "upload it's used as the video's picture and its thumbnail."
        )
        info.setObjectName("hintLabel")
        info.setWordWrap(True)
        col.addWidget(info)

        self.send_btn = QPushButton("Send to Publish")
        self.send_btn.setObjectName("primaryButton")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setMinimumHeight(40)
        self.send_btn.clicked.connect(self._on_send_clicked)
        col.addWidget(self.send_btn)

        col.addStretch(1)
        return box

    # ---------------------------------------------------------------- image
    def _browse_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select image", "", IMAGE_FILTER)
        if not path:
            return
        try:
            image = Image.open(path)
            image.load()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Couldn't open image", str(exc))
            return
        self._image_path = path
        self._current_image = image.convert("RGB")
        self.canvas.set_image(self._current_image)
        self._reset_crop_slider()
        self._update_dims_label()
        self._update_enabled_state()

    def _reset_crop_slider(self) -> None:
        self.crop_slider.blockSignals(True)
        self.crop_slider.setValue(100)
        self.crop_slider.blockSignals(False)

    def _update_dims_label(self) -> None:
        if self._current_image is None:
            self.dims_label.setText("No image loaded")
            return
        w, h = self._current_image.size
        self.dims_label.setText(f"Current image: {w} x {h}px")

    # -------------------------------------------------------------- upscale
    def _on_upscale_clicked(self) -> None:
        if self._current_image is None or self._upscale_thread is not None:
            return
        w, h = self._current_image.size
        if max(w, h) > UPSCALE_WARN_DIM:
            reply = QMessageBox.question(
                self,
                "This may take a while",
                f"This image is {w}x{h}. AI upscaling runs locally on your CPU and can "
                "take several minutes at this size. Continue?",
            )
            if reply != QMessageBox.Yes:
                return

        self.upscale_btn.setEnabled(False)
        self.upscale_status_label.setText("Starting…")

        worker = UpscaleWorker(self._current_image)
        thread = run_worker_in_thread(worker, self)
        worker.stage_changed.connect(self._on_upscale_stage)
        worker.progress.connect(self._on_upscale_progress)
        worker.finished.connect(self._on_upscale_finished)
        worker.failed.connect(self._on_upscale_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_upscale_thread_done)
        thread.start()
        self._upscale_thread = thread
        self._upscale_worker = worker

    def _on_upscale_stage(self, stage: str) -> None:
        self._upscale_stage = stage
        self.upscale_status_label.setText(stage)

    def _on_upscale_progress(self, fraction: float) -> None:
        stage = getattr(self, "_upscale_stage", "Working")
        self.upscale_status_label.setText(f"{stage}… {int(fraction * 100)}%")

    def _on_upscale_finished(self, image: Image.Image) -> None:
        self._current_image = image
        self.canvas.set_image(image)
        self._reset_crop_slider()
        self._update_dims_label()
        self.upscale_status_label.setText("Done")

    def _on_upscale_failed(self, message: str) -> None:
        self.upscale_status_label.setText("Failed")
        QMessageBox.critical(self, "Upscale failed", message)

    def _on_upscale_thread_done(self) -> None:
        self._upscale_thread = None
        self._upscale_worker = None
        self.upscale_btn.setEnabled(True)

    # ------------------------------------------------------------ crop size
    def _on_crop_slider_changed(self, value: int) -> None:
        self.canvas.set_crop_fraction(value / 100)

    def _on_canvas_crop_changed(self, fraction: float) -> None:
        self.crop_slider.blockSignals(True)
        self.crop_slider.setValue(max(20, min(100, round(fraction * 100))))
        self.crop_slider.blockSignals(False)

    # -------------------------------------------------------- send to publish
    def _on_send_clicked(self) -> None:
        if self._current_image is None or not self.canvas.has_image():
            QMessageBox.warning(self, "Nothing to send", "Load an image first.")
            return
        size = self.size_combo.currentData()
        try:
            cropped = self.canvas.export_square(size)
            self._export_seq += 1
            out_path = self._work_dir / f"cover_{self._export_seq}.png"
            cropped.save(out_path, format="PNG")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.status_label.setText("Sent to Publish tab")
        self.send_to_publish.emit(str(out_path))

    # -------------------------------------------------------------- enable
    def _update_enabled_state(self) -> None:
        has_image = self._current_image is not None
        self.upscale_btn.setEnabled(has_image)
        self.send_btn.setEnabled(has_image)
