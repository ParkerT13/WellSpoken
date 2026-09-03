ACCENT = "#E67517"
ACCENT_HOVER = "#C4620F"
ACCENT_SOFT = "#FDECDD"
BG = "#F6F5F3"
PANEL_BG = "#FFFFFF"
BORDER = "#E3E1DD"
TEXT = "#1D1F23"
MUTED_TEXT = "#6B7280"
FLAG = "#B3261E"

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", sans-serif;
    font-size: 10.5pt;
    color: {TEXT};
}}

QMainWindow, QWidget {{
    background: {BG};
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    background: {PANEL_BG};
    top: 6px;
}}

QTabBar {{
    background: transparent;
}}

QTabBar::tab {{
    background: transparent;
    color: {MUTED_TEXT};
    padding: 9px 18px;
    margin: 3px 3px 0 0;
    border-radius: 9px;
    font-weight: 500;
}}

QTabBar::tab:selected {{
    background: {ACCENT};
    color: white;
    font-weight: 700;
}}

QTabBar::tab:hover:!selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}

QPushButton {{
    background: {ACCENT};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}}

QPushButton:hover {{
    background: {ACCENT_HOVER};
}}

QPushButton:disabled {{
    background: #E3D9CE;
    color: #B9AFA3;
}}

QPushButton[flat="true"] {{
    background: transparent;
    color: {ACCENT};
    border: 1px solid {BORDER};
    font-weight: 600;
}}

QPushButton[flat="true"]:hover {{
    background: {ACCENT_SOFT};
    border: 1px solid {ACCENT};
}}

QLineEdit, QTextEdit, QListWidget, QComboBox {{
    background: {PANEL_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}

QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border: 1.5px solid {ACCENT};
}}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 12px;
    padding-top: 14px;
    font-weight: 700;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {TEXT};
}}

QProgressBar {{
    border: none;
    border-radius: 5px;
    background: {ACCENT_SOFT};
    height: 8px;
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 5px;
}}

QLabel[muted="true"] {{
    color: {MUTED_TEXT};
}}

QLabel[flagged="true"] {{
    color: {FLAG};
}}

QRadioButton, QCheckBox {{
    spacing: 8px;
    padding: 2px 0;
}}

QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 8px;
    border: 1.5px solid {MUTED_TEXT};
    background: {PANEL_BG};
}}

QRadioButton::indicator:checked {{
    border: 1.5px solid {ACCENT};
    background: qradialgradient(
        cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {ACCENT}, stop:0.5 {ACCENT}, stop:0.6 {PANEL_BG}, stop:1 {PANEL_BG}
    );
}}

QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1.5px solid {MUTED_TEXT};
    background: {PANEL_BG};
}}

QCheckBox::indicator:checked {{
    border: 1.5px solid {ACCENT};
    background: {ACCENT};
}}
"""
