"""Colours and the application-wide Qt stylesheet."""

BG        = "#151516"
PANEL     = "#1c1c1e"
PANEL_ALT = "#232326"
SURFACE   = "#1a1a1b"
BORDER    = "#2d2d30"
TEXT      = "#d4d4d4"
MUTED     = "#8b8b8f"
DIM       = "#5c5c60"
ACCENT    = "#2f7fd0"
ACCENT_H  = "#3d8fe0"
CLAUDE    = "#7c5cd6"
CLAUDE_H  = "#9075e6"
OK        = "#2f6b2f"
DANGER    = "#8e3b38"
WARN      = "#e5c07b"
GOOD      = "#98c379"
BAD       = "#e06c75"
SELECT    = "#264f78"
ACTIVE_LINE = "#202024"
CODE_BG   = "#111214"

MONO = "Consolas"
UI_FONT = "Segoe UI"

QSS = f"""
* {{ font-family: "{UI_FONT}"; font-size: 12px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QToolBar {{ background: {PANEL}; border: none; padding: 4px 6px; spacing: 6px; }}
QToolBar QToolButton {{ padding: 4px 8px; border-radius: 4px; }}
QToolBar QToolButton:hover {{ background: {PANEL_ALT}; }}
QStatusBar {{ background: {ACCENT}; color: white; }}
QStatusBar QLabel {{ color: white; }}
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; color: {MUTED}; font-weight: bold; }}
QDockWidget::title {{ background: {PANEL}; padding: 6px 10px; text-align: left; }}
QDockWidget > QWidget {{ background: {PANEL}; }}
QSplitter::handle {{ background: {BG}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {SURFACE}; top: -1px; }}
QTabBar::tab {{ background: {PANEL_ALT}; color: {MUTED}; padding: 6px 12px; margin-right: 2px;
               border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QTabBar::tab:selected {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; border-bottom: none; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::close-button {{ image: none; subcontrol-position: right; }}
QTabBar QToolButton {{ background: {PANEL_ALT}; border: none; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 8px;
    selection-background-color: {SELECT}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QPlainTextEdit#code {{ background: {SURFACE}; border: none; font-family: "{MONO}"; font-size: 13px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; selection-background-color: {SELECT}; }}
QPushButton {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 12px; }}
QPushButton:hover {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {DIM}; }}
QPushButton#accent {{ background: {ACCENT}; border: none; color: white; font-weight: bold; }}
QPushButton#accent:hover {{ background: {ACCENT_H}; }}
QPushButton#claude {{ background: {CLAUDE}; border: none; color: white; font-weight: bold; }}
QPushButton#claude:hover {{ background: {CLAUDE_H}; }}
QPushButton#danger {{ background: {DANGER}; border: none; color: white; }}
QPushButton#danger:disabled {{ background: #2e2626; color: #6a5a5a; }}
QPushButton#claude:disabled, QPushButton#accent:disabled {{ background: #2c2c30; color: #6a6a6e; }}
QPushButton#ok {{ background: {OK}; border: none; color: white; }}
QPushButton#flat {{ background: transparent; border: none; color: {MUTED}; padding: 2px 6px; }}
QPushButton#flat:hover {{ color: {TEXT}; background: {PANEL_ALT}; }}
QTreeWidget, QTreeView, QTableWidget, QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; alternate-background-color: #1e1e20;
    selection-background-color: {SELECT}; outline: none; }}
QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{ background: {SELECT}; color: white; }}
QHeaderView::section {{ background: {PANEL_ALT}; color: {MUTED}; padding: 4px 6px; border: none; border-right: 1px solid {BORDER}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3a3a3e; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4a4a4e; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #3a3a3e; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QProgressBar {{ background: {BORDER}; border: none; border-radius: 3px; height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QProgressBar#warn::chunk {{ background: {WARN}; }}
QProgressBar#bad::chunk {{ background: {BAD}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#dim {{ color: {DIM}; font-size: 11px; }}
QLabel#h {{ color: {MUTED}; font-size: 11px; font-weight: bold; letter-spacing: 1px; }}
QFrame#card {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#bubble_user {{ background: #1f2a3a; border: 1px solid #2c3e57; border-radius: 10px; }}
QFrame#bubble_ai {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#composer {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#composer:focus-within {{ border: 1px solid {CLAUDE}; }}
QFrame#chip {{ background: {BORDER}; border-radius: 10px; }}
QMenu {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {SELECT}; }}
QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {BORDER}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px; border: 1px solid {BORDER}; background: {PANEL_ALT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
"""
