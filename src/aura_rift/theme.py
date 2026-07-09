from __future__ import annotations


def stylesheet(theme: str) -> str:
    dark = theme != "light"
    if dark:
        return """
        * { font-family: "Noto Sans CJK SC", "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 14px; }
        QMainWindow, QWidget { background: #202020; color: #f0f0f0; }
        #titleBar { background: #1f1f1f; border-bottom: 1px solid #2f2f2f; }
        #sideBar { background: #1b1b1b; border-right: 1px solid #303030; }
        #settingsNav { background: #1f1f1f; border-right: 1px solid #303030; }
        #navButton { border: 0; border-radius: 6px; color: #f2f2f2; padding: 8px 4px; text-align: center; }
        #navButton:hover { background: #303030; }
        #navButton:checked { background: #4a4a4a; border-left: 4px solid #cfcfcf; }
        #navButtonAccent { border: 0; border-radius: 6px; color: #ff93a6; padding: 8px 4px; }
        #navButtonAccent:hover { background: #303030; }
        #pageHeader { background: #303030; border-bottom: 1px solid #3c3c3c; }
        #hero { border-radius: 8px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #263748, stop:0.45 #182633, stop:1 #513842); }
        #card, QGroupBox { background: #2b2b2b; border: 1px solid #3a3a3a; border-radius: 6px; }
        #softCard { background: #292929; border: 1px solid #383838; border-radius: 6px; }
        QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {
            background: #252525; color: #f2f2f2; border: 1px solid #454545; border-radius: 5px; padding: 7px;
        }
        QPlainTextEdit#console { background: #050505; color: #e8e8e8; border: 0; font-family: "JetBrains Mono", "Consolas", monospace; }
        QPushButton {
            background: #3a3a3a; color: #f2f2f2; border: 1px solid #4a4a4a; border-radius: 6px; padding: 8px 14px;
        }
        QPushButton:hover { background: #444444; }
        QPushButton:pressed { background: #545454; }
        QPushButton#primary { background: #c7c7c7; color: #111111; border: 0; font-size: 20px; padding: 16px 28px; }
        QPushButton#danger { background: #a33f3f; border: 0; }
        QPushButton#flat { background: transparent; border: 0; }
        QTableWidget { background: #292929; alternate-background-color: #252525; gridline-color: #444444; border: 1px solid #3a3a3a; }
        QHeaderView::section { background: #303030; color: #f1f1f1; border: 1px solid #3e3e3e; padding: 8px; }
        QTabWidget::pane { border: 0; }
        QTabBar::tab { background: #2c2c2c; color: #f2f2f2; padding: 10px 18px; border: 1px solid #3a3a3a; }
        QTabBar::tab:selected { background: #343434; border-bottom: 3px solid #cfcfcf; }
        QScrollBar:vertical { background: #202020; width: 12px; }
        QScrollBar::handle:vertical { background: #666666; border-radius: 6px; min-height: 30px; }
        QCheckBox::indicator { width: 18px; height: 18px; }
        """

    return """
        * { font-family: "Noto Sans CJK SC", "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 14px; }
        QMainWindow, QWidget { background: #f4f4f4; color: #1e1e1e; }
        #titleBar { background: #ffffff; border-bottom: 1px solid #dddddd; }
        #sideBar { background: #fdfdfd; border-right: 1px solid #dddddd; }
        #settingsNav { background: #fafafa; border-right: 1px solid #dddddd; }
        #navButton { border: 0; border-radius: 6px; color: #222222; padding: 8px 4px; }
        #navButton:hover { background: #eeeeee; }
        #navButton:checked { background: #e2e2e2; border-left: 4px solid #555555; }
        #navButtonAccent { border: 0; border-radius: 6px; color: #c72f4d; padding: 8px 4px; }
        #navButtonAccent:hover { background: #eeeeee; }
        #pageHeader { background: #ffffff; border-bottom: 1px solid #dddddd; }
        #hero { border-radius: 8px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8e8f6, stop:0.45 #ecf4fb, stop:1 #f7dfe4); }
        #card, QGroupBox, #softCard { background: #ffffff; border: 1px solid #dddddd; border-radius: 6px; }
        QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {
            background: #ffffff; color: #1e1e1e; border: 1px solid #cfcfcf; border-radius: 5px; padding: 7px;
        }
        QPlainTextEdit#console { background: #111111; color: #f0f0f0; border: 0; font-family: "JetBrains Mono", "Consolas", monospace; }
        QPushButton {
            background: #ffffff; color: #222222; border: 1px solid #cccccc; border-radius: 6px; padding: 8px 14px;
        }
        QPushButton:hover { background: #f0f0f0; }
        QPushButton#primary { background: #222222; color: #ffffff; border: 0; font-size: 20px; padding: 16px 28px; }
        QPushButton#danger { background: #b64242; color: #ffffff; border: 0; }
        QPushButton#flat { background: transparent; border: 0; }
        QTableWidget { background: #ffffff; alternate-background-color: #f6f6f6; gridline-color: #dddddd; border: 1px solid #dddddd; }
        QHeaderView::section { background: #eeeeee; color: #222222; border: 1px solid #dddddd; padding: 8px; }
        QTabWidget::pane { border: 0; }
        QTabBar::tab { background: #f3f3f3; color: #222222; padding: 10px 18px; border: 1px solid #dddddd; }
        QTabBar::tab:selected { background: #ffffff; border-bottom: 3px solid #555555; }
        QScrollBar:vertical { background: #f4f4f4; width: 12px; }
        QScrollBar::handle:vertical { background: #bbbbbb; border-radius: 6px; min-height: 30px; }
        QCheckBox::indicator { width: 18px; height: 18px; }
        """

