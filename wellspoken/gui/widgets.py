from __future__ import annotations

from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget


def make_hint_label(text: str) -> QLabel:
    """A short instructional line shown at the top of a tab."""
    label = QLabel(text)
    label.setProperty("muted", True)
    label.setWordWrap(True)
    return label


class ProgressBar(QWidget):
    """A labeled indeterminate progress bar you can start/stop and re-message."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.label = QLabel("")
        self.label.setProperty("muted", True)
        layout.addWidget(self.label)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

    def start(self, message: str = "Working...") -> None:
        self.label.setText(message)
        self.bar.setVisible(True)

    def set_message(self, message: str) -> None:
        self.label.setText(message)

    def stop(self, message: str = "") -> None:
        self.bar.setVisible(False)
        self.label.setText(message)
