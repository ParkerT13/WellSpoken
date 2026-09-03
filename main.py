import os
import sys

# Must run before any other import. When launched via pythonw.exe (the
# no-console-window launch mode this app's own README recommends for the
# desktop shortcut, and generally whenever a Windows GUI app has no attached
# console), sys.stdout/sys.stderr are None rather than real file objects.
# Several dependencies assume otherwise and crash on import/use as a result -
# verified concretely: kokoro/__init__.py unconditionally does
# `logger.add(sys.stderr, ...)` at import time, and loguru raises
# `TypeError: Cannot log to objects of type 'NoneType'` when sys.stderr is
# None. Redirecting to the null device (rather than e.g. io.StringIO, which
# would accumulate unbounded log text in memory over a long session) keeps
# every such library happy without spending memory on discarded output.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from wellspoken.app import App
from wellspoken.gui.style import STYLESHEET

ICON_PATH = Path(__file__).resolve().parent / "assets" / "branding" / "icon.ico"

if sys.platform == "win32":
    # Without this, Windows groups the taskbar icon under the generic Python
    # interpreter icon instead of WellSpoken's own icon.
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WellSpoken.App")
    except Exception:
        pass

if __name__ == "__main__":
    qt_app = QApplication(sys.argv)
    qt_app.setStyleSheet(STYLESHEET)
    qt_app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = App()
    window.show()
    sys.exit(qt_app.exec())
