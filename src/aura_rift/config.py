from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_ID = "aura-rift"
APP_NAME = "Aura-Rift"
COMFY_REPO_URL = "https://github.com/comfyanonymous/ComfyUI.git"
MANAGER_REPO_URL = "https://github.com/ltdrdata/ComfyUI-Manager.git"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def user_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_ID
    return Path.home() / ".config" / APP_ID


def default_comfy_dir() -> Path:
    return Path.home() / "Aura-Rift" / "ComfyUI"


@dataclass
class LaunchOptions:
    host: str = "127.0.0.1"
    port: int = 8188
    listen: bool = False
    vram_mode: str = "auto"
    attention: str = "auto"
    precision: str = "auto"
    preview_method: str = "auto"
    cpu_vae: bool = False
    disable_auto_launch: bool = False
    extra_args: str = ""

    def to_args(self) -> list[str]:
        args: list[str] = []
        if self.listen:
            args.extend(["--listen", self.host or "0.0.0.0"])
        if self.port:
            args.extend(["--port", str(self.port)])

        vram_flags = {
            "lowvram": "--lowvram",
            "normalvram": "--normalvram",
            "highvram": "--highvram",
            "novram": "--novram",
        }
        attention_flags = {
            "split": "--use-split-cross-attention",
            "quad": "--use-quad-cross-attention",
            "pytorch": "--use-pytorch-cross-attention",
        }
        precision_flags = {
            "fp16": "--force-fp16",
            "fp32": "--force-fp32",
        }

        if self.vram_mode in vram_flags:
            args.append(vram_flags[self.vram_mode])
        if self.attention in attention_flags:
            args.append(attention_flags[self.attention])
        if self.precision in precision_flags:
            args.append(precision_flags[self.precision])
        if self.cpu_vae:
            args.append("--cpu-vae")
        if self.preview_method and self.preview_method != "auto":
            args.extend(["--preview-method", self.preview_method])
        if self.disable_auto_launch:
            args.append("--disable-auto-launch")
        if self.extra_args.strip():
            args.extend(shlex.split(self.extra_args))
        return args


@dataclass
class NetworkOptions:
    http_proxy: str = ""
    https_proxy: str = ""
    pypi_mirror: bool = False
    github_proxy: str = ""

    def environment(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.http_proxy:
            env["HTTP_PROXY"] = self.http_proxy
            env["http_proxy"] = self.http_proxy
        if self.https_proxy:
            env["HTTPS_PROXY"] = self.https_proxy
            env["https_proxy"] = self.https_proxy
        return env


@dataclass
class AppConfig:
    comfy_path: str = field(default_factory=lambda: str(default_comfy_dir()))
    python_path_override: str = ""
    theme: str = "dark"
    language: str = "zh_CN"
    expert_mode: bool = False
    launch: LaunchOptions = field(default_factory=LaunchOptions)
    network: NetworkOptions = field(default_factory=NetworkOptions)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        cfg = cls()
        for key in ("comfy_path", "python_path_override", "theme", "language", "expert_mode"):
            if key in data:
                setattr(cfg, key, data[key])
        if isinstance(data.get("launch"), dict):
            cfg.launch = LaunchOptions(**{**asdict(cfg.launch), **data["launch"]})
        if isinstance(data.get("network"), dict):
            cfg.network = NetworkOptions(**{**asdict(cfg.network), **data["network"]})
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_config_dir() / "config.json"

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            return AppConfig.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def bundled_markdown(name: str) -> str:
    for path in (Path.cwd() / name, repo_root() / name, user_config_dir() / name):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""
