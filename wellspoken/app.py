from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMainWindow, QScrollArea, QTabWidget

from wellspoken.project import Project
from wellspoken.tts.lexicon import Lexicon

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LEXICON_PATH = ROOT_DIR / "assets" / "lexicon_default.json"
ICON_PATH = ROOT_DIR / "assets" / "branding" / "icon.ico"
TAB_ICONS_DIR = ROOT_DIR / "assets" / "branding" / "icons"
SCRATCH_DIR = ROOT_DIR / "scratch" / "gui_render"


def _tab_icon(name: str) -> QIcon:
    return QIcon(str(TAB_ICONS_DIR / f"{name}@2x.png"))


def _scrollable(widget) -> QScrollArea:
    """Wrap a tab's content in a scroll area so a tab with lots of controls
    (Export, especially, after Caption Style/Background Music/Also Export As
    were all added to it) scrolls internally instead of forcing the whole
    main window to grow past the screen's height to fit everything - which
    is what happened before this: the window became too tall to show its own
    bottom buttons even at "restored" (non-maximized) size."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(widget)
    return scroll


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WellSpoken")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1080, 760)

        self.project = Project()
        self.lexicon = Lexicon.load(DEFAULT_LEXICON_PATH)
        self._voice_engine = None
        self._voice_id = None

        # Buttons that read/write shared project state (narration audio file,
        # segments) via a background task. Only one may run at a time - e.g.
        # rendering while voice generation is still mid-write to narration.wav
        # would read a truncated/partial file. register_busy_widget() below
        # collects them; set_busy() disables all of them while any one runs.
        self._busy_widgets: list = []
        self._busy_count = 0

        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(18, 18))
        self.setCentralWidget(self.tabs)

        from wellspoken.gui.tab_export import ExportTab
        from wellspoken.gui.tab_intro_outro import IntroOutroTab
        from wellspoken.gui.tab_project import ProjectTab
        from wellspoken.gui.tab_record import RecordTab
        from wellspoken.gui.tab_script_voice import ScriptVoiceTab
        from wellspoken.gui.tab_timeline import TimelineTab
        from wellspoken.gui.tab_transcribe import TranscribeTab

        self.tab_record = RecordTab(self)
        self.tab_project = ProjectTab(self)
        self.tab_transcribe = TranscribeTab(self)
        self.tab_script_voice = ScriptVoiceTab(self)
        self.tab_timeline = TimelineTab(self)
        self.tab_intro_outro = IntroOutroTab(self)
        self.tab_export = ExportTab(self)

        # Maps each tab's actual widget to the QScrollArea it's wrapped in
        # (see _scrollable()) - QTabWidget's own pages are the wrappers, not
        # these objects directly, so switching tabs by object (show_tab)
        # needs this to find the right page.
        self._tab_wrappers: dict = {}

        for tab, icon_name, label in (
            (self.tab_record, "record", "Record"),
            (self.tab_project, "project", "Project"),
            (self.tab_transcribe, "transcribe", "Transcribe"),
            (self.tab_script_voice, "script_voice", "AI Voice"),
            (self.tab_timeline, "timeline", "Timeline"),
            (self.tab_intro_outro, "intro_outro", "Intro/Outro"),
            (self.tab_export, "export", "Export"),
        ):
            wrapper = _scrollable(tab)
            self._tab_wrappers[tab] = wrapper
            self.tabs.addTab(wrapper, _tab_icon(icon_name), label)

    def show_tab(self, tab_widget) -> None:
        """Switch to `tab_widget` (one of self.tab_record/tab_project/etc.) by
        object, same intent as QTabWidget.setCurrentWidget() - but the tab's
        own widget isn't a direct page of self.tabs anymore (it's nested in a
        QScrollArea wrapper, see _scrollable()), so this looks up that
        wrapper instead of calling setCurrentWidget(tab_widget) directly."""
        self.tabs.setCurrentWidget(self._tab_wrappers[tab_widget])

    def register_busy_widget(self, widget) -> None:
        """Register a button that must be disabled while any registered
        button's background task is running (see comment in __init__)."""
        self._busy_widgets.append(widget)

    def set_busy(self, busy: bool) -> None:
        if busy:
            self._busy_count += 1
        else:
            self._busy_count = max(0, self._busy_count - 1)
        enabled = self._busy_count == 0
        for w in self._busy_widgets:
            w.setEnabled(enabled)
            w.setToolTip("" if enabled else "Another operation is in progress - please wait for it to finish.")

    def refresh_all_tabs(self) -> None:
        """Sync every tab's widgets to the current self.project - called after
        New Project or Open Project swaps it out, so tabs stop showing stale
        content (an old script, an old waveform, old intro/outro choices) left
        over from whatever project was open before."""
        for tab in (
            self.tab_script_voice,
            self.tab_transcribe,
            self.tab_timeline,
            self.tab_intro_outro,
            self.tab_export,
        ):
            refresh = getattr(tab, "refresh_from_project", None)
            if refresh:
                refresh()

    def get_voice_engine(self, voice_id: str):
        from wellspoken.tts.voices import VOICE_ENGINES

        if self._voice_engine is None or self._voice_id != voice_id:
            engine_name = VOICE_ENGINES[voice_id]
            if engine_name == "kokoro":
                from wellspoken.tts.kokoro_engine import KokoroEngine

                self._voice_engine = KokoroEngine(voice_id, lexicon=self.lexicon)
            elif engine_name == "chatterbox":
                from wellspoken.tts.chatterbox_engine import ChatterboxEngine

                self._voice_engine = ChatterboxEngine(voice_id, lexicon=self.lexicon)
            else:
                raise ValueError(f"Unknown TTS engine: {engine_name!r}")
            self._voice_id = voice_id
        return self._voice_engine

    def scratch_dir(self) -> Path:
        SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        return SCRATCH_DIR
