from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wellspoken.gui.widgets import ProgressBar, make_hint_label
from wellspoken.media.ffmpeg_runner import has_audio_track
from wellspoken.transcribe.whisper_engine import transcribe
from wellspoken.workers import BackgroundTask

AUDIO_FILTER = "Audio/video files (*.wav *.mp3 *.m4a *.mp4 *.mkv *.mov);;All files (*.*)"


class TranscribeTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.segments = []
        self._selected_audio = None

        layout = QVBoxLayout(self)
        layout.addWidget(make_hint_label(
            "Use this tab if you already recorded your own narration (or the screen recording "
            "already has your voice on it). Transcribe it, fix any lines the speech-to-text got "
            "wrong below, then optionally send the corrected transcript to the Script -> Voice tab "
            "to re-record it as a script in a different voice - reword it there before generating."
        ))

        top = QHBoxLayout()
        select_btn = QPushButton("Select Narration Audio...")
        select_btn.setProperty("flat", True)
        select_btn.clicked.connect(self.select_audio)
        use_video_btn = QPushButton("Use Project Video's Audio")
        use_video_btn.setProperty("flat", True)
        use_video_btn.clicked.connect(self.use_project_video)
        transcribe_btn = QPushButton("Transcribe")
        transcribe_btn.clicked.connect(self.transcribe_audio)
        self.app.register_busy_widget(transcribe_btn)
        top.addWidget(select_btn)
        top.addWidget(use_video_btn)
        top.addWidget(transcribe_btn)
        top.addStretch(1)
        layout.addLayout(top)

        self.audio_label = QLabel("No narration audio selected.")
        self.audio_label.setProperty("muted", True)
        layout.addWidget(self.audio_label)

        self.progress = ProgressBar()
        layout.addWidget(self.progress)

        layout.addWidget(QLabel("Caption segments (select a line, edit text below, then Update):"))
        self.seg_list = QListWidget()
        self.seg_list.currentRowChanged.connect(self._on_select)
        layout.addWidget(self.seg_list, stretch=1)

        edit_row = QHBoxLayout()
        self.edit_line = QLineEdit()
        update_btn = QPushButton("Update Segment Text")
        update_btn.setProperty("flat", True)
        update_btn.clicked.connect(self.update_segment)
        edit_row.addWidget(self.edit_line, stretch=1)
        edit_row.addWidget(update_btn)
        layout.addLayout(edit_row)

        send_row = QHBoxLayout()
        send_btn = QPushButton("Send Transcript to Script -> Voice...")
        send_btn.clicked.connect(self.send_to_script_voice)
        send_row.addWidget(send_btn)
        send_row.addStretch(1)
        layout.addLayout(send_row)

    def refresh_from_project(self) -> None:
        """Re-sync to self.app.project - called after New/Open Project."""
        project = self.app.project
        self.segments = list(project.segments) if project.workflow == "transcribe" else []
        self._selected_audio = None
        self.audio_label.setText("No narration audio selected.")
        self.edit_line.clear()
        self._refresh_seg_list()

    def select_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select narration audio", "", AUDIO_FILTER)
        if not path:
            return
        if not has_audio_track(path):
            QMessageBox.information(
                self, "No audio track",
                "This file doesn't have an audio track, so there's nothing to transcribe. "
                "Select a different file.",
            )
            return
        self._selected_audio = path
        self.audio_label.setText(path)

    def use_project_video(self) -> None:
        video = self.app.project.source_video
        if not video:
            QMessageBox.information(self, "No video", "Select a screen recording in the Project tab first.")
            return
        if not has_audio_track(video):
            QMessageBox.information(
                self, "No audio track",
                "This recording doesn't have an audio track, so there's nothing to transcribe. "
                "If you recorded it without a microphone selected, use the AI Voice tab to add narration instead.",
            )
            return
        self._selected_audio = video
        self.audio_label.setText(f"(using project video's audio track) {video}")

    def transcribe_audio(self) -> None:
        if self.app._busy_count > 0:
            QMessageBox.information(self, "Busy", "Another operation is in progress - please wait for it to finish.")
            return
        if not self._selected_audio:
            QMessageBox.information(self, "No audio", "Select narration audio first.")
            return
        self.progress.start("Loading transcription model...")

        def work(report):
            report("Transcribing...")
            return transcribe(
                self._selected_audio, source="transcribed",
                initial_prompt=self.app.lexicon.prompt_text(), lexicon=self.app.lexicon,
            )

        def done(segments):
            self.app.set_busy(False)
            self.segments = segments
            self.app.project.segments = segments
            self.app.project.narration_audio = self._selected_audio
            self.app.project.workflow = "transcribe"
            self.progress.stop(f"Done. {len(segments)} caption lines.")
            self._refresh_seg_list()

        def error(tb: str) -> None:
            self.app.set_busy(False)
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Transcription failed", tb)

        self.app.set_busy(True)
        BackgroundTask(self, work, done, on_error=error, on_progress=self.progress.set_message).start()

    def _refresh_seg_list(self) -> None:
        self.seg_list.clear()
        for seg in self.segments:
            self.seg_list.addItem(f"[{seg.start:6.2f}-{seg.end:6.2f}]  {seg.text}")

    def _on_select(self, row: int) -> None:
        if row < 0 or row >= len(self.segments):
            return
        self.edit_line.setText(self.segments[row].text)

    def update_segment(self) -> None:
        row = self.seg_list.currentRow()
        if row < 0:
            return
        self.segments[row].text = self.edit_line.text()
        self.app.project.segments = self.segments
        self._refresh_seg_list()
        self.seg_list.setCurrentRow(row)

    def send_to_script_voice(self) -> None:
        if not self.segments:
            QMessageBox.information(self, "Nothing to send", "Transcribe some audio first.")
            return
        script_text = " ".join(seg.text for seg in self.segments)
        self.app.tab_script_voice.script_edit.setPlainText(script_text)
        self.app.show_tab(self.app.tab_script_voice)
        QMessageBox.information(
            self,
            "Sent to Script -> Voice",
            "Your transcript is now in the Script -> Voice tab's script box. Reword whatever you "
            "want changed, pick a voice, and click Generate to re-record it as AI narration.",
        )
