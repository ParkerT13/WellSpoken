from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wellspoken.gui.widgets import ProgressBar, make_hint_label
from wellspoken.media import combine, ffmpeg_runner
from wellspoken.media.thumbnail import make_thumbnail
from wellspoken.project import Project
from wellspoken.workers import BackgroundTask

VIDEO_FILTER = "Video files (*.mp4 *.mkv *.mov *.avi *.webm);;All files (*.*)"
PROJECT_FILTER = "WellSpoken project (*.json)"


class ProjectTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app

        layout = QVBoxLayout(self)
        layout.addWidget(make_hint_label(
            "Pick the screen recording you want to turn into a finished video - or record a new "
            "one on the Record tab. Then head to the Transcribe or AI Voice tab to add narration and captions."
        ))

        top = QHBoxLayout()
        new_btn = QPushButton("New Project")
        new_btn.setProperty("flat", True)
        new_btn.clicked.connect(self.new_project)
        open_btn = QPushButton("Open Project...")
        open_btn.setProperty("flat", True)
        open_btn.clicked.connect(self.open_project)
        save_btn = QPushButton("Save Project...")
        save_btn.setProperty("flat", True)
        save_btn.clicked.connect(self.save_project)
        top.addWidget(new_btn)
        top.addWidget(open_btn)
        top.addWidget(save_btn)
        top.addStretch(1)
        layout.addLayout(top)

        select_row = QHBoxLayout()
        select_btn = QPushButton("Select Screen Recording...")
        select_btn.clicked.connect(self.select_video)
        self.append_btn = QPushButton("Append Another Recording...")
        self.append_btn.setProperty("flat", True)
        self.append_btn.setEnabled(False)
        self.append_btn.clicked.connect(self.append_recording)
        player_btn = QPushButton("Open in Default Player")
        player_btn.setProperty("flat", True)
        player_btn.clicked.connect(self.open_in_player)
        select_row.addWidget(select_btn)
        select_row.addWidget(self.append_btn)
        select_row.addWidget(player_btn)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        self.info_label = QLabel("No video selected.")
        self.info_label.setProperty("muted", True)
        layout.addWidget(self.info_label)

        self.thumb_label = QLabel()
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setMinimumHeight(280)
        layout.addWidget(self.thumb_label)

        self.progress = ProgressBar()
        layout.addWidget(self.progress)
        layout.addStretch(1)

    def select_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select screen recording", "", VIDEO_FILTER)
        if not path:
            return
        self.app.project.source_video = path
        self._refresh_video_info(path)

    def _refresh_video_info(self, path: str) -> None:
        self.progress.start("Reading video info...")

        def work(report):
            meta = ffmpeg_runner.probe(path)
            thumb_path = self.app.scratch_dir() / "project_thumb.png"
            make_thumbnail(path, thumb_path, at_seconds=min(1.0, meta["duration"] / 2))
            return meta, thumb_path

        def done(result):
            meta, thumb_path = result
            w, h = meta["size"]
            self.info_label.setText(
                f"{os.path.basename(path)}   |   {w}x{h}   |   {meta['fps']:.2f} fps   |   {meta['duration']:.1f}s"
            )
            pixmap = QPixmap(str(thumb_path)).scaled(
                480, 270, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.thumb_label.setPixmap(pixmap)
            self.append_btn.setEnabled(True)
            self.progress.stop("")

        def error(tb: str) -> None:
            self.progress.stop("Failed to read video.")
            QMessageBox.critical(self, "Could not read video", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def append_recording(self) -> None:
        project = self.app.project
        if not project.source_video:
            return
        if project.segments:
            reply = QMessageBox.question(
                self,
                "Append Recording",
                "This project already has narration/captions timed against the current video. "
                "Appending another recording changes its length, so existing captions will no "
                "longer line up - you'll need to re-transcribe or regenerate voice afterward.\n\n"
                "Continue anyway?",
            )
            if reply != QMessageBox.Yes:
                return
        path, _ = QFileDialog.getOpenFileName(self, "Select recording to append", "", VIDEO_FILTER)
        if not path:
            return

        self.progress.start("Appending recording...")
        self.append_btn.setEnabled(False)

        def work(report):
            out_path = self.app.scratch_dir() / "combined_recording.mp4"
            return combine.append_clip(project.source_video, path, out_path, self.app.scratch_dir())

        def done(combined_path):
            project.source_video = str(combined_path)
            self._refresh_video_info(str(combined_path))

        def error(tb: str) -> None:
            self.append_btn.setEnabled(True)
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Append failed", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def open_in_player(self) -> None:
        path = self.app.project.source_video
        if not path:
            QMessageBox.information(self, "No video", "Select a screen recording first.")
            return
        os.startfile(path)

    def new_project(self) -> None:
        reply = QMessageBox.question(self, "New Project", "Discard current project state?")
        if reply == QMessageBox.Yes:
            self.app.project = Project()
            self.info_label.setText("No video selected.")
            self.thumb_label.clear()
            self.append_btn.setEnabled(False)
            self.app.refresh_all_tabs()

    def save_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "project.json", PROJECT_FILTER)
        if not path:
            return
        self.app.project.save(path)
        QMessageBox.information(self, "Saved", f"Project saved to {path}")

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", PROJECT_FILTER)
        if not path:
            return
        try:
            self.app.project = Project.load(path)
            if self.app.project.source_video:
                self._refresh_video_info(self.app.project.source_video)
            else:
                self.info_label.setText("No video selected.")
                self.thumb_label.clear()
                self.append_btn.setEnabled(False)
            self.app.refresh_all_tabs()
        except Exception as exc:
            QMessageBox.critical(self, "Could not open project", str(exc))
