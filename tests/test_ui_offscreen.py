from __future__ import annotations

import os
from pathlib import Path

# This must be set before the first QApplication is constructed.  The test is
# intentionally independent of pytest-qt so the normal project dependencies
# are sufficient on a headless Linux runner.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollBar

from aura_rift.services.environment import TorchInfo
from aura_rift.ui import main_window as window_module
from aura_rift.ui.main_window import MainWindow
from aura_rift.ui.theme import DARK


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_main_window_routes_resize_theme_and_resources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # Tools/maintenance probes use subprocesses on a real desktop.  Their
    # service behavior has separate unit coverage; keep this UI regression
    # deterministic and focused on routing/layout/state preservation.
    monkeypatch.setattr(
        window_module.environment,
        "dependency_status",
        lambda *_args, **_kwargs: {"Python": "测试环境", "依赖": "未检查"},
    )
    monkeypatch.setattr(
        window_module.environment,
        "inspect_torch",
        lambda *_args, **_kwargs: TorchInfo(False, detail="测试环境"),
    )
    monkeypatch.setattr(
        window_module.environment,
        "detect_gpu",
        lambda: ["测试 GPU"],
    )

    app = _application()
    window = MainWindow()
    window.show()
    app.processEvents()

    routes = (
        "launch",
        "advanced",
        "troubleshoot",
        "versions",
        "tools",
        "console",
        "settings",
    )
    assert tuple(window.pages) == routes
    assert window.advanced_page.adv_tabs.count() == 3
    assert window.version_page.tabs.count() == 3
    assert window.settings_page.tabs.count() == 2
    assert window.advanced_page.full_widgets["directml"].isVisible() is False
    assert DARK.title_bar == "#202020"
    assert DARK.window == "#262626"
    assert DARK.surface == "#2e2e2e"
    assert DARK.console == "#0c0c0c"

    banner = window_module._resource_file("hanabi.jpg")
    catalog = window_module._resource_file("launcher_data.json")
    assert banner is not None and banner.is_file()
    assert catalog is not None and catalog.is_file()

    for size in ((2107, 1317), (1280, 800), (960, 640)):
        window.resize(*size)
        for route in routes:
            window.show_page(route)
            app.processEvents()
            page = window.pages[route]
            assert window.stack.currentWidget() is page
            assert window.nav_buttons[route].isChecked()
            # Pages may scroll vertically at compact sizes, but the target
            # layout must never hide controls behind a page-wide horizontal
            # scrollbar.
            assert not any(
                bar.orientation() == Qt.Horizontal and bar.isVisible()
                for bar in page.findChildren(QScrollBar)
            )

    # Legacy page names still route to the consolidated Linux hierarchy.
    window.show_page("patches")
    assert window.stack.currentWidget() is window.advanced_page
    assert window.advanced_page.adv_tabs.currentIndex() == 2
    window.show_page("maintenance")
    assert window.advanced_page.adv_tabs.currentIndex() == 1
    window.show_page("diagnostics")
    assert window.stack.currentWidget() is window.troubleshoot_page

    # A theme switch rebuilds the shell; retain both the visible page and its
    # selected inline tab rather than returning the user to the first page.
    window.show_page("advanced")
    window.advanced_page.adv_tabs.setCurrentIndex(2)
    previous_theme = window.config.theme
    window.toggle_theme()
    app.processEvents()
    assert window.config.theme != previous_theme
    assert window.stack.currentWidget() is window.advanced_page
    assert window.advanced_page.adv_tabs.currentIndex() == 2

    window.close()
    app.processEvents()


def test_console_compatibility_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        window_module.environment,
        "dependency_status",
        lambda *_args, **_kwargs: {},
    )
    app = _application()
    window = MainWindow()
    console = window.console_page
    console.append("\x1b[32mok\x1b[0m\n")
    assert "ok" in console.output.toPlainText()
    console.set_status("运行中")
    assert console.status.text() == "运行中"
    console.clear_output()
    assert console.output.toPlainText() == ""
    window.close()
    app.processEvents()
