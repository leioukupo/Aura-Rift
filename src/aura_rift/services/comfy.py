from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from aura_rift.config import AppConfig, COMFY_REPO_URL, MANAGER_REPO_URL
from aura_rift.services.environment import resolve_python
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
        python = str(resolve_python(comfy_path, config.python_path_override))
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


def install_comfy_commands(target: Path, env: dict[str, str] | None = None) -> list[CommandSpec]:
    parent = target.parent
    return [
        CommandSpec(["git", "clone", COMFY_REPO_URL, str(target)], cwd=parent, env=env or {}, title="克隆 ComfyUI"),
        CommandSpec(["python3", "-m", "venv", str(target / ".venv")], env=env or {}, title="创建项目虚拟环境"),
        CommandSpec(
            [str(target / ".venv" / "bin" / "python"), "-m", "pip", "install", "--upgrade", "pip"],
            cwd=target,
            env=env or {},
            title="升级 pip",
        ),
        CommandSpec(
            [str(target / ".venv" / "bin" / "pip"), "install", "-r", "requirements.txt"],
            cwd=target,
            env=env or {},
            title="安装 ComfyUI 依赖",
        ),
    ]


def install_manager_commands(comfy_path: Path, env: dict[str, str] | None = None) -> list[CommandSpec]:
    custom_nodes = comfy_path / "custom_nodes"
    manager_path = custom_nodes / "ComfyUI-Manager"
    return [
        CommandSpec(["git", "clone", MANAGER_REPO_URL, str(manager_path)], cwd=custom_nodes, env=env or {}, title="安装 ComfyUI-Manager"),
    ]


def create_venv_commands(comfy_path: Path, env: dict[str, str] | None = None) -> list[CommandSpec]:
    commands = [
        CommandSpec(["python3", "-m", "venv", str(comfy_path / ".venv")], cwd=comfy_path, env=env or {}, title="创建 .venv"),
    ]
    requirements = comfy_path / "requirements.txt"
    if requirements.exists():
        commands.append(
            CommandSpec(
                [str(comfy_path / ".venv" / "bin" / "pip"), "install", "-r", "requirements.txt"],
                cwd=comfy_path,
                env=env or {},
                title="安装 requirements.txt",
            )
        )
    return commands


def reinstall_package_command(comfy_path: Path, package_name: str, env: dict[str, str] | None = None) -> CommandSpec:
    pip = comfy_path / ".venv" / "bin" / "pip"
    if not pip.exists():
        pip = Path("pip")
    return CommandSpec(
        [str(pip), "install", "--upgrade", "--force-reinstall", package_name],
        cwd=comfy_path,
        env=env or {},
        title=f"重装 Python 组件：{package_name}",
    )


def install_plugin_command(comfy_path: Path, url: str, env: dict[str, str] | None = None) -> CommandSpec:
    custom_nodes = comfy_path / "custom_nodes"
    name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    return CommandSpec(
        ["git", "clone", url, str(custom_nodes / name)],
        cwd=custom_nodes,
        env=env or {},
        title=f"安装扩展：{name}",
    )


def command_environment(config: AppConfig) -> dict[str, str]:
    env = config.network.environment()
    if config.network.pypi_mirror:
        env["PIP_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
    if os.environ.get("PATH"):
        env["PATH"] = os.environ["PATH"]
    return env
