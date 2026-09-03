from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class _Worker(QThread):
    progress = Signal(str)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, fn: Callable[[Callable[[str], None]], Any], parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:
        def report_progress(msg: str) -> None:
            self.progress.emit(msg)

        try:
            result = self.fn(report_progress)
            self.done.emit(result)
        except Exception:
            self.error.emit(traceback.format_exc())


class BackgroundTask(QObject):
    """Runs `fn` on a QThread and delivers progress/result/error back to the
    Qt main thread, so long TTS/whisper/ffmpeg calls never block the GUI.

    BackgroundTask must itself be a QObject (not a plain Python object): Qt's
    AutoConnection only becomes a proper cross-thread QueuedConnection when it
    can see the receiving *object's* thread affinity. A plain bound method has
    none, so signals emitted from the worker thread would otherwise invoke the
    callbacks - and touch GUI widgets - directly on the worker thread.

    Parenting to `parent_widget` ties both this object's and the worker's
    lifetime to the widget so they're cleaned up if the widget is destroyed.
    """

    def __init__(
        self,
        parent_widget,
        fn: Callable[[Callable[[str], None]], Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[str], None] | None = None,
    ):
        super().__init__(parent_widget)
        self._worker = _Worker(fn, parent=self)
        self._on_done = on_done
        self._on_error = on_error
        self._worker.done.connect(self._handle_done)
        self._worker.error.connect(self._handle_error)
        if on_progress:
            self._worker.progress.connect(on_progress)

    def start(self) -> None:
        self._worker.start()

    def _handle_done(self, result: Any) -> None:
        self._on_done(result)
        self._worker.deleteLater()
        self.deleteLater()

    def _handle_error(self, tb: str) -> None:
        if self._on_error:
            self._on_error(tb)
        self._worker.deleteLater()
        self.deleteLater()
