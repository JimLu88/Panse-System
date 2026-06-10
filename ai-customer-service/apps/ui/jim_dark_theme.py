"""
Jim Design System · Dark Theme (Midnight Notebook)
PyQt6 QSS translation of jim.css dark mode tokens.
"""

JIM_DARK_QSS = """
/* ── Jim Dark · Global ────────────────────────── */
QWidget {
    background-color: #0D0D14;
    color: #F5EFE2;
    font-family: "Noto Sans SC", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}

/* ── Panels / GroupBox ──────────────────────────── */
QGroupBox {
    background-color: #1B1B28;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    padding: 20px 16px 12px 16px;
    margin-top: 12px;
    font-weight: 600;
    font-size: 14px;
    color: #F5EFE2;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 4px 14px;
    background-color: #25253A;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 9999px;
    color: #FAE254;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1px;
}

/* ── Inputs ─────────────────────────────────────── */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {
    background-color: #1B1B28;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 8px 12px;
    color: #F5EFE2;
    font-size: 13px;
    selection-background-color: #B8A2F0;
    selection-color: #0D0D14;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #FAE254;
    background-color: #25253A;
}
QLineEdit[echoMode="2"] {
    lineedit-password-character: 9679;
}

/* ── ComboBox ───────────────────────────────────── */
QComboBox {
    background-color: #1B1B28;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 8px 12px;
    color: #F5EFE2;
    font-size: 13px;
    min-height: 20px;
}
QComboBox:hover {
    border: 1px solid rgba(255, 255, 255, 0.16);
}
QComboBox:focus {
    border: 1px solid #FAE254;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #9E9A8F;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #1B1B28;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    color: #F5EFE2;
    selection-background-color: #25253A;
    selection-color: #FAE254;
    padding: 4px;
    outline: none;
}

/* ── Buttons ────────────────────────────────────── */
QPushButton {
    background-color: #25253A;
    color: #F5EFE2;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 9999px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
    min-height: 18px;
}
QPushButton:hover {
    background-color: #2F2F4A;
    border: 1px solid rgba(255, 255, 255, 0.16);
}
QPushButton:pressed {
    background-color: #1B1B28;
}
QPushButton:disabled {
    background-color: #15151F;
    color: rgba(245, 239, 226, 0.30);
    border: 1px solid rgba(255, 255, 255, 0.04);
}

/* ── CheckBox ───────────────────────────────────── */
QCheckBox {
    spacing: 8px;
    color: #F5EFE2;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid rgba(255, 255, 255, 0.16);
    background-color: #1B1B28;
}
QCheckBox::indicator:checked {
    background-color: #FAE254;
    border-color: #FAE254;
}
QCheckBox::indicator:hover {
    border-color: #FAE254;
}

/* ── Labels ─────────────────────────────────────── */
QLabel {
    color: #F5EFE2;
    background-color: transparent;
    font-size: 13px;
}

/* ── ScrollArea ─────────────────────────────────── */
QScrollArea {
    background-color: #0D0D14;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background-color: #0D0D14;
}
QScrollBar:vertical {
    background-color: #0D0D14;
    width: 8px;
    margin: 0;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: rgba(255, 255, 255, 0.12);
    min-height: 32px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background-color: rgba(255, 255, 255, 0.22);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0; background: none; border: none;
}
QScrollBar:horizontal {
    background-color: #0D0D14;
    height: 8px;
    margin: 0;
    border: none;
}
QScrollBar::handle:horizontal {
    background-color: rgba(255, 255, 255, 0.12);
    min-width: 32px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal:hover {
    background-color: rgba(255, 255, 255, 0.22);
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    width: 0; background: none; border: none;
}

/* ── Nav list (left sidebar) ────────────────────── */
QListWidget {
    background-color: #1B1B28;
    border: none;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
    outline: none;
    font-size: 14px;
    padding: 8px 0;
}
QListWidget::item {
    padding: 14px 20px;
    border-radius: 0;
    color: rgba(245, 239, 226, 0.65);
    border-left: 3px solid transparent;
}
QListWidget::item:hover {
    background-color: #25253A;
    color: #F5EFE2;
}
QListWidget::item:selected {
    background-color: rgba(250, 226, 84, 0.08);
    color: #FAE254;
    border-left: 3px solid #FAE254;
    font-weight: 600;
}

/* ── Splitter ───────────────────────────────────── */
QSplitter::handle {
    background-color: rgba(255, 255, 255, 0.04);
    width: 1px;
}

/* ── StackedWidget ──────────────────────────────── */
QStackedWidget {
    background-color: #0D0D14;
}

/* ── Dialog ─────────────────────────────────────── */
QDialog {
    background-color: #1B1B28;
    color: #F5EFE2;
}
QDialogButtonBox QPushButton {
    min-width: 80px;
}

/* ── MessageBox ─────────────────────────────────── */
QMessageBox {
    background-color: #1B1B28;
}
QMessageBox QLabel {
    color: #F5EFE2;
}

/* ── ToolTip ────────────────────────────────────── */
QToolTip {
    background-color: #25253A;
    color: #F5EFE2;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
}

/* ── MainWindow ─────────────────────────────────── */
QMainWindow {
    background-color: #0D0D14;
}

/* ── TextBrowser (activity log) ─────────────────── */
QTextBrowser {
    background-color: #1B1B28;
    color: rgba(245, 239, 226, 0.78);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 8px;
    padding: 8px;
    font-size: 12px;
    font-family: "Cascadia Code", "Consolas", "Noto Sans SC", monospace;
    selection-background-color: #B8A2F0;
    selection-color: #0D0D14;
}

/* ── DateEdit ───────────────────────────────────── */
QDateEdit {
    background-color: #1B1B28;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 8px 12px;
    color: #F5EFE2;
}
QDateEdit::drop-down {
    border: none;
    width: 24px;
}

/* ── TabWidget ──────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    background-color: #1B1B28;
}
QTabBar::tab {
    background-color: #1B1B28;
    color: rgba(245, 239, 226, 0.55);
    padding: 10px 20px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 13px;
    font-weight: 500;
}
QTabBar::tab:hover {
    color: #F5EFE2;
    background-color: #25253A;
}
QTabBar::tab:selected {
    color: #FAE254;
    border-bottom: 2px solid #FAE254;
}

/* ── Menu ───────────────────────────────────────── */
QMenuBar {
    background-color: #1B1B28;
    color: #F5EFE2;
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
QMenuBar::item:selected {
    background-color: #25253A;
}
QMenu {
    background-color: #1B1B28;
    color: #F5EFE2;
    border: 1px solid rgba(255, 255, 255, 0.08);
    padding: 4px;
}
QMenu::item:selected {
    background-color: #25253A;
    color: #FAE254;
}
"""

