from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QDialog


class _RegionPicker(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self._start = None
        self._end = None
        self.selected_rect: Optional[QRect] = None

        geo = QRect()
        for screen in QApplication.screens():
            geo = geo.united(screen.geometry())
        self.setGeometry(geo)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self._start and self._end:
            rect = QRect(self._start, self._end).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor(47, 111, 237), 2))
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        self._start = event.position().toPoint()
        self._end = self._start
        self.update()

    def mouseMoveEvent(self, event):
        if self._start:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        self._end = event.position().toPoint()
        rect = QRect(self._start, self._end).normalized()
        if rect.width() > 5 and rect.height() > 5:
            self.selected_rect = rect
            self.accept()
        else:
            self.reject()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()


def select_region() -> Optional[QRect]:
    """Show a full-virtual-desktop overlay for a click-drag rectangle selection.
    Returns the selected QRect in screen coordinates, or None if cancelled."""
    picker = _RegionPicker()
    picker.show()
    result = picker.exec()
    return picker.selected_rect if result == QDialog.Accepted else None
