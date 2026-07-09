from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from aura_rift.config import AppConfig, COMFY_REPO_URL, MANAGER_REPO_URL
from aura_rift.services.environment import VenvManager, conda_env_name, resolve_python
from aura_rift.services.tasks import CommandSpec


class ComfyProcess(QObject):
    output = Signal(str)
    state_changed = Signal(str)
    finished = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.started.connect(lambda: self.state_changed.emit("运行中"))
        self.process.finished.connect(self._finished)

    def is_running(self) -> bool:
        return self.process.state() != QProcess.NotRunning

    def start(self, config: AppConfig) -> None:
        comfy_path = Path(config.comfy_path).expanduser()
        main_py = comfy_path / "main.py"
        if self.is_running():
            self.output.emit("ComfyUI 已在运行。\n")
            return
        if not main_py.exists():
            self.output.emit("未找到 main.py，请先选择或安装 ComfyUI。\n")
            self.state_changed.emit("路径错误")
            return
        python = str(resolve_python(comfy_path, config.python_path_override, config.venv_manager))
        args = [str(main_py), *config.launch.to_args()]
        env = QProcessEnvironment.systemEnvironment()
        for key, value in config.network.environment().items():
            env.insert(key, value)
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(comfy_path))
        self.output.emit(f"$ {python} {' '.join(args)}\n")
        self.process.start(python, args)

    def stop(self) -> None:
        if not self.is_running():
            self.output.emit("当前没有运行中的 ComfyUI 进程。\n")
            return
        self.process.terminate()
        if not self.process.waitForFinished(3000):
            self.output.emit("普通终止超时，正在强制结束进程。\n")
            self.process.kill()

    def _read(self) -> None:
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if data:
            self.output.emit(data)

    def _finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self.state_changed.emit("未运行")
        self.finished.emit(code)
        self.output.emit(f"\n进程已退出，退出码：{code}\n")


def _github_url(url: str, config: AppConfig) -> str:
    """Apply github mirror prefix if configured."""
    proxy = config.network.github_proxy.strip()
    if proxy and url.startswith("https://github.com/"):
        return proxy.rstrip("/") + "/" + url
    return url


