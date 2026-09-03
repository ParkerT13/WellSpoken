from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from wellspoken.captions.export import write_ass
from wellspoken.captions.style import AVAILABLE_FONTS, CaptionStyle
from wellspoken.gui.tab_intro_outro import DEFAULT_LOGO_PATH
from wellspoken.gui.widgets import ProgressBar, make_hint_label
from wellspoken.media import ffmpeg_runner
from wellspoken.media.render_pipeline import IntroOutroSpec, RenderOptions, render
from wellspoken.models import CaptionSegment
from wellspoken.workers import BackgroundTask

ASPECT_OPTIONS = [
    ("original", "Original (matches the recording)"),
    ("16:9", "16:9 Landscape - YouTube, standard 1920x1080"),
    ("9:16", "9:16 Vertical - Reels, TikTok, Stories"),
    ("1:1", "1:1 Square - feed posts"),
]

PREVIEW_SAMPLE_TEXT = "Sample caption text goes here"


def _color_swatch(hex_color: str) -> QPushButton:
    btn = QPushButton()
    btn.setFixedSize(36, 24)
    btn.setProperty("hex_color", hex_color)
    btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #888;")
    return btn


class ExportTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._last_output_dir = None
        self._preview_frame_path = None
        self._preview_frame_source = None

        layout = QVBoxLayout(self)
        layout.addWidget(make_hint_label(
            "Choose how captions should appear, pick a folder for the finished video, then Render. "
            "This can take a minute or two for longer recordings - progress is shown below."
        ))

        mode_box = QGroupBox("Captions")
        mode_layout = QVBoxLayout(mode_box)
        self.burned_radio = QRadioButton("Burned into the video")
        self.sidecar_radio = QRadioButton("Separate .srt/.vtt file only")
        self.both_radio = QRadioButton("Both")
        {"burned_in": self.burned_radio, "sidecar": self.sidecar_radio, "both": self.both_radio}[
            app.project.caption_mode
        ].setChecked(True)
        for r in (self.burned_radio, self.sidecar_radio, self.both_radio):
            mode_layout.addWidget(r)
        layout.addWidget(mode_box)

        style_box = QGroupBox("Caption Style")
        style_layout = QVBoxLayout(style_box)
        style_layout.addWidget(make_hint_label(
            "Defaults are tuned for legibility - bold white text with a thick black outline, "
            "readable over any background. Adjust and preview below if you want something different."
        ))

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Font:"))
        self.font_combo = QComboBox()
        self.font_combo.addItems(AVAILABLE_FONTS)
        row1.addWidget(self.font_combo)
        row1.addWidget(QLabel("Size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(16, 96)
        row1.addWidget(self.size_spin)
        self.bold_check = QCheckBox("Bold")
        row1.addWidget(self.bold_check)
        row1.addStretch(1)
        style_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Text color:"))
        self.text_color_btn = _color_swatch(CaptionStyle().primary_color)
        self.text_color_btn.clicked.connect(lambda: self._pick_color(self.text_color_btn))
        row2.addWidget(self.text_color_btn)
        row2.addWidget(QLabel("Outline color:"))
        self.outline_color_btn = _color_swatch(CaptionStyle().outline_color)
        self.outline_color_btn.clicked.connect(lambda: self._pick_color(self.outline_color_btn))
        row2.addWidget(self.outline_color_btn)
        row2.addWidget(QLabel("Outline width:"))
        self.outline_width_spin = QSpinBox()
        self.outline_width_spin.setRange(0, 8)
        row2.addWidget(self.outline_width_spin)
        row2.addStretch(1)
        style_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Position:"))
        self.position_combo = QComboBox()
        self.position_combo.addItem("Bottom", userData="bottom")
        self.position_combo.addItem("Top", userData="top")
        row3.addWidget(self.position_combo)
        row3.addWidget(QLabel("Margin:"))
        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 300)
        row3.addWidget(self.margin_spin)
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setProperty("flat", True)
        reset_btn.clicked.connect(self._reset_style_defaults)
        row3.addWidget(reset_btn)
        row3.addStretch(1)
        style_layout.addLayout(row3)

        self._load_style_into_widgets(app.project.caption_style)

        self.style_preview_label = QLabel("Select a screen recording in the Project tab to preview caption style.")
        self.style_preview_label.setAlignment(Qt.AlignCenter)
        self.style_preview_label.setMinimumHeight(200)
        self.style_preview_label.setProperty("muted", True)
        style_layout.addWidget(self.style_preview_label)

        preview_row = QHBoxLayout()
        preview_clip_btn = QPushButton("Preview Full Clip...")
        preview_clip_btn.setProperty("flat", True)
        preview_clip_btn.clicked.connect(self._preview_full_clip)
        preview_row.addWidget(preview_clip_btn)
        preview_row.addStretch(1)
        style_layout.addLayout(preview_row)

        layout.addWidget(style_box)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._update_style_preview)
        for w, sig in (
            (self.font_combo, "currentIndexChanged"),
            (self.size_spin, "valueChanged"),
            (self.bold_check, "toggled"),
            (self.outline_width_spin, "valueChanged"),
            (self.position_combo, "currentIndexChanged"),
            (self.margin_spin, "valueChanged"),
        ):
            getattr(w, sig).connect(self._schedule_preview)

        music_box = QGroupBox("Background Music (optional)")
        music_layout = QVBoxLayout(music_box)
        music_row = QHBoxLayout()
        choose_music_btn = QPushButton("Choose Music File...")
        choose_music_btn.setProperty("flat", True)
        choose_music_btn.clicked.connect(self.choose_music)
        clear_music_btn = QPushButton("Clear")
        clear_music_btn.setProperty("flat", True)
        clear_music_btn.clicked.connect(self.clear_music)
        self.music_label = QLabel(os.path.basename(app.project.background_music_path) if app.project.background_music_path else "(none)")
        self.music_label.setProperty("muted", True)
        music_row.addWidget(choose_music_btn)
        music_row.addWidget(clear_music_btn)
        music_row.addWidget(self.music_label)
        music_row.addStretch(1)
        music_layout.addLayout(music_row)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Volume under narration:"))
        self.music_volume_spin = QSpinBox()
        self.music_volume_spin.setRange(1, 100)
        self.music_volume_spin.setSuffix("%")
        self.music_volume_spin.setValue(round(app.project.background_music_volume * 100))
        volume_row.addWidget(self.music_volume_spin)
        volume_row.addStretch(1)
        music_layout.addLayout(volume_row)
        layout.addWidget(music_box)

        aspect_box = QGroupBox("Aspect Ratio")
        aspect_layout = QVBoxLayout(aspect_box)
        self.aspect_radios: dict[str, QRadioButton] = {}
        for key, label in ASPECT_OPTIONS:
            r = QRadioButton(label)
            self.aspect_radios[key] = r
            aspect_layout.addWidget(r)
        self.aspect_radios.get(app.project.aspect_ratio, self.aspect_radios["original"]).setChecked(True)
        layout.addWidget(aspect_box)

        extra_box = QGroupBox("Also Export As (optional)")
        extra_layout = QVBoxLayout(extra_box)
        extra_layout.addWidget(make_hint_label(
            "Render additional formats in the same pass - handy for posting the same video to "
            "multiple platforms without re-rendering from scratch."
        ))
        self.extra_aspect_checks: dict[str, QCheckBox] = {}
        for key, label in ASPECT_OPTIONS:
            c = QCheckBox(label)
            self.extra_aspect_checks[key] = c
            extra_layout.addWidget(c)
        layout.addWidget(extra_box)

        out_row = QHBoxLayout()
        choose_btn = QPushButton("Choose Output Folder...")
        choose_btn.setProperty("flat", True)
        choose_btn.clicked.connect(self.choose_output_dir)
        out_row.addWidget(choose_btn)
        self.output_label = QLabel(app.project.output_dir or "(not set)")
        self.output_label.setProperty("muted", True)
        out_row.addWidget(self.output_label)
        out_row.addStretch(1)
        layout.addLayout(out_row)

        render_row = QHBoxLayout()
        render_btn = QPushButton("Render")
        render_btn.clicked.connect(self.render_now)
        self.app.register_busy_widget(render_btn)
        render_row.addWidget(render_btn)
        render_row.addStretch(1)
        layout.addLayout(render_row)

        self.progress = ProgressBar()
        layout.addWidget(self.progress)

        self.open_folder_btn = QPushButton("Open Output Folder")
        self.open_folder_btn.setProperty("flat", True)
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        layout.addWidget(self.open_folder_btn)
        layout.addStretch(1)

        self._schedule_preview()

    def showEvent(self, event) -> None:
        """The Project/Timeline tabs can change source_video or segments
        while this tab isn't visible - re-check the preview whenever this tab
        becomes visible again, not just when a style widget changes."""
        super().showEvent(event)
        self._schedule_preview()

    def refresh_from_project(self) -> None:
        """Re-sync to self.app.project - called after New/Open Project."""
        project = self.app.project
        {"burned_in": self.burned_radio, "sidecar": self.sidecar_radio, "both": self.both_radio}[
            project.caption_mode
        ].setChecked(True)
        self._load_style_into_widgets(project.caption_style)
        self.music_label.setText(os.path.basename(project.background_music_path) if project.background_music_path else "(none)")
        self.music_volume_spin.setValue(round(project.background_music_volume * 100))
        self.aspect_radios.get(project.aspect_ratio, self.aspect_radios["original"]).setChecked(True)
        for c in self.extra_aspect_checks.values():
            c.setChecked(False)
        self.output_label.setText(project.output_dir or "(not set)")
        self.open_folder_btn.setEnabled(False)
        self._last_output_dir = None
        self._preview_frame_source = None
        self._schedule_preview()

    def choose_music(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose background music", "", "Audio files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;All files (*.*)"
        )
        if not path:
            return
        self.app.project.background_music_path = path
        self.music_label.setText(os.path.basename(path))

    def clear_music(self) -> None:
        self.app.project.background_music_path = None
        self.music_label.setText("(none)")

    def _load_style_into_widgets(self, style: CaptionStyle) -> None:
        idx = self.font_combo.findText(style.font_family)
        self.font_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.size_spin.setValue(style.font_size)
        self.bold_check.setChecked(style.bold)
        self._set_swatch(self.text_color_btn, style.primary_color)
        self._set_swatch(self.outline_color_btn, style.outline_color)
        self.outline_width_spin.setValue(style.outline_width)
        pos_idx = self.position_combo.findData(style.position)
        self.position_combo.setCurrentIndex(pos_idx if pos_idx >= 0 else 0)
        self.margin_spin.setValue(style.margin_v)

    def _reset_style_defaults(self) -> None:
        self._load_style_into_widgets(CaptionStyle())
        self._schedule_preview()

    @staticmethod
    def _set_swatch(btn: QPushButton, hex_color: str) -> None:
        btn.setProperty("hex_color", hex_color)
        btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #888;")

    def _pick_color(self, btn: QPushButton) -> None:
        current = QColor(btn.property("hex_color") or "#FFFFFF")
        color = QColorDialog.getColor(current, self, "Choose color")
        if color.isValid():
            self._set_swatch(btn, color.name().upper())
            self._schedule_preview()

    def _caption_mode(self) -> str:
        if self.sidecar_radio.isChecked():
            return "sidecar"
        if self.both_radio.isChecked():
            return "both"
        return "burned_in"

    def _caption_style(self) -> CaptionStyle:
        return CaptionStyle(
            font_family=self.font_combo.currentText(),
            font_size=self.size_spin.value(),
            bold=self.bold_check.isChecked(),
            primary_color=self.text_color_btn.property("hex_color") or "#FFFFFF",
            outline_color=self.outline_color_btn.property("hex_color") or "#000000",
            outline_width=self.outline_width_spin.value(),
            position=self.position_combo.currentData(),
            margin_v=self.margin_spin.value(),
        )

    def _aspect_ratio(self) -> str:
        for key, r in self.aspect_radios.items():
            if r.isChecked():
                return key
        return "original"

    def _extra_aspect_ratios(self) -> list[str]:
        return [key for key, c in self.extra_aspect_checks.items() if c.isChecked()]

    def _schedule_preview(self, *_args) -> None:
        self._preview_timer.start(150)

    def _update_style_preview(self) -> None:
        video = self.app.project.source_video
        if not video or not os.path.exists(video):
            self.style_preview_label.setText("Select a screen recording in the Project tab to preview caption style.")
            self.style_preview_label.setPixmap(QPixmap())
            return

        def work(report):
            meta = ffmpeg_runner.probe(video)
            width, height = meta["size"]
            frame_path = self.app.scratch_dir() / "caption_style_preview_frame.png"
            if self._preview_frame_source != video:
                ffmpeg_runner.grab_frame(video, frame_path, at_seconds=min(1.0, meta["duration"] / 2))
                self._preview_frame_source = video
            sample_text = self.app.project.segments[0].text if self.app.project.segments else PREVIEW_SAMPLE_TEXT
            ass_path = self.app.scratch_dir() / "caption_style_preview.ass"
            write_ass(
                [CaptionSegment(index=1, start=0.0, end=3.0, text=sample_text, source="tts")],
                self._caption_style(), width, height, ass_path,
            )
            out_path = self.app.scratch_dir() / "caption_style_preview_out.png"
            ffmpeg_runner.render_caption_style_preview(frame_path, ass_path, out_path)
            return out_path

        def done(out_path):
            pixmap = QPixmap(str(out_path)).scaled(
                640, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.style_preview_label.setPixmap(pixmap)

        def error(_tb: str) -> None:
            self.style_preview_label.setText("Could not render preview.")

        BackgroundTask(self, work, done, on_error=error).start()

    def _preview_full_clip(self) -> None:
        project = self.app.project
        if not project.source_video:
            QMessageBox.information(self, "No video", "Select a screen recording in the Project tab first.")
            return
        if not project.segments:
            QMessageBox.information(
                self, "No captions", "Generate voice or transcribe narration first, so there's something to preview."
            )
            return

        clip_len = min(15.0, ffmpeg_runner.probe(project.source_video)["duration"])
        preview_segments = [
            s for s in project.segments if s.start < clip_len
        ]
        if not preview_segments:
            preview_segments = project.segments[:1]

        dialog = _CaptionPreviewDialog(self, project.source_video, preview_segments, self._caption_style(), clip_len)
        dialog.exec()

    def choose_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if not path:
            return
        self.app.project.output_dir = path
        self.output_label.setText(path)

    def render_now(self) -> None:
        if self.app._busy_count > 0:
            QMessageBox.information(self, "Busy", "Another operation is in progress - please wait for it to finish.")
            return
        project = self.app.project
        if not project.source_video:
            QMessageBox.information(self, "No video", "Select a screen recording in the Project tab first.")
            return
        if not project.segments:
            QMessageBox.information(
                self,
                "No captions",
                "Generate voice (Script -> Voice tab) or transcribe narration (Transcribe tab) first.",
            )
            return
        if not project.output_dir:
            QMessageBox.information(self, "No output folder", "Choose an output folder first.")
            return

        project.caption_mode = self._caption_mode()
        project.caption_style = self._caption_style()
        project.aspect_ratio = self._aspect_ratio()
        project.background_music_volume = self.music_volume_spin.value() / 100.0
        narration = project.resolve_narration_for_render()

        opts = RenderOptions(
            source_video=project.source_video,
            narration_wav=narration,
            segments=project.segments,
            intro=IntroOutroSpec(
                kind=project.intro_kind, title=project.intro_title, clip_path=project.intro_clip_path
            ),
            outro=IntroOutroSpec(
                kind=project.outro_kind, title=project.outro_title, clip_path=project.outro_clip_path
            ),
            caption_mode=project.caption_mode,
            output_dir=project.output_dir,
            scratch_dir=str(self.app.scratch_dir()),
            aspect_ratio=project.aspect_ratio,
            caption_style=project.caption_style,
            background_music=project.background_music_path,
            background_music_volume=project.background_music_volume,
            extra_aspect_ratios=self._extra_aspect_ratios(),
            watermark_path=(project.logo_path or DEFAULT_LOGO_PATH) if project.watermark_enabled else None,
            watermark_position=project.watermark_position,
        )

        self.progress.start("Starting render...")
        self.open_folder_btn.setEnabled(False)

        def work(report):
            return render(opts, on_progress=report)

        def done(result):
            self.app.set_busy(False)
            self.progress.stop("Render complete.")
            self._last_output_dir = project.output_dir
            self.open_folder_btn.setEnabled(True)
            extra_lines = "".join(f"\n{k}: {v}" for k, v in result.items() if k.startswith("video_"))
            QMessageBox.information(self, "Done", f"Rendered:\n{result.get('video')}{extra_lines}")

        def error(tb: str) -> None:
            self.app.set_busy(False)
            self.progress.stop("Render failed.")
            QMessageBox.critical(self, "Render failed", tb)

        self.app.set_busy(True)
        BackgroundTask(self, work, done, on_error=error, on_progress=self.progress.set_message).start()

    def open_output_folder(self) -> None:
        if self._last_output_dir:
            os.startfile(self._last_output_dir)


class _CaptionPreviewDialog(QDialog):
    """Burns the current caption style onto a short real clip (real segments,
    real timing) and plays it back - a true "what you'll actually get"
    preview, not just a static frame."""

    def __init__(self, parent, source_video: str, segments: list[CaptionSegment], style: CaptionStyle, clip_len: float):
        super().__init__(parent)
        self.setWindowTitle("Caption Style Preview")
        self.resize(720, 480)
        layout = QVBoxLayout(self)

        self.status_label = QLabel("Rendering preview clip...")
        layout.addWidget(self.status_label)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(360)
        layout.addWidget(self.video_widget)

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)

        controls = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle_play)
        controls.addWidget(self.play_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        app = parent.app
        scratch = app.scratch_dir()

        def work(report):
            report("Trimming clip...")
            trimmed = ffmpeg_runner.extract_range(source_video, 0.0, clip_len, scratch / "style_preview_trim.mp4")
            report("Writing captions...")
            width, height = ffmpeg_runner.probe(source_video)["size"]
            ass_path = scratch / "style_preview.ass"
            write_ass(segments, style, width, height, ass_path)
            report("Burning in captions...")
            out_path = scratch / "style_preview_out.mp4"
            return ffmpeg_runner.burn_subtitles(trimmed, ass_path, out_path)

        def done(out_path):
            self.status_label.setText("Preview ready.")
            self.media_player.setSource(QUrl.fromLocalFile(str(out_path)))
            self.play_btn.setEnabled(True)
            self.media_player.play()
            self.play_btn.setText("Pause")

        def error(tb: str) -> None:
            self.status_label.setText("Preview failed.")
            QMessageBox.critical(self, "Preview failed", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def _toggle_play(self) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("Play")
        else:
            self.media_player.play()
            self.play_btn.setText("Pause")
