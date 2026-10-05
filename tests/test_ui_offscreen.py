from __future__ import annotations

import os
from pathlib import Path

# This must be set before the first QApplication is constructed.  The test is
# intentionally independent of pytest-qt so the normal project dependencies
# are sufficient on a headless Linux runner.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QScrollBar

from aura_rift.services.registry import ExtensionEntry
from aura_rift.services.environment import TorchInfo
from aura_rift.services import native_components
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


def test_version_tables_reflow_actions_after_large_to_compact_resize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Version actions must remain reachable after a DPI/window-size change.

    The launcher is commonly opened on a large monitor and then moved to a
    laptop or a fractional-DPI display.  QTableWidget remembers interactive
    column widths for hidden tabs, so merely checking the initial compact
    geometry misses the regression: a table can retain its 1900px geometry
    after the window has shrunk and place its action cell outside the viewport.
    Seed every version tab at the wide size, shrink while the install tab is
    active, then inspect every visible action widget after switching tabs.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
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
    monkeypatch.setattr(window_module.environment, "detect_gpu", lambda: ["测试 GPU"])

    app = _application()
    window = MainWindow()
    window.show()
    app.processEvents()

    # Keep all probes local and deterministic.  Expert mode intentionally
    # exposes the branch action as well, exercising the densest action cell.
    comfy = tmp_path / "ComfyUI"
    custom_nodes = comfy / "custom_nodes"
    (custom_nodes / "NodeAlpha").mkdir(parents=True)
    (custom_nodes / "NodeBeta").mkdir(parents=True)
    window.config.comfy_path = str(comfy)
    window.config.expert_mode = True

    page = window.version_page
    window.show_page("versions")
    page.tabs.setCurrentIndex(0)
    page._populate_catalog_versions(
        [
            {"commit": "abcdef0123456789", "description": "wide row", "date": "2026-01-01"},
            {"commit": "1234567890abcdef", "description": "another row", "date": "2026-01-02"},
        ]
    )

    page.tabs.setCurrentIndex(1)
    page.refresh_extensions()
    install_entry = ExtensionEntry(
        title="Responsive Node",
        reference="responsive-node",
        author="Aura-Rift",
        repository_url="https://example.invalid/responsive-node.git",
        description="offline test entry",
    )
    # Supplying a non-empty catalog keeps refresh_install_tab offline and
    # avoids its asynchronous remote-registry fallback.
    page.all_extensions = [install_entry]
    page.tabs.setCurrentIndex(2)
    page._populate_extension_list([install_entry])
    page._loaded_tabs = {0, 1, 2}

    # Establish the wide layout for every tab before shrinking.  This is the
    # sequence that exposed the stale hidden-table geometry in the old UI.
    window.resize(2107, 1317)
    for index in range(3):
        page.tabs.setCurrentIndex(index)
        app.processEvents()

    window.resize(960, 640)
    app.processEvents()

    def assert_actions_fit(table, action_column: int) -> None:
        table.doItemsLayout()
        app.processEvents()
        viewport = table.viewport()
        assert table.width() <= page.tabs.width()
        assert table.horizontalHeader().length() <= viewport.width()
        assert not table.horizontalScrollBar().isVisible()
        assert table.rowCount() > 0
        for row in range(table.rowCount()):
            index = table.model().index(row, action_column)
            cell = table.visualRect(index)
            host = table.cellWidget(row, action_column)
            assert host is not None
            host_rect = QRect(host.mapTo(viewport, QPoint(0, 0)), host.size())
            assert cell.contains(host_rect.topLeft())
            assert cell.contains(host_rect.bottomRight())
            for button in host.findChildren(window_module.QPushButton):
                if not button.isVisible():
                    continue
                button_rect = QRect(button.mapTo(viewport, QPoint(0, 0)), button.size())
                assert host_rect.contains(button_rect.topLeft())
                assert host_rect.contains(button_rect.bottomRight())
                assert cell.contains(button_rect.topLeft())
                assert cell.contains(button_rect.bottomRight())

    # Switching to a hidden tab after the resize must trigger its reflow too.
    for index, table, action_column in (
        (0, page.commit_table, 4),
        (1, page.extension_table, 5),
        (2, page.extension_install_list, 4),
    ):
        page.tabs.setCurrentIndex(index)
        app.processEvents()
        assert_actions_fit(table, action_column)

    window.close()
    app.processEvents()


def test_maintenance_native_actions_reflow_without_changing_launch_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Native component commands stay inside their cells after a resize.

    This also guards the important boundary of the responsive work: a
    geometry pass must not call a save/reset method or otherwise mutate the
    launch configuration.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        window_module.environment,
        "dependency_status",
        lambda *_args, **_kwargs: {"Python": "测试环境"},
    )
    monkeypatch.setattr(
        window_module.environment,
        "inspect_torch",
        lambda *_args, **_kwargs: TorchInfo(False, detail="测试环境"),
    )
    monkeypatch.setattr(window_module.environment, "detect_gpu", lambda: ["测试 GPU"])

    class FakeNativeComponentService:
        def detect(self, names):
            return [
                {
                    "name": name,
                    "label": name.upper(),
                    "installed": index % 2 == 0,
                    "version": "1.0" if index % 2 == 0 else "",
                    "reason": "未检测到" if index % 2 else "",
                }
                for index, name in enumerate(names)
            ]

    monkeypatch.setattr(native_components, "NativeComponentService", FakeNativeComponentService)

    app = _application()
    window = MainWindow()
    window.show()
    app.processEvents()
    window.show_page("maintenance")
    page = window.advanced_page
    page.adv_tabs.setCurrentIndex(1)
    app.processEvents()

    # Expand all maintenance cards so the native card follows the same
    # visible path as a user clicking through the page.
    for expandable in page.findChildren(window_module.ExpandCard):
        expandable.set_expanded(True)
    app.processEvents()
    before = window.config.to_dict()

    for size in ((2107, 1317), (960, 640)):
        window.resize(*size)
        app.processEvents()
        table = page.native_table
        table.doItemsLayout()
        app.processEvents()
        viewport = table.viewport()
        assert table.horizontalHeader().length() <= viewport.width()
        assert not table.horizontalScrollBar().isVisible()
        assert table.rowCount() == 4
        for row in range(table.rowCount()):
            index = table.model().index(row, 3)
            cell = table.visualRect(index)
            host = table.cellWidget(row, 3)
            assert host is not None
            host_rect = QRect(host.mapTo(viewport, QPoint(0, 0)), host.size())
            assert cell.contains(host_rect.topLeft())
            assert cell.contains(host_rect.bottomRight())
            for button in host.findChildren(window_module.QPushButton):
                button_rect = QRect(button.mapTo(viewport, QPoint(0, 0)), button.size())
                assert host_rect.contains(button_rect.topLeft())
                assert host_rect.contains(button_rect.bottomRight())
                assert cell.contains(button_rect.topLeft())
                assert cell.contains(button_rect.bottomRight())
        assert window.config.to_dict() == before

    window.close()
    app.processEvents()
