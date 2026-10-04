from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from aura_rift.config import AppConfig, ConfigStore, FullOptions, LaunchOptions
from aura_rift.services.catalog import LauncherCatalog, read_data
from aura_rift.services.comfy import (
    filter_cli_args,
    install_comfy_commands,
    platform_blocked_cli_flags,
    reinstall_package_command,
)
from aura_rift.services.environment import conda_env_name
from aura_rift.services.hotfixes import HotfixManager
from aura_rift.services.native_components import NativeComponentService, UnsupportedComponentError
from aura_rift.services.registry import ExtensionEntry, mark_installed


def test_catalog_accepts_signature_trailer_and_empty_fallback() -> None:
    assert read_data(b'{"ok": true}signature')["ok"] is True
    assert read_data(b"not-json", fallback={}) == {}
    assert isinstance(LauncherCatalog().data, dict)


def test_config_migrates_aliases_and_preserves_unknown(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    config = AppConfig.from_dict({"comfy": "/tmp/comfy", "future": {"x": 1}, "extra": {"legacy": True}})
    store.save(config)
    loaded = store.load()
    assert loaded.comfy_path == "/tmp/comfy"
    assert loaded.to_dict()["future"] == {"x": 1}
    assert loaded.to_dict()["legacy"] is True
    assert "extra" not in loaded.to_dict()


def test_native_commands_are_linux_only() -> None:
    service = NativeComponentService(platform_name="Linux", package_manager="apt", which=lambda _: "/bin/tool")
    assert service.install_command("git") == ["sudo", "apt-get", "install", "-y", "git"]
    try:
        NativeComponentService(platform_name="Windows", package_manager="apt").install_command("git")
    except UnsupportedComponentError:
        pass
    else:  # pragma: no cover - assertion documents the safety boundary
        raise AssertionError("Windows package commands must be rejected")


def test_hotfix_backup_and_revert(tmp_path: Path) -> None:
    target = tmp_path / "main.py"
    target.write_text("old", encoding="utf-8")
    payload = b"new"
    manager = HotfixManager(
        tmp_path,
        patches=[{
            "name": "safe",
            "linux": [{"path": "main.py", "content": payload.decode(), "sha256": hashlib.sha256(payload).hexdigest()}],
        }],
        platform_name="Linux",
    )
    assert manager.apply("safe").ok
    assert target.read_text(encoding="utf-8") == "new"
    assert manager.revert("safe").ok
    assert target.read_text(encoding="utf-8") == "old"


def test_comfy_cache_arguments_match_current_cli() -> None:
    assert "--cache-lru" in LaunchOptions(cache_strategy="lru").to_args()
    lru = LaunchOptions(cache_strategy="lru").to_args()
    assert lru[lru.index("--cache-lru") + 1].isdigit()
    assert FullOptions(cache_ram="4 8").to_args() == ["--cache-ram", "4"]
    # ComfyUI declares cache switches as one mutually-exclusive argparse
    # group; the process command suppresses the secondary RAM switch.
    assert FullOptions(cache_ram="4").to_args(cache_strategy="lru") == []


def test_linux_filters_windows_only_directml_flag(monkeypatch) -> None:
    import aura_rift.services.comfy as comfy_service

    monkeypatch.setattr(comfy_service.sys, "platform", "linux")
    args, dropped = filter_cli_args(
        ["--directml", "0", "--port", "8188"],
        {"--directml", "--port"},
        blocked_flags=platform_blocked_cli_flags(),
    )
    assert args == ["--port", "8188"]
    assert dropped == ["--directml"]


def test_fresh_install_commands_install_after_clone(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    config = AppConfig(python_path_override=sys.executable)
    commands = install_comfy_commands(target, config)
    assert commands[0].args[:2] == ["git", "clone"]
    assert any(command.args[-2:] == ["-r", "requirements.txt"] for command in commands)
    # The override is used to bootstrap the new project environment rather
    # than an ambient PATH python3 executable.
    assert commands[1].args[:3] == [sys.executable, "-m", "venv"]


def test_manager_install_commands_honor_python_override(tmp_path: Path) -> None:
    target = tmp_path / "ComfyUI"
    for manager, expected in (
        ("poetry", ["poetry", "env", "use", sys.executable]),
        ("pdm", ["pdm", "use", sys.executable]),
    ):
        config = AppConfig(venv_manager=manager, python_path_override=sys.executable)
        commands = install_comfy_commands(target, config)
        assert expected in [command.args for command in commands]

    config = AppConfig(venv_manager="uv", python_path_override=sys.executable)
    commands = install_comfy_commands(target, config)
    assert commands[1].args[:4] == ["uv", "venv", "--python", sys.executable]


def test_conda_environment_name_ignores_inline_comment(tmp_path: Path) -> None:
    (tmp_path / "environment.yml").write_text(
        "name: comfy-linux  # keep this environment isolated\n", encoding="utf-8"
    )
    assert conda_env_name(tmp_path) == "comfy-linux"


def test_reinstall_package_never_uses_bare_pip(tmp_path: Path) -> None:
    command = reinstall_package_command(tmp_path, "example", AppConfig())
    assert command.args[0] != "pip"
    assert command.args[0].endswith("python.exe") or command.args[0].endswith("python")


def test_disabled_extension_is_marked_installed(tmp_path: Path) -> None:
    custom_nodes = tmp_path / "custom_nodes"
    (custom_nodes / "Example.disabled").mkdir(parents=True)
    entries = [ExtensionEntry("Example", "", "", "https://github.com/a/Example.git", "")]
    mark_installed(entries, custom_nodes)
    assert entries[0].installed is True
