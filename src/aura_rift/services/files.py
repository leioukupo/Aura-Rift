from __future__ import annotations

import os
import subprocess
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_path(path: Path) -> bool:
    path = path.expanduser()
    if not path.exists():
        return False
    if os.name == "posix":
        try:
            subprocess.Popen(["xdg-open", str(path)])
            return True
        except Exception:
            return False
    try:
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def directory_size_hint(path: Path) -> str:
    if not path.exists():
        return "不存在"
    try:
        count = sum(1 for _ in path.iterdir())
    except OSError:
        return "无法读取"
    return f"{count} 项"

