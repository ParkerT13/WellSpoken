from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from wellspoken.gui.widgets import make_hint_label
from wellspoken.media import ffmpeg_runner
from wellspoken.media.intro_outro import build_title_card_clip, normalize_user_clip
from wellspoken.workers import BackgroundTask

VIDEO_FILTER = "Video files (*.mp4 *.mkv *.mov *.avi *.webm);;All files (*.*)"
IMAGE_FILTER = "Image files (*.png *.jpg *.jpeg);;All files (*.*)"
DEFAULT_LOGO_PATH = str(
    Path(__file__).resolve().parent.parent.parent / "assets" / "branding" / "logo" / "seisware_icon.png"
)
WATERMARK_POSITIONS = [("bottom-right", "Bottom Right"), ("bottom-left", "Bottom Left")]


class _Section(QGroupBox):
    def __init__(self, app, label: str, attr_prefix: str):
        super().__init__(label)
        self.app = app
        self.attr_prefix = attr_prefix

        layout = QVBoxLayout(self)

        self.none_radio = QRadioButton("None")
        self.template_radio = QRadioButton("Built-in title card")
        self.clip_radio = QRadioButton("My own clip")
        for r in (self.none_radio, self.template_radio, self.clip_radio):
            layout.addWidget(r)

        self.title_row = QHBoxLayout()
        self.title_row.addWidget(QLabel("Title text:"))
        self.title_edit = QLineEdit(getattr(app.project, f"{attr_prefix}_title"))
        self.title_edit.textChanged.connect(self._sync_to_project)
        self.title_row.addWidget(self.title_edit)
        self._title_widget = QWidget()
        self._title_widget.setLayout(self.title_row)
        layout.addWidget(self._title_widget)

        self.clip_row = QHBoxLayout()
        self.clip_edit = QLineEdit(getattr(app.project, f"{attr_prefix}_clip_path") or "")
        self.clip_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_clip)
        self.clip_row.addWidget(self.clip_edit)
        self.clip_row.addWidget(browse_btn)
        self._clip_widget = QWidget()
        self._clip_widget.setLayout(self.clip_row)
        layout.addWidget(self._clip_widget)

        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setProperty("flat", True)
        self.preview_btn.clicked.connect(self._preview)
        layout.addWidget(self.preview_btn)

        for r in (self.none_radio, self.template_radio, self.clip_radio):
            r.toggled.connect(self._on_kind_change)
        kind = getattr(app.project, f"{attr_prefix}_kind")
        {"none": self.none_radio, "template": self.template_radio, "clip": self.clip_radio}[kind].setChecked(True)
        self._on_kind_change()

    def refresh_from_project(self) -> None:
        """Re-sync to self.app.project - called after New/Open Project.

        Snapshot every target value into local variables FIRST, before
        touching any widget. Each widget change below fires a live
        textChanged/toggled signal that writes the CURRENT (mid-update)
        widget state back into self.app.project via _sync_to_project() - if
        we re-read from self.app.project between widget updates instead of
        using this snapshot, an earlier update's side effect would corrupt a
        later read (verified empirically: setting the radio before the text
        fields let a stale kind leak back in; the reverse let a stale title
        leak back in - only a full up-front snapshot avoids both).
        """
        project = self.app.project
        title = getattr(project, f"{self.attr_prefix}_title")
        clip_path = getattr(project, f"{self.attr_prefix}_clip_path") or ""
        kind = getattr(project, f"{self.attr_prefix}_kind")

        self.title_edit.setText(title)
        self.clip_edit.setText(clip_path)
        {"none": self.none_radio, "template": self.template_radio, "clip": self.clip_radio}[kind].setChecked(True)
        self._on_kind_change()

    def _current_kind(self) -> str:
        if self.template_radio.isChecked():
            return "template"
        if self.clip_radio.isChecked():
            return "clip"
        return "none"

    def _on_kind_change(self) -> None:
        kind = self._current_kind()
        self._title_widget.setVisible(kind == "template")
        self._clip_widget.setVisible(kind == "clip")
        self._sync_to_project()

    def _browse_clip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select clip", "", VIDEO_FILTER)
        if path:
            self.clip_edit.setText(path)
            self._sync_to_project()

    def _sync_to_project(self) -> None:
        p = self.app.project
        setattr(p, f"{self.attr_prefix}_kind", self._current_kind())
        setattr(p, f"{self.attr_prefix}_title", self.title_edit.text())
        setattr(p, f"{self.attr_prefix}_clip_path", self.clip_edit.text() or None)

    def _preview(self) -> None:
        self._sync_to_project()
        kind = self._current_kind()
        if kind == "none":
            QMessageBox.information(self, "Nothing to preview", "Set this section to Template or My own clip first.")
            return
        video = self.app.project.source_video
        if not video:
            QMessageBox.information(self, "No video", "Select a screen recording in the Project tab first.")
            return

        def work(report):
            report("Probing source video...")
            meta = ffmpeg_runner.probe(video)
            w, h = meta["size"]
            out_path = self.app.scratch_dir() / f"{self.attr_prefix}_preview.mp4"
            if kind == "template":
                report("Building title card...")
                return build_title_card_clip(
                    self.title_edit.text(), out_path, width=w, height=h, fps=meta["fps"],
                    scratch_dir=self.app.scratch_dir(),
                )
            report("Normalizing clip...")
            return normalize_user_clip(self.clip_edit.text(), out_path, width=w, height=h, fps=meta["fps"])

        def done(path):
            os.startfile(path)

        def error(tb: str) -> None:
            QMessageBox.critical(self, "Preview failed", tb)

        BackgroundTask(self, work, done, on_error=error).start()


class IntroOutroTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        layout = QVBoxLayout(self)
        layout.addWidget(make_hint_label(
            "Optional. Add a title card at the start and/or end of the video - either a "
            "built-in text card, or your own intro/outro clip. Leave a section on \"None\" to skip it."
        ))
        self.intro_section = _Section(app, "Intro", "intro")
        layout.addWidget(self.intro_section)
        self.outro_section = _Section(app, "Outro", "outro")
        layout.addWidget(self.outro_section)

        logo_box = QGroupBox("Logo & Watermark")
        logo_layout = QVBoxLayout(logo_box)
        logo_layout.addWidget(make_hint_label(
            "The SeisWare logo is preset as the default. Pick a different image if needed - it's "
            "used as the watermark shown continuously in a corner of the exported video."
        ))

        logo_row = QHBoxLayout()
        choose_logo_btn = QPushButton("Choose Logo...")
        choose_logo_btn.setProperty("flat", True)
        choose_logo_btn.clicked.connect(self._choose_logo)
        reset_logo_btn = QPushButton("Reset to Default")
        reset_logo_btn.setProperty("flat", True)
        reset_logo_btn.clicked.connect(self._reset_logo)
        self.logo_preview = QLabel()
        self.logo_preview.setFixedSize(48, 48)
        self.logo_preview.setAlignment(Qt.AlignCenter)
        logo_row.addWidget(self.logo_preview)
        logo_row.addWidget(choose_logo_btn)
        logo_row.addWidget(reset_logo_btn)
        logo_row.addStretch(1)
        logo_layout.addLayout(logo_row)

        watermark_row = QHBoxLayout()
        self.watermark_check = QCheckBox("Watermark exported videos with this logo")
        self.watermark_check.toggled.connect(self._sync_watermark_to_project)
        watermark_row.addWidget(self.watermark_check)
        watermark_row.addWidget(QLabel("Position:"))
        self.watermark_position_combo = QComboBox()
        for key, label in WATERMARK_POSITIONS:
            self.watermark_position_combo.addItem(label, userData=key)
        self.watermark_position_combo.currentIndexChanged.connect(self._sync_watermark_to_project)
        watermark_row.addWidget(self.watermark_position_combo)
        watermark_row.addStretch(1)
        logo_layout.addLayout(watermark_row)

        layout.addWidget(logo_box)
        layout.addStretch(1)

        self._load_logo_preview(app.project.logo_path or DEFAULT_LOGO_PATH)
        self.watermark_check.setChecked(app.project.watermark_enabled)
        pos_idx = self.watermark_position_combo.findData(app.project.watermark_position)
        self.watermark_position_combo.setCurrentIndex(pos_idx if pos_idx >= 0 else 0)

    def _load_logo_preview(self, path: str) -> None:
        pixmap = QPixmap(path).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.logo_preview.setPixmap(pixmap)

    def _choose_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose logo image", "", IMAGE_FILTER)
        if not path:
            return
        self.app.project.logo_path = path
        self._load_logo_preview(path)

    def _reset_logo(self) -> None:
        self.app.project.logo_path = None
        self._load_logo_preview(DEFAULT_LOGO_PATH)

    def _sync_watermark_to_project(self, *_args) -> None:
        self.app.project.watermark_enabled = self.watermark_check.isChecked()
        self.app.project.watermark_position = self.watermark_position_combo.currentData()

    def refresh_from_project(self) -> None:
        self.intro_section.refresh_from_project()
        self.outro_section.refresh_from_project()
        project = self.app.project
        self._load_logo_preview(project.logo_path or DEFAULT_LOGO_PATH)
        self.watermark_check.setChecked(project.watermark_enabled)
        pos_idx = self.watermark_position_combo.findData(project.watermark_position)
        self.watermark_position_combo.setCurrentIndex(pos_idx if pos_idx >= 0 else 0)
