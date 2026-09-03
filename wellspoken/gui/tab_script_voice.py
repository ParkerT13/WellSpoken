from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from wellspoken.captions.qc import reconcile_tts_captions
from wellspoken.gui.widgets import ProgressBar, make_hint_label
from wellspoken.transcribe.whisper_engine import transcribe
from wellspoken.tts.voices import CURATED_VOICES
from wellspoken.workers import BackgroundTask


class ScriptVoiceTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app

        layout = QVBoxLayout(self)
        layout.addWidget(make_hint_label(
            "Use this tab if you want a computer voice to read a script you write - either type one "
            "from scratch, or send a transcript over from the Transcribe tab and reword it here. "
            "Pick a voice, then click Generate - it will synthesize narration and build time-synced "
            "captions automatically. If a word gets mispronounced, add it to the pronunciation "
            "lexicon below and generate again."
        ))

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Voice:"))
        self.voice_combo = QComboBox()
        self.voice_combo.setMinimumWidth(280)
        for voice_id, label, _engine in CURATED_VOICES:
            self.voice_combo.addItem(label, userData=voice_id)
        current = app.project.voice_name
        idx = self.voice_combo.findData(current)
        self.voice_combo.setCurrentIndex(idx if idx >= 0 else 0)
        voice_row.addWidget(self.voice_combo)
        hint = QLabel("Downloaded automatically the first time you use it.")
        hint.setProperty("muted", True)
        voice_row.addWidget(hint)
        voice_row.addStretch(1)
        layout.addLayout(voice_row)

        layout.addWidget(QLabel("Script:"))
        self.script_edit = QTextEdit()
        self.script_edit.setPlainText(app.project.script_text)
        self.script_edit.setFixedHeight(140)
        layout.addWidget(self.script_edit)

        lex_box = QGroupBox("Pronunciation lexicon (word -> respelling)")
        lex_layout = QHBoxLayout(lex_box)
        self.lex_list = QListWidget()
        self.lex_list.setMaximumHeight(110)
        lex_layout.addWidget(self.lex_list, stretch=1)

        lex_controls = QVBoxLayout()
        self.word_edit = QLineEdit()
        self.word_edit.setPlaceholderText("word")
        self.respelling_edit = QLineEdit()
        self.respelling_edit.setPlaceholderText("respelling")
        add_btn = QPushButton("Add / Update")
        add_btn.clicked.connect(self.add_lexicon_entry)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.setProperty("flat", True)
        remove_btn.clicked.connect(self.remove_lexicon_entry)
        lex_controls.addWidget(self.word_edit)
        lex_controls.addWidget(self.respelling_edit)
        lex_controls.addWidget(add_btn)
        lex_controls.addWidget(remove_btn)
        lex_layout.addLayout(lex_controls)
        layout.addWidget(lex_box)
        self._refresh_lexicon_list()

        actions = QHBoxLayout()
        gen_btn = QPushButton("Generate Voice + Captions")
        gen_btn.clicked.connect(self.generate)
        self.app.register_busy_widget(gen_btn)
        play_btn = QPushButton("Play Narration")
        play_btn.setProperty("flat", True)
        play_btn.clicked.connect(self.play_narration)
        actions.addWidget(gen_btn)
        actions.addWidget(play_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.progress = ProgressBar()
        layout.addWidget(self.progress)

        layout.addWidget(QLabel("Flagged (possibly mispronounced) lines:"))
        self.flag_list = QListWidget()
        layout.addWidget(self.flag_list, stretch=1)

    def refresh_from_project(self) -> None:
        """Re-sync to self.app.project - called after New/Open Project."""
        self.script_edit.setPlainText(self.app.project.script_text)
        idx = self.voice_combo.findData(self.app.project.voice_name)
        self.voice_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.flag_list.clear()
        for seg in self.app.project.segments:
            if seg.flagged:
                self.flag_list.addItem(seg.text)

    def _refresh_lexicon_list(self) -> None:
        self.lex_list.clear()
        for word, respelling in sorted(self.app.lexicon.overrides.items()):
            self.lex_list.addItem(f"{word}  ->  {respelling}")

    def add_lexicon_entry(self) -> None:
        word = self.word_edit.text().strip()
        respelling = self.respelling_edit.text().strip()
        if not word or not respelling:
            return
        self.app.lexicon.set(word, respelling)
        self._refresh_lexicon_list()
        self.word_edit.clear()
        self.respelling_edit.clear()

    def remove_lexicon_entry(self) -> None:
        item = self.lex_list.currentItem()
        if not item:
            return
        word = item.text().split("  ->  ")[0]
        self.app.lexicon.remove(word)
        self._refresh_lexicon_list()

    def generate(self) -> None:
        if self.app._busy_count > 0:
            QMessageBox.information(self, "Busy", "Another operation is in progress - please wait for it to finish.")
            return
        script = self.script_edit.toPlainText().strip()
        if not script:
            QMessageBox.information(self, "No script", "Type a script first.")
            return
        voice_name = self.voice_combo.currentData()
        self.app.project.script_text = script
        self.app.project.voice_name = voice_name
        self.progress.start("Loading voice model...")

        def work(report):
            report("Loading voice model...")
            engine = self.app.get_voice_engine(voice_name)
            report("Synthesizing narration...")
            wav_path = engine.synthesize_to_wav(script, self.app.scratch_dir() / "narration.wav", on_progress=report)
            report("Transcribing narration for timing + QC...")
            segments = transcribe(
                wav_path, source="tts",
                initial_prompt=self.app.lexicon.prompt_text(), lexicon=self.app.lexicon,
            )
            segments = reconcile_tts_captions(segments, script, lexicon=self.app.lexicon)
            return wav_path, segments

        def done(result):
            self.app.set_busy(False)
            wav_path, segments = result
            self.app.project.narration_audio = str(wav_path)
            self.app.project.segments = segments
            self.app.project.workflow = "script"
            self.progress.stop(f"Done. {len(segments)} caption lines generated.")
            self.flag_list.clear()
            for seg in segments:
                if seg.flagged:
                    self.flag_list.addItem(seg.text)
            if any(seg.flagged for seg in segments):
                QMessageBox.warning(
                    self,
                    "Possible mispronunciations",
                    "Some lines didn't line up cleanly with the script when re-checked. "
                    "Check the flagged list, add a lexicon entry, and regenerate.",
                )

        def error(tb: str) -> None:
            self.app.set_busy(False)
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Voice generation failed", tb)

        self.app.set_busy(True)
        BackgroundTask(self, work, done, on_error=error, on_progress=self.progress.set_message).start()

    def play_narration(self) -> None:
        path = self.app.project.narration_audio
        if not path:
            QMessageBox.information(self, "No narration", "Generate voice first.")
            return
        os.startfile(path)
