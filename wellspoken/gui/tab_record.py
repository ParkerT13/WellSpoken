from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from wellspoken.gui.monitor_highlight import MonitorHighlight
from wellspoken.gui.region_picker import select_region
from wellspoken.gui.widgets import ProgressBar, make_hint_label
from wellspoken.media import recorder
from wellspoken.workers import BackgroundTask

RECORDINGS_DIR = Path.home() / "Documents" / "WellSpoken Recordings"


def _screen_for_point(x: int, y: int):
    """The QScreen containing a logical-space point, for devicePixelRatio lookup."""
    for screen in QApplication.screens():
        if screen.geometry().contains(x, y):
            return screen
    return QApplication.primaryScreen()


class RecordTab(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._session = None
        self._output_path: Path | None = None
        self._start_time: float | None = None
        self._elapsed_before_pause: float = 0.0
        self._region = None  # QRect, logical global screen coords, from region_picker
        self._monitor_highlight: MonitorHighlight | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(make_hint_label(
            "Record your screen right here - full screen, a specific window, or a region you "
            "draw - then click \"Use This Recording\" to send it straight into the Project tab."
        ))

        source_box = QGroupBox("Source")
        source_layout = QVBoxLayout(source_box)

        self.fullscreen_radio = QRadioButton("Full Screen")
        self.window_radio = QRadioButton("Window")
        self.region_radio = QRadioButton("Region")
        for r in (self.fullscreen_radio, self.window_radio, self.region_radio):
            source_layout.addWidget(r)

        self.monitor_row = QHBoxLayout()
        self.monitor_row.addWidget(QLabel("Monitor:"))
        self.monitor_combo = QComboBox()
        self._refresh_monitors()
        self.monitor_combo.currentIndexChanged.connect(self._update_monitor_highlight)
        self.monitor_row.addWidget(self.monitor_combo, stretch=1)
        self._monitor_widget = QWidget()
        self._monitor_widget.setLayout(self.monitor_row)
        source_layout.addWidget(self._monitor_widget)

        self.window_row = QHBoxLayout()
        self.window_combo = QComboBox()
        refresh_windows_btn = QPushButton("Refresh")
        refresh_windows_btn.setProperty("flat", True)
        refresh_windows_btn.clicked.connect(self._refresh_windows)
        self.window_row.addWidget(self.window_combo, stretch=1)
        self.window_row.addWidget(refresh_windows_btn)
        self._window_widget = QWidget()
        self._window_widget.setLayout(self.window_row)
        source_layout.addWidget(self._window_widget)

        self.region_row = QHBoxLayout()
        select_region_btn = QPushButton("Select Region...")
        select_region_btn.clicked.connect(self._pick_region)
        self.region_label = QLabel("No region selected.")
        self.region_label.setProperty("muted", True)
        self.region_row.addWidget(select_region_btn)
        self.region_row.addWidget(self.region_label)
        self._region_widget = QWidget()
        self._region_widget.setLayout(self.region_row)
        source_layout.addWidget(self._region_widget)

        layout.addWidget(source_box)

        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("Microphone:"))
        self.audio_combo = QComboBox()
        self.audio_combo.setMinimumWidth(220)
        self._refresh_audio_devices()
        options_row.addWidget(self.audio_combo)
        options_row.addWidget(QLabel("FPS:"))
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["30", "60"])
        options_row.addWidget(self.fps_combo)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        record_row = QHBoxLayout()
        self.record_btn = QPushButton("Record")
        self.record_btn.clicked.connect(self._toggle_record)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setProperty("flat", True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.elapsed_label = QLabel("")
        record_row.addWidget(self.record_btn)
        record_row.addWidget(self.pause_btn)
        record_row.addWidget(self.elapsed_label)
        record_row.addStretch(1)
        layout.addLayout(record_row)

        self.progress = ProgressBar()
        layout.addWidget(self.progress)

        self.output_label = QLabel("No recording yet.")
        self.output_label.setProperty("muted", True)
        layout.addWidget(self.output_label)

        self.use_recording_btn = QPushButton("Use This Recording")
        self.use_recording_btn.setEnabled(False)
        self.use_recording_btn.clicked.connect(self._use_recording)
        layout.addWidget(self.use_recording_btn)
        layout.addStretch(1)

        for r in (self.fullscreen_radio, self.window_radio, self.region_radio):
            r.toggled.connect(self._on_source_change)
        self.fullscreen_radio.setChecked(True)
        self._on_source_change()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def _refresh_monitors(self) -> None:
        self.monitor_combo.clear()
        # Order here MUST match recorder.list_monitors(), which is what
        # CaptureSpec.monitor_index indexes into (see that function's docstring
        # for why this can't just reuse QApplication.screens()'s order).
        for i, (x, y, w, h) in enumerate(recorder.list_monitors()):
            self.monitor_combo.addItem(f"Monitor {i + 1} ({w}x{h})", userData=i)

    def _refresh_windows(self) -> None:
        self.window_combo.clear()
        for hwnd, title in recorder.list_open_windows():
            self.window_combo.addItem(title, userData=hwnd)

    def _refresh_audio_devices(self) -> None:
        self.audio_combo.clear()
        self.audio_combo.addItem("None")
        try:
            self.audio_combo.addItems(recorder.list_audio_devices())
        except Exception:
            pass

    def _on_source_change(self) -> None:
        self._monitor_widget.setVisible(self.fullscreen_radio.isChecked())
        self._window_widget.setVisible(self.window_radio.isChecked())
        self._region_widget.setVisible(self.region_radio.isChecked())
        if self.window_radio.isChecked() and self.window_combo.count() == 0:
            self._refresh_windows()
        self._update_monitor_highlight()

    def _update_monitor_highlight(self, *_args) -> None:
        """Shows a yellow border around the currently-selected monitor, so
        it's clear which physical screen "Monitor 2" etc. actually refers to
        before recording starts - mirrors WGC's own native yellow border
        that appears once recording is actually running."""
        show_it = (
            self._session is None  # WGC's own border takes over once actually recording - don't double up
            and self.isVisible()
            and self.fullscreen_radio.isChecked()
        )
        monitor_index = self.monitor_combo.currentData() if show_it else None
        if monitor_index is None:
            self._hide_monitor_highlight()
            return
        monitors = recorder.list_monitors()
        if monitor_index >= len(monitors):
            self._hide_monitor_highlight()
            return
        x, y, w, h = monitors[monitor_index]
        if self._monitor_highlight is None:
            self._monitor_highlight = MonitorHighlight()
        self._monitor_highlight.setGeometry(x, y, w, h)
        self._monitor_highlight.show()

    def _hide_monitor_highlight(self) -> None:
        if self._monitor_highlight is not None:
            self._monitor_highlight.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_monitor_highlight()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._hide_monitor_highlight()

    def _pick_region(self) -> None:
        main_window = self.window()
        main_window.hide()
        QApplication.processEvents()
        try:
            rect = select_region()
        finally:
            main_window.show()
        if rect:
            self._region = rect
            self.region_label.setText(f"{rect.width()}x{rect.height()} at ({rect.x()}, {rect.y()})")
        else:
            self.region_label.setText("No region selected.")

    def _build_spec(self):
        if self.fullscreen_radio.isChecked():
            monitor_index = self.monitor_combo.currentData()
            if monitor_index is None:
                return None
            return recorder.CaptureSpec(kind="fullscreen", monitor_index=monitor_index)

        if self.window_radio.isChecked():
            hwnd = self.window_combo.currentData()
            if not hwnd:
                return None
            return recorder.CaptureSpec(kind="window", window_hwnd=hwnd)

        if self._region is None:
            return None
        return self._region_to_spec(self._region)

    def _region_to_spec(self, rect):
        """Translate a region_picker QRect (logical, global screen coords) into
        a CaptureSpec: which native monitor it's on (matching WGC's own index -
        see recorder.list_monitors()) and the crop box in that monitor's
        PHYSICAL pixel space (WGC's frame_buffer is physical pixels; Qt's
        coordinates are logical/DPI-scaled, so the DPI ratio must be applied -
        verified empirically, this is not a 1:1 mapping when scaling != 100%)."""
        monitors = recorder.list_monitors()
        cx, cy = rect.x(), rect.y()
        monitor_index = 0
        mx, my = 0, 0
        for i, (x, y, w, h) in enumerate(monitors):
            if x <= cx < x + w and y <= cy < y + h:
                monitor_index = i
                mx, my = x, y
                break

        screen = _screen_for_point(cx, cy)
        ratio = screen.devicePixelRatio() if screen else 1.0

        local_x = (cx - mx) * ratio
        local_y = (cy - my) * ratio
        width = rect.width() * ratio
        height = rect.height() * ratio

        return recorder.CaptureSpec(
            kind="region",
            monitor_index=monitor_index,
            x=int(local_x), y=int(local_y),
            width=int(width), height=int(height),
        )

    def _toggle_record(self) -> None:
        if self._session is None:
            self._start_record()
        else:
            self._stop_record()

    def _start_record(self) -> None:
        spec = self._build_spec()
        if spec is None:
            QMessageBox.information(self, "Nothing to record", "Pick a window or draw a region first.")
            return

        audio_device = None if self.audio_combo.currentText() == "None" else self.audio_combo.currentText()
        fps = int(self.fps_combo.currentText())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._output_path = RECORDINGS_DIR / f"recording_{timestamp}.mp4"

        try:
            self._session = recorder.start_recording(self._output_path, spec, audio_device, fps=fps)
        except Exception as exc:
            QMessageBox.critical(self, "Could not start recording", str(exc))
            return

        self._hide_monitor_highlight()  # WGC's own native border takes over now that capture is actually running
        self._elapsed_before_pause = 0.0
        self._start_time = time.time()
        self._timer.start(1000)
        self.record_btn.setText("Stop Recording")
        self.pause_btn.setText("Pause")
        self.pause_btn.setEnabled(True)
        self.use_recording_btn.setEnabled(False)
        self.output_label.setText(f"Recording to {self._output_path} ...")
        for w in (self.fullscreen_radio, self.window_radio, self.region_radio, self.audio_combo, self.fps_combo):
            w.setEnabled(False)

    def _tick(self) -> None:
        elapsed = int(self._elapsed_before_pause + (time.time() - self._start_time))
        self.elapsed_label.setText(f"{elapsed // 60:02d}:{elapsed % 60:02d}")

    def _toggle_pause(self) -> None:
        if self._session is None:
            return
        if self._session.paused:
            self._resume_record()
        else:
            self._pause_record()

    def _pause_record(self) -> None:
        self._timer.stop()
        self._elapsed_before_pause += time.time() - self._start_time
        session = self._session
        self.pause_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.progress.start("Pausing...")

        def work(report):
            recorder.pause_recording(session)

        def done(_result):
            self.progress.stop("")
            self.pause_btn.setText("Resume")
            self.pause_btn.setEnabled(True)
            self.record_btn.setEnabled(True)
            self.output_label.setText(f"Paused - {self._output_path}")

        def error(tb: str) -> None:
            self.progress.stop("Failed.")
            self.pause_btn.setEnabled(True)
            self.record_btn.setEnabled(True)
            QMessageBox.critical(self, "Pause failed", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def _resume_record(self) -> None:
        session = self._session
        self.pause_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.progress.start("Resuming...")

        def work(report):
            recorder.resume_recording(session)

        def done(_result):
            self.progress.stop("")
            self._start_time = time.time()
            self._timer.start(1000)
            self.pause_btn.setText("Pause")
            self.pause_btn.setEnabled(True)
            self.record_btn.setEnabled(True)
            self.output_label.setText(f"Recording to {self._output_path} ...")

        def error(tb: str) -> None:
            self.progress.stop("Failed.")
            self.pause_btn.setEnabled(True)
            self.record_btn.setEnabled(True)
            QMessageBox.critical(self, "Resume failed", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def _stop_record(self) -> None:
        self._timer.stop()
        session = self._session
        self.progress.start("Finalizing recording...")
        self.record_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)

        def work(report):
            recorder.stop_recording(session)

        def done(_result):
            self._session = None
            self.record_btn.setEnabled(True)
            self.record_btn.setText("Record")
            self.pause_btn.setEnabled(False)
            self.pause_btn.setText("Pause")
            self.progress.stop("")
            for w in (self.fullscreen_radio, self.window_radio, self.region_radio, self.audio_combo, self.fps_combo):
                w.setEnabled(True)
            if self._output_path and self._output_path.exists():
                self.output_label.setText(str(self._output_path))
                self.use_recording_btn.setEnabled(True)
            else:
                self.output_label.setText("Recording failed - no output file was created.")
            self._update_monitor_highlight()

        def error(tb: str) -> None:
            self.record_btn.setEnabled(True)
            self.record_btn.setText("Record")
            self.pause_btn.setEnabled(False)
            self.pause_btn.setText("Pause")
            self.progress.stop("Failed.")
            QMessageBox.critical(self, "Recording error", tb)

        BackgroundTask(self, work, done, on_error=error).start()

    def _use_recording(self) -> None:
        if not self._output_path or not self._output_path.exists():
            return
        self.app.project.source_video = str(self._output_path)
        self.app.tab_project._refresh_video_info(str(self._output_path))
        self.app.show_tab(self.app.tab_project)