# ── Special button styles (applied inline) ──────
JIM_BTN_START = """
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FAE254, stop:1 #FF6B5A);
    color: #0D0D14;
    border: none;
    border-radius: 9999px;
    padding: 14px 28px;
    font-size: 15px;
    font-weight: 700;
    min-height: 24px;
}
QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #FBE86A, stop:1 #FF7D6E);
}
QPushButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #F5D90A, stop:1 #E55D4D);
}
QPushButton:disabled {
    background: #25253A;
    color: rgba(245, 239, 226, 0.30);
}
"""

JIM_BTN_STOP = """
QPushButton {
    background-color: #FF6B5A;
    color: #0D0D14;
    border: none;
    border-radius: 9999px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover { background-color: #FF7D6E; }
"""

JIM_BTN_HANDOFF = """
QPushButton {
    background-color: #7FE3B8;
    color: #0D0D14;
    border: none;
    border-radius: 9999px;
    padding: 14px 28px;
    font-size: 15px;
    font-weight: 700;
    min-height: 24px;
}
QPushButton:hover { background-color: #8FECCC; }
QPushButton:pressed { background-color: #6CD4A8; }
"""

JIM_BTN_ACCENT = """
QPushButton {
    background-color: #B8A2F0;
    color: #0D0D14;
    border: none;
    border-radius: 9999px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover { background-color: #C5B2F5; }
"""
