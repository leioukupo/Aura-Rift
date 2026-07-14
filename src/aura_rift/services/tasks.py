from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal


@dataclass
class CommandSpec:
    args: list[str]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    title: str = ""


class CommandWorker(QObject):
    output = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, commands: list[CommandSpec]) -> None:
        super().__init__()
        self.commands = commands
        self.stop_event = Event()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self.stop_event.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def run(self) -> None:
        try:
            for command in self.commands:
                if self.stop_event.is_set():
                    self.finished.emit(False, "任务已取消")
                    return
                if command.title:
                    self.output.emit(f"\n$ {command.title}\n")
                self.output.emit(f"$ {' '.join(command.args)}\n")
                env = os.environ.copy()
                env.update(command.env)
                # Don't let the launcher's own virtualenv leak into spawned
                # processes: uv/pip/conda all discover their target environment
                # via VIRTUAL_ENV, so an inherited value pointing to Aura-Rift's
                # .venv would cause packages to install into the wrong place.
                env.pop("VIRTUAL_ENV", None)
                env.pop("PYTHONHOME", None)
                self._process = subprocess.Popen(
                    command.args,
                    cwd=str(command.cwd) if command.cwd else None,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                )
                assert self._process.stdout is not None
                for line in self._process.stdout:
                    self.output.emit(line)
                    if self.stop_event.is_set():
                        self._process.terminate()
                        self.finished.emit(False, "任务已取消")
                        return
                code = self._process.wait()
                if code != 0:
                    self.finished.emit(False, f"命令退出码 {code}")
                    return
            self.finished.emit(True, "任务完成")
        except Exception as exc:
            self.finished.emit(False, str(exc))


class TaskHandle(QObject):
    output = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, commands: list[CommandSpec]) -> None:
        super().__init__()
        self.thread = QThread()
        self.worker = CommandWorker(commands)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.output.connect(self.output)
        self.worker.finished.connect(self._finish)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

    def start(self) -> None:
        self.thread.start()

    def cancel(self) -> None:
        self.worker.cancel()

    def _finish(self, ok: bool, message: str) -> None:
        self.thread.quit()
        self.thread.wait()
        self.finished.emit(ok, message)
