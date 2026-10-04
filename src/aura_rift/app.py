from __future__ import annotations

from PySide6.QtWidgets import QApplication

from aura_rift.ui.main_window import MainWindow


def run(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setApplicationName("Aura-Rift")
    window = MainWindow()
    # 绘世 opens as a maximized workbench on large desktop displays while
    # retaining a normal, resizable window on compact/CI screens.
    screen = app.primaryScreen()
    if window.config.window_maximized or (screen is not None and screen.availableGeometry().width() >= 1600):
        window.showMaximized()
    else:
        window.show()
    return app.exec()

