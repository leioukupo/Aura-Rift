from __future__ import annotations

import os
import subprocess
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# Process names that indicate a running desktop session.
_DESKTOP_PROCESS_NAMES = {
    "xfce4-session", "xfwm4", "xfdesktop",
    "gnome-shell", "gnome-session",
    "plasmashell", "kwin_x11", "kwin_wayland",
    "Thunar", "nautilus", "dolphin",
    "cinnamon-session", "mate-session", "lxsession",
}

# Environment variables to inherit from the desktop session process.
_DISPLAY_ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
                     "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION")


def _resolve_desktop_environment() -> bool:
    """If DISPLAY/WAYLAND_DISPLAY is unset, try to find it from a running desktop process."""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True

    try:
        proc_dir = Path("/proc")
        for entry in proc_dir.iterdir():
            if not entry.name.isdigit():
                continue
            comm_path = entry / "comm"
            try:
                comm = comm_path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if comm not in _DESKTOP_PROCESS_NAMES:
                continue
            environ_path = entry / "environ"
            try:
                raw = environ_path.read_bytes()
            except Exception:
                continue
            env: dict[str, str] = {}
            for line in raw.split(b"\0"):
                if b"=" in line:
                    key, _, value = line.decode(errors="replace").partition("=")
                    env[key] = value
            display = env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")
            if not display:
                continue
            # Inherit display-related env vars into our process environment
            for key in _DISPLAY_ENV_KEYS:
                if key in env and key not in os.environ:
                    os.environ[key] = env[key]
            return True
    except Exception:
        pass
    return False


def open_path(path: Path) -> bool:
    path = path.expanduser()
    if not path.exists():
        return False
    if os.name == "posix":
        if not _resolve_desktop_environment():
            return False
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
