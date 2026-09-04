from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from wellspoken.captions.qc import reconcile_tts_captions
from wellspoken.gui.tab_intro_outro import DEFAULT_LOGO_PATH
from wellspoken.gui.widgets import ProgressBar, make_hint_label
from wellspoken.media import audio_mix, ffmpeg_runner, silence, sync, timeline_edit, waveform
from wellspoken.media.render_pipeline import IntroOutroSpec, RenderOptions, render
from wellspoken.transcribe.whisper_engine import transcribe
from wellspoken.workers import BackgroundTask

WAVEFORM_WIDTH = 2000
SELECTION_BRUSH = pg.mkBrush(47, 111, 237, 80)
PENDING_CUT_BRUSH = pg.mkBrush(220, 60, 60, 100)
MARKER_PEN = pg.mkPen(color=(30, 160, 60), width=2)


class TimelineTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cut_ranges: list[tuple[float, float]] = []
        self.pending_markers: list[pg.LinearRegionItem] = []
        self.duration = 0.0
        self.markers: list[float] = []
        self.marker_lines: list[pg.InfiniteLine] = []

        layout = QVBoxLayout(self)
        layout.addWidget(make_hint_label(
            "Trim dead air or mistakes from the narration - drag the blue region to select a "
            "range and click Cut, or let Auto-Detect find pauses for you. Cuts remove that time "
            "from both the video and the narration together, so they stay in sync. The video "
            "preview below plays with the AI narration you're editing (not the recording's own "
            "audio track, if it has one) so what you hear here matches the waveform. Click "
            "\"Full Preview\" to see and hear the true final result - narration, captions, and "
            "music all composited exactly as export will produce them."
        ))

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(220)
        layout.addWidget(self.video_widget)

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.positionChanged.connect(self._on_position_changed)

        transport_row = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.setProperty("flat", True)
        self.play_btn.clicked.connect(self._toggle_play)
        reload_btn = QPushButton("Reload from Project")
        reload_btn.setProperty("flat", True)
        reload_btn.clicked.connect(self.reload)
        self.full_preview_btn = QPushButton("Full Preview (narration + captions + music)")
        self.full_preview_btn.clicked.connect(self._full_preview)
        self.app.register_busy_widget(self.full_preview_btn)
        transport_row.addWidget(self.play_btn)
        transport_row.addWidget(reload_btn)
        transport_row.addWidget(self.full_preview_btn)
        transport_row.addStretch(1)
        layout.addLayout(transport_row)

        self.plot = pg.PlotWidget(background="w")
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setMenuEnabled(False)
        self.plot.getAxis("left").hide()
        self.plot.setLabel("bottom", "seconds")
        self.plot.setMinimumHeight(160)
        self.plot.scene().sigMouseClicked.connect(self._on_plot_clicked)
        layout.addWidget(self.plot)

        self.region = pg.LinearRegionItem(values=(0, 1), brush=SELECTION_BRUSH)
        self.plot.addItem(self.region)

        self.playhead = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen("r", width=2))
        self.plot.addItem(self.playhead)

        actions_row = QHBoxLayout()
        auto_detect_btn = QPushButton("Auto-Detect Dead Air")
        auto_detect_btn.setProperty("flat", True)
        auto_detect_btn.clicked.connect(self._auto_detect)
        cut_btn = QPushButton("Cut Selected Range")
        cut_btn.setProperty("flat", True)
        cut_btn.clicked.connect(self._cut_selected)
        clear_btn = QPushButton("Clear Pending Cuts")
        clear_btn.setProperty("flat", True)
        clear_btn.clicked.connect(self._clear_pending)
        actions_row.addWidget(auto_detect_btn)
        actions_row.addWidget(cut_btn)
        actions_row.addWidget(clear_btn)
        actions_row.addStretch(1)
        layout.addLayout(actions_row)

        self.pending_label = QLabel("No pending cuts.")
        self.pending_label.setProperty("muted", True)
        layout.addWidget(self.pending_label)

        self.progress = ProgressBar()
        layout.addWidget(self.progress)

        self.apply_btn = QPushButton("Apply Cuts")
        self.apply_btn.clicked.connect(self._apply_cuts)
        layout.addWidget(self.apply_btn)

        layout.addWidget(make_hint_label(
            "Sync markers: play the video above and click \"Add Marker at Playhead\" at each moment "
            "your script should land on (a click, a menu opening, etc.) - they show as green lines "
            "on the timeline above, right alongside the narration waveform. Split your script (in "
            "Script -> Voice) into paragraphs with a blank line between them - paragraph 1 syncs to "
            "marker 1, paragraph 2 to marker 2, and so on. \"Generate Synced Narration\" re-synthesizes "
            "the whole script, padding each paragraph with silence so it starts exactly on its marker; "
            "it never speeds up speech to force a fit, so if a line runs long it'll flag which one so "
            "you can shorten it and try again."
        ))

        marker_row = QHBoxLayout()
        add_marker_btn = QPushButton("Add Marker at Playhead")
        add_marker_btn.setProperty("flat", True)
        add_marker_btn.clicked.connect(self._add_marker)
        remove_marker_btn = QPushButton("Remove Selected Marker")
        remove_marker_btn.setProperty("flat", True)
        remove_marker_btn.clicked.connect(self._remove_selected_marker)
        clear_markers_btn = QPushButton("Clear All Markers")
        clear_markers_btn.setProperty("flat", True)
        clear_markers_btn.clicked.connect(self._clear_markers)
        marker_row.addWidget(add_marker_btn)
        marker_row.addWidget(remove_marker_btn)
        marker_row.addWidget(clear_markers_btn)
        marker_row.addStretch(1)
        layout.addLayout(marker_row)

        self.marker_list = QListWidget()
        self.marker_list.setMaximumHeight(90)
        self.marker_list.itemDoubleClicked.connect(self._seek_to_marker_item)
        layout.addWidget(self.marker_list)

        self.generate_synced_btn = QPushButton("Generate Synced Narration")
        self.generate_synced_btn.clicked.connect(self._generate_synced_narration)
        self.app.register_busy_widget(self.generate_synced_btn)
        layout.addWidget(self.generate_synced_btn)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.duration == 0.0:
            self.reload()

    def refresh_from_project(self) -> None:
        """Drop cached waveform/preview state - called after New/Open Project,
        so a stale waveform from the previous project isn't shown. The next
        time this tab becomes visible, showEvent() reloads from the (possibly
        different) project's own narration."""
        self.media_player.stop()
        self.plot.clear()
        self._clear_pending()
        self.marker_lines.clear()
        self.markers = []
        self.marker_list.clear()
        self.duration = 0.0

    def reload(self) -> None:
        project = self.app.project
        if not project.narration_audio or not Path(project.narration_audio).exists():
            QMessageBox.information(
                self, "No narration", "Generate voice (Script -> Voice) or transcribe audio first."
            )
            return

        self.progress.start("Loading waveform...")

        def work(report):
            samples = waveform.extract_pcm(project.narration_audio, sample_rate=8000)
            dur = ffmpeg_runner.media_duration(project.narration_audio)
            # The scrub/edit preview must play the narration being edited, not
            # whatever audio happens to already be embedded in source_video
            # (e.g. the user's own mic voice from screen recording) - those
            # are frequently different tracks. resolve_narration_for_render()
            # already encodes the one case where they're intentionally the
            # same file (the transcribe workflow, narration_audio ==
            # source_video) and no muxing is needed.
            narration_for_render = project.resolve_narration_for_render()
            if narration_for_render and project.source_video:
                report("Syncing preview audio to narration...")
                proxy = audio_mix.replace_narration(
                    project.source_video, narration_for_render, self.app.scratch_dir() / "timeline_preview_proxy.mp4"
                )
                preview_source = str(proxy)
            else:
                preview_source = project.source_video
            return samples, dur, preview_source

        def done(result):
            samples, dur, preview_source = result
            self.duration = dur
            self._render_waveform(samples, dur)
            self._clear_pending()
            self.markers = list(project.sync_markers)
            self._redraw_marker_lines()
            self._refresh_marker_list()
            if preview_source:
                self.media_player.setSource(QUrl.fromLocalFile(preview_source))
            self.progress.stop("")

        def error(tb: str) -> None:
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Could not load waveform", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def _full_preview(self) -> None:
        """Renders and plays the true composited output - narration mixed
        in, captions burned, background music mixed - using the exact same
        render_pipeline.render() the real Export tab uses (minus intro/outro
        and aspect reformatting, which don't affect what's being trimmed
        here), so this can never drift from what exporting will actually
        produce. This is the "does it actually sound/look right before I
        commit to exporting" check the plain waveform-and-cuts view alone
        can't answer."""
        if self.app._busy_count > 0:
            QMessageBox.information(self, "Busy", "Another operation is in progress - please wait for it to finish.")
            return
        project = self.app.project
        if not project.source_video:
            QMessageBox.information(self, "No video", "Select a screen recording in the Project tab first.")
            return
        if not project.segments:
            QMessageBox.information(
                self, "No captions", "Generate voice or transcribe narration first, so there's something to preview."
            )
            return

        self.progress.start("Rendering full preview...")
        scratch = self.app.scratch_dir()

        def work(report):
            opts = RenderOptions(
                source_video=project.source_video,
                narration_wav=project.resolve_narration_for_render(),
                segments=project.segments,
                intro=IntroOutroSpec(kind="none"),
                outro=IntroOutroSpec(kind="none"),
                caption_mode="burned_in",  # always show captions in this preview, regardless of export setting
                output_dir=str(scratch),
                scratch_dir=str(scratch / "timeline_full_preview_tmp"),
                caption_style=project.caption_style,
                background_music=project.background_music_path,
                background_music_volume=project.background_music_volume,
                watermark_path=(project.logo_path or DEFAULT_LOGO_PATH) if project.watermark_enabled else None,
                watermark_position=project.watermark_position,
            )
            result = render(opts, on_progress=report)
            return result["video"]

        def done(video_path):
            self.app.set_busy(False)
            self.progress.stop("Full preview ready.")
            self.media_player.setSource(QUrl.fromLocalFile(video_path))
            self.media_player.play()
            self.play_btn.setText("Pause")

        def error(tb: str) -> None:
            self.app.set_busy(False)
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Full preview failed", tb)

        self.app.set_busy(True)
        BackgroundTask(self, work, done, on_error=error, on_progress=self.progress.set_message).start()

    def _render_waveform(self, samples: np.ndarray, duration: float) -> None:
        self.plot.clear()
        mins, maxes = waveform.peak_pairs(samples, WAVEFORM_WIDTH)
        times = np.linspace(0, duration, len(maxes)) if len(maxes) else np.zeros(0)
        pen = pg.mkPen(color=(47, 111, 237), width=1)
        fill = pg.mkBrush(47, 111, 237, 120)
        self.plot.plot(times, maxes, pen=pen, fillLevel=0, brush=fill)
        self.plot.plot(times, mins, pen=pen, fillLevel=0, brush=fill)
        self.plot.setXRange(0, max(duration, 0.1))
        self.plot.setYRange(-32768, 32768)
        self.region.setBounds((0, duration))
        self.region.setRegion((0, min(1.0, duration)))
        self.plot.addItem(self.region)
        self.plot.addItem(self.playhead)

    def _on_plot_clicked(self, event) -> None:
        pos = event.scenePos()
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        point = self.plot.getPlotItem().vb.mapSceneToView(pos)
        t = max(0.0, min(point.x(), self.duration))
        self.media_player.setPosition(int(t * 1000))

    def _on_position_changed(self, position_ms: int) -> None:
        self.playhead.setPos(position_ms / 1000.0)

    def _toggle_play(self) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("Play")
        else:
            self.media_player.play()
            self.play_btn.setText("Pause")

    def _auto_detect(self) -> None:
        project = self.app.project
        if not project.narration_audio:
            QMessageBox.information(self, "No narration", "Load a project with narration first.")
            return
        self.progress.start("Detecting dead air...")

        def work(report):
            return silence.detect_silence(project.narration_audio)

        def done(ranges):
            self.progress.stop(f"Found {len(ranges)} possible dead-air range(s) - review before applying.")
            for r in ranges:
                self._add_pending_cut(r)

        def error(tb: str) -> None:
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Detection failed", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def _cut_selected(self) -> None:
        start, end = self.region.getRegion()
        if end - start < 0.05:
            QMessageBox.information(self, "Selection too small", "Drag the blue region to select a range first.")
            return
        self._add_pending_cut((start, end))

    def _add_pending_cut(self, rng: tuple[float, float]) -> None:
        start, end = rng
        marker = pg.LinearRegionItem(values=(start, end), movable=False, brush=PENDING_CUT_BRUSH)
        marker.setZValue(-10)
        self.plot.addItem(marker)
        self.cut_ranges.append(rng)
        self.pending_markers.append(marker)
        self._refresh_pending_label()

    def _clear_pending(self) -> None:
        for m in self.pending_markers:
            self.plot.removeItem(m)
        self.pending_markers.clear()
        self.cut_ranges.clear()
        self._refresh_pending_label()

    def _refresh_pending_label(self) -> None:
        if not self.cut_ranges:
            self.pending_label.setText("No pending cuts.")
        else:
            total = sum(e - s for s, e in self.cut_ranges)
            self.pending_label.setText(f"{len(self.cut_ranges)} pending cut(s), removing {total:.1f}s total.")

    def _apply_cuts(self) -> None:
        if not self.cut_ranges:
            QMessageBox.information(self, "No cuts", "Select or detect at least one range to cut first.")
            return
        project = self.app.project
        if not project.source_video:
            QMessageBox.information(self, "No video", "Select a screen recording in the Project tab first.")
            return

        self.media_player.stop()
        self.progress.start("Applying cuts...")
        self.apply_btn.setEnabled(False)
        cut_ranges = list(self.cut_ranges)

        def work(report):
            return timeline_edit.apply_cuts(
                project.source_video,
                project.narration_audio,
                project.segments,
                cut_ranges,
                self.app.scratch_dir() / "timeline",
            )

        def done(result):
            new_video, new_narration, new_segments = result
            project.source_video = str(new_video)
            project.narration_audio = str(new_narration)
            project.segments = new_segments
            self.app.tab_project._refresh_video_info(str(new_video))
            self.apply_btn.setEnabled(True)
            self.progress.stop("Cuts applied.")
            self.reload()

        def error(tb: str) -> None:
            self.apply_btn.setEnabled(True)
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Apply failed", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def _add_marker(self) -> None:
        if self.duration <= 0:
            QMessageBox.information(self, "No video loaded", "Load a project with a video first.")
            return
        t = self.media_player.position() / 1000.0
        self.markers.append(t)
        self.markers.sort()
        self.app.project.sync_markers = list(self.markers)
        self._redraw_marker_lines()
        self._refresh_marker_list()

    def _remove_selected_marker(self) -> None:
        row = self.marker_list.currentRow()
        if row < 0:
            return
        del self.markers[row]
        self.app.project.sync_markers = list(self.markers)
        self._redraw_marker_lines()
        self._refresh_marker_list()

    def _clear_markers(self) -> None:
        self.markers = []
        self.app.project.sync_markers = []
        self._redraw_marker_lines()
        self._refresh_marker_list()

    def _redraw_marker_lines(self) -> None:
        for line in self.marker_lines:
            self.plot.removeItem(line)
        self.marker_lines = []
        for t in self.markers:
            line = pg.InfiniteLine(pos=t, angle=90, pen=MARKER_PEN, movable=False)
            self.plot.addItem(line)
            self.marker_lines.append(line)

    def _refresh_marker_list(self) -> None:
        self.marker_list.clear()
        for i, t in enumerate(self.markers, start=1):
            self.marker_list.addItem(f"Marker {i}: {t:.2f}s")

    def _seek_to_marker_item(self, item) -> None:
        row = self.marker_list.row(item)
        if 0 <= row < len(self.markers):
            self.media_player.setPosition(int(self.markers[row] * 1000))

    def _generate_synced_narration(self) -> None:
        if self.app._busy_count > 0:
            QMessageBox.information(self, "Busy", "Another operation is in progress - please wait for it to finish.")
            return
        project = self.app.project
        script = project.script_text.strip()
        if not script:
            QMessageBox.information(self, "No script", "Write a script in the Script -> Voice tab first.")
            return
        if not self.markers:
            QMessageBox.information(self, "No markers", "Add at least one marker on the timeline above first.")
            return
        paragraphs = sync.split_script_into_paragraphs(script)
        if len(paragraphs) < len(self.markers):
            QMessageBox.warning(
                self, "Fewer paragraphs than markers",
                f"The script has {len(paragraphs)} paragraph(s) (separated by a blank line) but there "
                f"are {len(self.markers)} marker(s) - the extra marker(s) at the end won't be used. "
                "Add blank lines to split the script further if you meant to sync more of it.",
            )

        self.progress.start("Loading voice model...")
        markers = list(self.markers)

        def work(report):
            engine = self.app.get_voice_engine(project.voice_name)
            wav_path, reports = sync.synthesize_with_markers(
                engine, script, markers, self.app.scratch_dir() / "narration.wav",
                self.app.scratch_dir() / "sync_pieces", on_progress=report,
            )
            report("Transcribing narration for timing + QC...")
            segments = transcribe(
                wav_path, source="tts",
                initial_prompt=self.app.lexicon.prompt_text(), lexicon=self.app.lexicon,
            )
            segments = reconcile_tts_captions(segments, script, lexicon=self.app.lexicon)
            return wav_path, segments, reports

        def done(result) -> None:
            self.app.set_busy(False)
            wav_path, segments, reports = result
            project.narration_audio = str(wav_path)
            project.segments = segments
            project.workflow = "script"
            self.progress.stop("Synced narration generated.")
            overruns = [r for r in reports if r.overrun > 0.05]
            if overruns:
                lines = "\n".join(f"- Line {r.index + 1} ran {r.overrun:.1f}s past its marker" for r in overruns)
                QMessageBox.warning(
                    self, "Some lines ran long",
                    "These lines took longer to say than the gap to their marker, so everything after "
                    "them was pushed later to stay in order (no audio was sped up or distorted):\n\n"
                    f"{lines}\n\nShorten them and regenerate for a tighter sync.",
                )
            self.reload()

        def error(tb: str) -> None:
            self.app.set_busy(False)
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Synced narration generation failed", tb)

        self.app.set_busy(True)
        BackgroundTask(self, work, done, on_error=error, on_progress=self.progress.set_message).start()
