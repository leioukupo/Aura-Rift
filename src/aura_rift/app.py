from __future__ import annotations

from PySide6.QtWidgets import QApplication

from aura_rift.ui.main_window import MainWindow


def run(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("Aura-Rift")
    window = MainWindow()
    window.show()
    return app.exec()

