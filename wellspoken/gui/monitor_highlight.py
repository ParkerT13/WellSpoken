from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

BORDER_COLOR = QColor(255, 204, 0)  # matches the yellow WGC itself draws around an actively-recording target
BORDER_WIDTH = 8


class MonitorHighlight(QWidget):
    """A borderless, click-through, always-on-top yellow outline shown around
    whichever monitor/window is currently selected on the Record tab - lets
    you confirm what will be captured before you hit Record, the same way
    Windows.Graphics.Capture's own native border confirms it once recording
    is actually running. Never darkens or blocks the screen underneath -
    WA_TransparentForMouseEvents means clicks pass straight through, so it
    doesn't get in the way of using that monitor normally."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        pen = QPen(BORDER_COLOR, BORDER_WIDTH)
        pen.setJoinStyle(Qt.MiterJoin)
        painter.setPen(pen)
        half = BORDER_WIDTH // 2
        painter.drawRect(self.rect().adjusted(half, half, -half - 1, -half - 1))
