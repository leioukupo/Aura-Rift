from __future__ import annotations


# Shared color tokens for the two themes (kept in one place so the two
# stylesheets stay in sync).  Palette picks a tealish accent that reads well
# against a cool slate dark surface and a warm light surface, complemented by
# the orange lightbulb used for theme switching.
def stylesheet(theme: str) -> str:
    if theme == "light":
        return _LIGHT
    return _DARK


_DARK = """
* {
    font-family: "Noto Sans CJK SC", "Microsoft YaHei", "Source Han Sans SC", "Segoe UI", sans-serif;
    font-size: 14px;
    outline: 0;
}
QMainWindow, QDialog, QWidget {
    background: #1c1e24;
    color: #e6e8ee;
}
QToolTip {
    background: #111317;
    color: #e6e8ee;
    border: 1px solid #2b2f38;
    padding: 5px 8px;
    border-radius: 4px;
}
#titleBar {
    background: #16181d;
    border: 0;
    border-bottom: 1px solid #23263a;
}
#sideBar {
    background: #15171c;
    border: 0;
    border-right: 1px solid #23263a;
}
#settingsNav {
    background: #191b21;
    border: 0;
    border-right: 1px solid #23263a;
}
#navButton {
    border: 0;
    border-radius: 8px;
    color: #aab2c0;
    padding: 10px 6px;
    text-align: center;
}
#navButton:hover {
    background: #232632;
    color: #e6e8ee;
}
#navButton:checked {
    background: #232632;
    color: #e6e8ee;
    border: 0;
    border-left: 3px solid #38bdb2;
}
#navButtonAccent {
    border: 0;
    border-radius: 8px;
    color: #ff93a6;
    padding: 10px 6px;
}
#navButtonAccent:hover {
    background: #232632;
}
#pageHeader {
    background: #1c1e24;
    border: 0;
    border-bottom: 1px solid #262a33;
}
#hero {
    border: 1px solid #2a3548;
    border-radius: 12px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #11403b, stop:0.5 #1b2f48, stop:1 #3a2336);
}
#card, QGroupBox {
    background: #23262f;
    border: 1px solid #2c303b;
    border-radius: 10px;
    padding: 6px;
}
#card:hover {
    border: 1px solid #38bdb2;
}
#softCard {
    background: #21232a;
    border: 1px solid #2a2d36;
    border-radius: 8px;
}
QLabel { background: transparent; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {
    background: #181a20;
    color: #e6e8ee;
    border: 1px solid #2f333e;
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: #38bdb2;
    selection-color: #111317;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QComboBox:focus, QSpinBox:focus {
    border: 1px solid #38bdb2;
}
QComboBox::drop-down {
    border: 0;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #1f222a;
    color: #e6e8ee;
    border: 1px solid #2f333e;
    border-radius: 6px;
    selection-background-color: #2a433f;
    selection-color: #e6e8ee;
    outline: 0;
    padding: 4px;
}
QPlainTextEdit#console {
    background: #0c0d11;
    color: #d6e2d0;
    border: 0;
    border-top: 1px solid #23263a;
    font-family: "JetBrains Mono", "Cascadia Code", "Consolas", "Noto Color Emoji", "Noto Sans Mono CJK SC", monospace;
}
QPushButton {
    background: #2b2f3a;
    color: #e6e8ee;
    border: 1px solid #393e4c;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton:hover {
    background: #353a47;
    border: 1px solid #4a4f5e;
}
QPushButton:pressed {
    background: #404554;
}
QPushButton:disabled {
    color: #5b606e;
    background: #252830;
    border: 1px solid #2c303a;
}
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38bdb2, stop:1 #2a9d8f);
    color: #0b1012;
    border: 0;
    font-size: 17px;
    font-weight: 600;
    padding: 14px 26px;
    border-radius: 10px;
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #46cec1, stop:1 #32ae9f);
}
QPushButton#primary:pressed {
    background: #2a9d8f;
}
QPushButton#danger {
    background: #7c3242;
    color: #ffd9de;
    border: 0;
    border-radius: 8px;
}
QPushButton#danger:hover {
    background: #923a4d;
}
QPushButton#danger:pressed {
    background: #532633;
}
QPushButton#danger:disabled {
    background: #2c2226;
    color: #6e5a60;
}
QPushButton#flat {
    background: transparent;
    border: 0;
    padding: 6px 10px;
    border-radius: 8px;
}
QPushButton#flat:hover {
    background: #232632;
}
QTableWidget {
    background: #21242b;
    alternate-background-color: #23262f;
    color: #e6e8ee;
    gridline-color: #2c303b;
    border: 1px solid #2c303b;
    border-radius: 8px;
    selection-background-color: #2a433f;
    selection-color: #e6e8ee;
    outline: 0;
}
QTableWidget::item {
    padding: 6px 8px;
    border: 0;
}
QTableWidget::item:selected {
    background: #2a433f;
}
QHeaderView {
    background: transparent;
    border: 0;
}
QHeaderView::section {
    background: #1b1d24;
    color: #aab2c0;
    border: 0;
    border-right: 1px solid #2c303b;
    border-bottom: 1px solid #2c303b;
    padding: 9px 10px;
    font-weight: 600;
}
QHeaderView::section:first { border-top-left-radius: 8px; }
QHeaderView::section:last { border-top-right-radius: 8px; }
QTabWidget::pane {
    border: 0;
    border-top: 1px solid #262a33;
    background: transparent;
}
QTabWidget::tab-bar { alignment: left; }
QTabBar::tab {
    background: transparent;
    color: #aab2c0;
    padding: 11px 20px;
    border: 0;
    border-bottom: 3px solid transparent;
    margin-right: 4px;
}
QTabBar::tab:hover {
    color: #e6e8ee;
    border-bottom: 3px solid #3a4150;
}
QTabBar::tab:selected {
    color: #e6e8ee;
    border-bottom: 3px solid #38bdb2;
}
QListWidget {
    background: #1a1c22;
    color: #e6e8ee;
    border: 0;
    border-top: 1px solid #2c303b;
    border-radius: 8px;
}
QListWidget::item {
    padding: 7px 10px;
    border-bottom: 1px solid #262a33;
}
QListWidget::item:hover {
    background: #232632;
}
QListWidget::item:selected {
    background: #2a433f;
    color: #e6e8ee;
}
QListWidget#breadcrumb { border: 1px solid #2c303b; }
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #3b4050;
    border-radius: 5px;
    min-height: 34px;
}
QScrollBar::handle:vertical:hover {
    background: #4a5163;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 11px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #3b4050;
    border-radius: 5px;
    min-width: 34px;
}
QScrollBar::handle:horizontal:hover {
    background: #4a5163;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #3b4050;
    background: #181a20;
    border-radius: 4px;
}
QCheckBox::indicator:hover {
    border: 1px solid #38bdb2;
}
QCheckBox::indicator:checked {
    background: #38bdb2;
    border: 1px solid #38bdb2;
    image: none;
}
QMessageBox {
    background: #1c1e24;
}
QMessageBox QLabel {
    color: #e6e8ee;
    min-width: 260px;
}
QGroupBox {
    margin-top: 12px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    color: #aab2c0;
    font-weight: 600;
}
QMenuBar {
    background: transparent;
    color: #e6e8ee;
}
QFrame#hr {
    background: #2c303b;
    border: 0;
    max-height: 1px;
}
QFrame#divider {
    background: #2c303b;
    border: 0;
}

/* status badge used on the console header */
QLabel#statusBadge, QLabel#statusRunning, QLabel#statusError {
    border-radius: 10px;
    padding: 5px 14px;
    min-width: 96px;
    font-weight: 600;
}
QLabel#statusBadge { background: #2c303b; color: #aab2c0; }
QLabel#statusRunning { background: #1f443d; color: #62e0d2; }
QLabel#statusError { background: #44202a; color: #ff93a6; }
QLabel#footnote { color: #868d9b; font-size: 12px; }
QLabel#mutedTitle { color: #868d9b; font-size: 12px; }
"""