def install_comfy_commands(
    target: Path,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> list[CommandSpec]:
    parent = target.parent
    cfg = config or AppConfig()
    env = env or command_environment(cfg)
    manager = cfg.venv_manager or "venv"
    repo_url = _github_url(COMFY_REPO_URL, cfg)
    commands = [
        CommandSpec(["git", "clone", repo_url, str(target)], cwd=parent, env=env, title="克隆 ComfyUI"),
    ]
    commands.extend(_create_env_commands(target, manager, env))
    return commands


def _create_env_commands(target: Path, manager: str, env: dict[str, str]) -> list[CommandSpec]:
    mgr = VenvManager(manager) if manager else VenvManager.VENV
    venv_bin = target / ".venv" / "bin"
    requirements = target / "requirements.txt"

    if mgr == VenvManager.POETRY:
        return [
            CommandSpec(["poetry", "install"], cwd=target, env=env, title="Poetry install"),
        ]
    if mgr == VenvManager.PDM:
        return [
            CommandSpec(["pdm", "install"], cwd=target, env=env, title="PDM install"),
        ]
    if mgr == VenvManager.UV:
        commands = [
            CommandSpec(["uv", "venv", str(target / ".venv")], cwd=target, env=env, title="uv venv"),
        ]
        uv_lock = target / "uv.lock"
        if uv_lock.exists():
            commands.append(CommandSpec(["uv", "sync"], cwd=target, env=env, title="uv sync"))
        elif requirements.exists():
            commands.append(
                CommandSpec(["uv", "pip", "install", "-r", "requirements.txt"], cwd=target, env=env, title="安装 ComfyUI 依赖")
            )
        return commands
    if mgr == VenvManager.CONDA:
        env_file = target / "environment.yml"
        env_name = conda_env_name(target)
        if env_file.exists():
            return [
                CommandSpec(["conda", "env", "create", "-f", "environment.yml"], cwd=target, env=env, title="conda env create"),
            ]
        name = env_name or target.name
        commands = [
            CommandSpec(["conda", "create", "-n", name, "python=3.11", "-y"], cwd=target, env=env, title="conda create"),
        ]
        if requirements.exists():
            commands.append(
                CommandSpec(["conda", "run", "-n", name, "pip", "install", "-r", "requirements.txt"], cwd=target, env=env, title="安装 ComfyUI 依赖")
            )
        return commands

    # stdlib venv (default)
    commands = [
        CommandSpec(["python3", "-m", "venv", str(target / ".venv")], env=env, title="创建项目虚拟环境"),
        CommandSpec([str(venv_bin / "python"), "-m", "pip", "install", "--upgrade", "pip"], cwd=target, env=env, title="升级 pip"),
    ]
    if requirements.exists():
        commands.append(
            CommandSpec([str(venv_bin / "pip"), "install", "-r", "requirements.txt"], cwd=target, env=env, title="安装 ComfyUI 依赖")
        )
    return commands


def install_manager_commands(
    comfy_path: Path,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> list[CommandSpec]:
    cfg = config or AppConfig()
    env = env or command_environment(cfg)
    custom_nodes = comfy_path / "custom_nodes"
    manager_path = custom_nodes / "ComfyUI-Manager"
    repo_url = _github_url(MANAGER_REPO_URL, cfg)
    return [
        CommandSpec(["git", "clone", repo_url, str(manager_path)], cwd=custom_nodes, env=env, title="安装 ComfyUI-Manager"),
    ]


def create_venv_commands(
    comfy_path: Path,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> list[CommandSpec]:
    cfg = config or AppConfig()
    env = env or command_environment(cfg)
    manager = cfg.venv_manager or "venv"
    return _create_env_commands(comfy_path, manager, env)


def reinstall_package_command(
    comfy_path: Path,
    package_name: str,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> CommandSpec:
    cfg = config or AppConfig()
    env = env or command_environment(cfg)
    manager = cfg.venv_manager or "venv"
    mgr = VenvManager(manager) if manager else VenvManager.VENV

    if mgr == VenvManager.CONDA:
        env_name = conda_env_name(comfy_path) or comfy_path.name
        return CommandSpec(
            ["conda", "run", "-n", env_name, "pip", "install", "--upgrade", "--force-reinstall", package_name],
            cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
        )
    if mgr == VenvManager.POETRY:
        return CommandSpec(
            ["poetry", "run", "pip", "install", "--upgrade", "--force-reinstall", package_name],
            cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
        )
    if mgr == VenvManager.PDM:
        return CommandSpec(
            ["pdm", "run", "pip", "install", "--upgrade", "--force-reinstall", package_name],
            cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
        )
    if mgr == VenvManager.UV:
        return CommandSpec(
            ["uv", "pip", "install", "--upgrade", "--force-reinstall", package_name],
            cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
        )

    # stdlib venv (default)
    pip = comfy_path / ".venv" / "bin" / "pip"
    if not pip.exists():
        pip = Path("pip")
    return CommandSpec(
        [str(pip), "install", "--upgrade", "--force-reinstall", package_name],
        cwd=comfy_path, env=env, title=f"重装 Python 组件：{package_name}",
    )


def install_plugin_command(
    comfy_path: Path,
    url: str,
    config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> CommandSpec:
    cfg = config or AppConfig()
    env = env or command_environment(cfg)
    custom_nodes = comfy_path / "custom_nodes"
    name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    clone_url = _github_url(url, cfg)
    return CommandSpec(
        ["git", "clone", clone_url, str(custom_nodes / name)],
        cwd=custom_nodes, env=env, title=f"安装扩展：{name}",
    )


def command_environment(config: AppConfig) -> dict[str, str]:
    env = config.network.environment()
    if config.network.pypi_mirror:
        env["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
        env["UV_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
    github_proxy = config.network.github_proxy.strip()
    if github_proxy:
        env["GITHUB_PROXY"] = github_proxy
    if os.environ.get("PATH"):
        env["PATH"] = os.environ["PATH"]
    return env
