from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
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
from wellspoken.tts.custom_voices import get_custom_voice, remove_custom_voice
from wellspoken.tts.voices import all_voices
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
        voice_row.addWidget(self.voice_combo)
        import_voice_btn = QPushButton("Import / Clone Voice...")
        import_voice_btn.setProperty("flat", True)
        import_voice_btn.clicked.connect(self.import_voice)
        voice_row.addWidget(import_voice_btn)
        self.remove_voice_btn = QPushButton("Remove")
        self.remove_voice_btn.setProperty("flat", True)
        self.remove_voice_btn.clicked.connect(self.remove_voice)
        voice_row.addWidget(self.remove_voice_btn)
        voice_row.addStretch(1)
        layout.addLayout(voice_row)
        hint = QLabel(
            "Built-in voices download automatically the first time you use them. Imported/cloned "
            "voices are cloned from a short reference clip and stay only on this computer."
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.voice_combo.currentIndexChanged.connect(self._update_remove_voice_enabled)
        self._refresh_voice_combo(select=app.project.voice_name)

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
        self._refresh_voice_combo(select=self.app.project.voice_name)
        self.flag_list.clear()
        for seg in self.app.project.segments:
            if seg.flagged:
                self.flag_list.addItem(seg.text)

    def _refresh_voice_combo(self, select: str | None = None) -> None:
        select = select if select is not None else self.voice_combo.currentData()
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for voice_id, label, _engine in all_voices():
            self.voice_combo.addItem(label, userData=voice_id)
        idx = self.voice_combo.findData(select)
        self.voice_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.voice_combo.blockSignals(False)
        self._update_remove_voice_enabled()

    def _update_remove_voice_enabled(self) -> None:
        voice_id = self.voice_combo.currentData()
        self.remove_voice_btn.setEnabled(bool(voice_id) and get_custom_voice(voice_id) is not None)

    def import_voice(self) -> None:
        if self.app._busy_count > 0:
            QMessageBox.information(self, "Busy", "Another operation is in progress - please wait for it to finish.")
            return
        source_path, _ = QFileDialog.getOpenFileName(
            self, "Choose a reference clip to clone",
            "", "Audio/Video (*.wav *.mp3 *.m4a *.flac *.ogg *.mp4 *.mov *.mkv);;All files (*)",
        )
        if not source_path:
            return
        label, ok = QInputDialog.getText(self, "Name this voice", "Voice name (e.g. an employee's name):")
        label = label.strip()
        if not ok or not label:
            return

        self.progress.start("Importing voice...")

        def work(report):
            from wellspoken.tts.custom_voices import add_custom_voice

            report("Extracting reference clip...")
            return add_custom_voice(label, source_path)

        def done(result) -> None:
            self.app.set_busy(False)
            cv, warning = result
            self.progress.stop(f"Imported '{cv.label}'.")
            self._refresh_voice_combo(select=cv.voice_id)
            if warning:
                QMessageBox.warning(self, "Voice imported", warning)

        def error(tb: str) -> None:
            self.app.set_busy(False)
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Voice import failed", tb)

        self.app.set_busy(True)
        BackgroundTask(self, work, done, on_error=error, on_progress=self.progress.set_message).start()

    def remove_voice(self) -> None:
        voice_id = self.voice_combo.currentData()
        custom = get_custom_voice(voice_id) if voice_id else None
        if custom is None:
            return
        confirm = QMessageBox.question(
            self, "Remove voice", f"Remove the cloned voice '{custom.label}'? This can't be undone."
        )
        if confirm != QMessageBox.Yes:
            return
        remove_custom_voice(voice_id)
        self._refresh_voice_combo()

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