_LIGHT = """
* {
    font-family: "Noto Sans CJK SC", "Microsoft YaHei", "Source Han Sans SC", "Segoe UI", sans-serif;
    font-size: 14px;
    outline: 0;
}
QMainWindow, QDialog, QWidget {
    background: #f6f7f9;
    color: #1f2329;
}
QToolTip {
    background: #ffffff;
    color: #1f2329;
    border: 1px solid #d8dce3;
    padding: 5px 8px;
    border-radius: 4px;
}
#titleBar {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid #e3e6ec;
}
#sideBar {
    background: #ffffff;
    border: 0;
    border-right: 1px solid #e3e6ec;
}
#settingsNav {
    background: #fbfbfc;
    border: 0;
    border-right: 1px solid #e3e6ec;
}
#navButton {
    border: 0;
    border-radius: 8px;
    color: #5b6472;
    padding: 10px 6px;
    text-align: center;
}
#navButton:hover {
    background: #eef0f4;
    color: #1f2329;
}
#navButton:checked {
    background: #eef0f4;
    color: #1f2329;
    border: 0;
    border-left: 3px solid #1aa090;
}
#navButtonAccent {
    border: 0;
    border-radius: 8px;
    color: #c72f4d;
    padding: 10px 6px;
}
#navButtonAccent:hover {
    background: #eef0f4;
}
#pageHeader {
    background: #ffffff;
    border: 0;
    border-bottom: 1px solid #e3e6ec;
}
#hero {
    border: 1px solid #dde3ee;
    border-radius: 12px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #d3ede9, stop:0.5 #e3eef6, stop:1 #f3e4ea);
}
#card, QGroupBox {
    background: #ffffff;
    border: 1px solid #e3e6ec;
    border-radius: 10px;
    padding: 6px;
}
#card:hover {
    border: 1px solid #1aa090;
}
#softCard {
    background: #ffffff;
    border: 1px solid #e6e8ee;
    border-radius: 8px;
}
QLabel { background: transparent; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {
    background: #ffffff;
    color: #1f2329;
    border: 1px solid #d3d7df;
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: #1aa090;
    selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QComboBox:focus, QSpinBox:focus {
    border: 1px solid #1aa090;
}
QComboBox::drop-down {
    border: 0;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    color: #1f2329;
    border: 1px solid #d3d7df;
    border-radius: 6px;
    selection-background-color: #eef0f4;
    selection-color: #1f2329;
    outline: 0;
    padding: 4px;
}
QPlainTextEdit#console {
    background: #101116;
    color: #d6e2d0;
    border: 0;
    border-top: 1px solid #e3e6ec;
    font-family: "JetBrains Mono", "Cascadia Code", "Consolas", "Noto Color Emoji", "Noto Sans Mono CJK SC", monospace;
}
QPushButton {
    background: #ffffff;
    color: #2a2f37;
    border: 1px solid #d3d7df;
    border-radius: 8px;
    padding: 8px 16px;
}
QPushButton:hover {
    background: #f3f5f8;
    border: 1px solid #c2c7d0;
}
QPushButton:pressed {
    background: #e9edf2;
}
QPushButton:disabled {
    color: #b3b9c4;
    background: #f0f1f4;
    border: 1px solid #e3e6ec;
}
QPushButton#primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1aa090, stop:1 #0f8a7c);
    color: #ffffff;
    border: 0;
    font-size: 17px;
    font-weight: 600;
    padding: 14px 26px;
    border-radius: 10px;
}
QPushButton#primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #20b8a6, stop:1 #168f80);
}
QPushButton#primary:pressed {
    background: #0f8a7c;
}
QPushButton#danger {
    background: #d34a5e;
    color: #ffffff;
    border: 0;
    border-radius: 8px;
}
QPushButton#danger:hover {
    background: #e05a6c;
}
QPushButton#danger:pressed {
    background: #b74052;
}
QPushButton#danger:disabled {
    background: #f4dfe2;
    color: #c7a3aa;
}
QPushButton#flat {
    background: transparent;
    border: 0;
    padding: 6px 10px;
    border-radius: 8px;
}
QPushButton#flat:hover {
    background: #eef0f4;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f7f8fa;
    color: #1f2329;
    gridline-color: #eef0f4;
    border: 1px solid #e3e6ec;
    border-radius: 8px;
    selection-background-color: #d9efec;
    selection-color: #1f2329;
    outline: 0;
}
QTableWidget::item {
    padding: 6px 8px;
    border: 0;
}
QTableWidget::item:selected {
    background: #d9efec;
}
QHeaderView {
    background: transparent;
    border: 0;
}
QHeaderView::section {
    background: #f3f5f8;
    color: #5b6472;
    border: 0;
    border-right: 1px solid #e3e6ec;
    border-bottom: 1px solid #e3e6ec;
    padding: 9px 10px;
    font-weight: 600;
}
QHeaderView::section:first { border-top-left-radius: 8px; }
QHeaderView::section:last { border-top-right-radius: 8px; }
QTabWidget::pane {
    border: 0;
    border-top: 1px solid #e3e6ec;
    background: transparent;
}
QTabWidget::tab-bar { alignment: left; }
QTabBar::tab {
    background: transparent;
    color: #5b6472;
    padding: 11px 20px;
    border: 0;
    border-bottom: 3px solid transparent;
    margin-right: 4px;
}
QTabBar::tab:hover {
    color: #1f2329;
    border-bottom: 3px solid #d3d7df;
}
QTabBar::tab:selected {
    color: #1f2329;
    border-bottom: 3px solid #1aa090;
}
QListWidget {
    background: #ffffff;
    color: #1f2329;
    border: 0;
    border-top: 1px solid #e3e6ec;
    border-radius: 8px;
}
QListWidget::item {
    padding: 7px 10px;
    border-bottom: 1px solid #eef0f4;
}
QListWidget::item:hover {
    background: #f3f5f8;
}
QListWidget::item:selected {
    background: #d9efec;
    color: #1f2329;
}
QListWidget#breadcrumb { border: 1px solid #e3e6ec; }
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #c2c7d0;
    border-radius: 5px;
    min-height: 34px;
}
QScrollBar::handle:vertical:hover {
    background: #a9b0bb;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 11px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #c2c7d0;
    border-radius: 5px;
    min-width: 34px;
}
QScrollBar::handle:horizontal:hover {
    background: #a9b0bb;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid #c2c7d0;
    background: #ffffff;
    border-radius: 4px;
}
QCheckBox::indicator:hover {
    border: 1px solid #1aa090;
}
QCheckBox::indicator:checked {
    background: #1aa090;
    border: 1px solid #1aa090;
    image: none;
}
QMessageBox {
    background: #ffffff;
}
QMessageBox QLabel {
    color: #1f2329;
    min-width: 260px;
}
QGroupBox {
    margin-top: 12px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    color: #5b6472;
    font-weight: 600;
}
QFrame#hr {
    background: #e3e6ec;
    border: 0;
    max-height: 1px;
}
QFrame#divider {
    background: #e3e6ec;
    border: 0;
}

/* status badge used on the console header */
QLabel#statusBadge, QLabel#statusRunning, QLabel#statusError {
    border-radius: 10px;
    padding: 5px 14px;
    min-width: 96px;
    font-weight: 600;
}
QLabel#statusBadge { background: #eef0f4; color: #5b6472; }
QLabel#statusRunning { background: #d9efec; color: #0f8a7c; }
QLabel#statusError { background: #fbe0e4; color: #b74052; }
QLabel#footnote { color: #7a8290; font-size: 12px; }
QLabel#mutedTitle { color: #7a8290; font-size: 12px; }
"""
