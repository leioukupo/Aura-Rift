"""Linux native-component discovery and safe package-command generation.

The Windows launcher catalog contains entries such as Visual Studio Build
Tools.  Those installers are meaningless on Linux and must never be surfaced
or executed by Aura-Rift.  This service only *describes* package-manager
commands; callers decide whether and when to run them.
"""

from __future__ import annotations

import platform as _platform
import shutil
import subprocess
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence


class UnsupportedComponentError(ValueError):
    """Raised when a component is not safe/meaningful on the current OS."""


@dataclass
class NativeComponentStatus:
    name: str
    label: str = ""
    command: str = ""
    installed: bool = False
    path: str = ""
    version: str = ""
    package: str = ""
    required: bool = False
    supported: bool = True
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def available(self) -> bool:
        return self.supported and self.installed

    @property
    def present(self) -> bool:
        return self.installed

    def to_dict(self) -> dict[str, Any]:
        value = {
            "name": self.name,
            "label": self.label or self.name,
            "command": self.command,
            "installed": self.installed,
            "present": self.installed,
            "available": self.available,
            "path": self.path,
            "version": self.version,
            "package": self.package,
            "required": self.required,
            "supported": self.supported,
            "reason": self.reason,
        }
        value.update(self.metadata)
        return value

    # Mapping-like access keeps the result convenient for lightweight UI code
    # and older integrations that predate the dataclass API.
    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


ComponentStatus = NativeComponentStatus


class ComponentResults(list[NativeComponentStatus]):
    """List with small mapping conveniences for legacy callers."""

    def by_name(self, name: str) -> NativeComponentStatus | None:
        needle = _normal_name(name)
        return next((item for item in self if _normal_name(item.name) == needle), None)

    def __getitem__(self, key: int | slice | str):  # type: ignore[override]
        if isinstance(key, str):
            item = self.by_name(key)
            if item is None:
                raise KeyError(key)
            return item
        return super().__getitem__(key)

    def get(self, name: str, default: Any = None) -> NativeComponentStatus | Any:
        return self.by_name(name) or default

    def keys(self):
        return [item.name for item in self]

    def values(self):
        return list(self)

    def items(self):
        return [(item.name, item) for item in self]


# Deliberately conservative allow-list.  Arbitrary strings must not become
# shell/package-manager arguments merely because they came from a JSON file.
_DEFAULT_COMPONENTS: dict[str, dict[str, Any]] = {
    "git": {"label": "Git", "command": "git", "package": "git", "required": True},
    "cmake": {"label": "CMake", "command": "cmake", "package": "cmake"},
    "ninja": {"label": "Ninja", "command": "ninja", "package": "ninja-build"},
    "gcc": {"label": "GCC", "command": "gcc", "package": "gcc"},
    "g++": {"label": "G++", "command": "g++", "package": "g++"},
    "make": {"label": "GNU Make", "command": "make", "package": "make"},
    "pkg-config": {"label": "pkg-config", "command": "pkg-config", "package": "pkg-config"},
    "ffmpeg": {"label": "FFmpeg", "command": "ffmpeg", "package": "ffmpeg"},
    "python": {"label": "Python", "command": "python3", "package": "python3", "required": True},
    "python3-dev": {"label": "Python headers", "command": "python3-config", "package": "python3-dev"},
}

_FORBIDDEN_NAMES = {
    "visual studio",
    "visual-studio",
    "visual_studio",
    "vs build tools",
    "vs-build-tools",
    "vs_build_tools",
    "msvc",
    "windows sdk",
    "windows-sdk",
}

_PACKAGE_MANAGERS: dict[str, tuple[str, str, str]] = {
    "apt": ("apt-get", "install", "remove"),
    "apt-get": ("apt-get", "install", "remove"),
    "dnf": ("dnf", "install", "remove"),
    "yum": ("yum", "install", "remove"),
    "pacman": ("pacman", "-S", "-R"),
    "zypper": ("zypper", "install", "remove"),
    "apk": ("apk", "add", "del"),
}


def _normal_name(value: object) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    return " ".join(text.split())


def _is_forbidden(name: str) -> bool:
    normalized = _normal_name(name)
    compact = normalized.replace(" ", "").replace("-", "").replace("_", "")
    return (
        normalized in {_normal_name(item) for item in _FORBIDDEN_NAMES}
        or "visual studio" in normalized
        or "visualstudio" in compact
        or compact.startswith("vsbuildtools")
        or compact.startswith("msvc")
    )


def _version_for(command: str, runner: Callable[..., Any] | None = None) -> str:
    if not command:
        return ""
    try:
        fn = runner or subprocess.run
        result = fn(
            [command, "--version"],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
        output = (getattr(result, "stdout", "") or getattr(result, "stderr", "") or "").strip()
        return output.splitlines()[0][:240] if output else ""
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return ""


class NativeComponentService:
    """Inspect Linux tools and construct package-manager commands.

    ``catalog`` may be a :class:`LauncherCatalog`, a raw dictionary, or
    ``None``.  A custom ``which``/``runner`` is accepted for deterministic
    tests and for callers embedding the service in a sandbox.
    """

    def __init__(
        self,
        catalog: Any = None,
        *,
        package_manager: str | None = None,
        platform_name: str | None = None,
        which: Callable[[str], str | None] | None = None,
        runner: Callable[..., Any] | None = None,
        use_sudo: bool = True,
    ) -> None:
        self.catalog = catalog
        self.package_manager = package_manager
        self.platform_name = (platform_name or _platform.system()).lower()
        self.which = which or shutil.which
        self.runner = runner
        self.use_sudo = use_sudo

    @property
    def is_linux(self) -> bool:
        return self.platform_name.startswith("linux")

    def _catalog_mapping(self) -> Mapping[str, Any]:
        raw = self.catalog
        if raw is None:
            return {}
        if isinstance(raw, Mapping):
            return raw
        data = getattr(raw, "data", None)
        if isinstance(data, Mapping):
            return data
        return {}

    def _specs(self) -> dict[str, dict[str, Any]]:
        specs = {key: dict(value) for key, value in _DEFAULT_COMPONENTS.items()}
        catalog = self._catalog_mapping().get("native_component_requirements", {})
        if not isinstance(catalog, Mapping):
            return specs
        # The source schema has platform_independent/platform_dependent maps.
        # Only merge entries that have a Linux package hint; Windows download
        # URLs are intentionally ignored.
        independent = catalog.get("platform_independent", {})
        if isinstance(independent, Mapping):
            for raw_name, raw_value in independent.items():
                name = _normal_name(raw_name)
                if _is_forbidden(name) or not name:
                    continue
                info = dict(raw_value) if isinstance(raw_value, Mapping) else {}
                current = specs.setdefault(name, {"label": str(raw_name), "command": name, "package": name})
                current.update(self._linux_metadata(info))
        return specs

    @staticmethod
    def _linux_metadata(info: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ("linux_package", "package", "apt", "apt_package", "command", "label", "required"):
            if key in info and isinstance(info[key], (str, bool)):
                result[key] = info[key]
        # A generic ``packages`` map is common in newer catalog snapshots.
        packages = info.get("packages")
        if isinstance(packages, Mapping):
            for key in ("linux", "debian", "ubuntu", "apt"):
                if isinstance(packages.get(key), str):
                    result["package"] = packages[key]
                    break
        return result

    def _resolve_spec(self, component: str | Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        if isinstance(component, Mapping):
            raw_name = component.get("name") or component.get("id") or component.get("command")
        else:
            raw_name = component
        name = _normal_name(raw_name)
        if not name or _is_forbidden(name):
            raise UnsupportedComponentError(f"Linux 不支持原生组件：{raw_name}")
        specs = self._specs()
        if name not in specs:
            # Permit only executable-like names for catalog extensions.  This
            # still rejects shell metacharacters and option injection.
            if not name.replace("-", "").replace(".", "").isalnum():
                raise ValueError(f"非法组件名：{raw_name}")
            specs[name] = {"label": name, "command": name, "package": name}
        spec = dict(specs[name])
        if isinstance(component, Mapping):
            spec.update({key: value for key, value in component.items() if key in {"command", "package", "linux_package", "label", "required"}})
        spec["command"] = str(spec.get("command") or name)
        spec["package"] = str(spec.get("linux_package") or spec.get("package") or name)
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+:_=-]*", spec["package"])
            or spec["package"].startswith("-")
        ):
            raise ValueError(f"非法软件包名：{spec['package']}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", spec["command"]):
            raise ValueError(f"非法可执行文件名：{spec['command']}")
        return name, spec

    def detect(self, components: Iterable[str] | str | None = None) -> ComponentResults:
        names = list(components.split(",") if isinstance(components, str) else components or self._specs().keys())
        result: ComponentResults = ComponentResults()
        for requested in names:
            try:
                name, spec = self._resolve_spec(requested)
            except (ValueError, UnsupportedComponentError) as exc:
                result.append(NativeComponentStatus(name=str(requested), supported=False, reason=str(exc)))
                continue
            command = str(spec.get("command") or name)
            if not self.is_linux:
                result.append(NativeComponentStatus(
                    name=name,
                    label=str(spec.get("label") or name),
                    command=command,
                    package=str(spec.get("package") or name),
                    required=bool(spec.get("required", False)),
                    supported=False,
                    reason="原生组件管理仅支持 Linux",
                ))
                continue
            path = self.which(command) or ""
            installed = bool(path)
            result.append(NativeComponentStatus(
                name=name,
                label=str(spec.get("label") or name),
                command=command,
                installed=installed,
                path=path,
                version=_version_for(command, self.runner) if installed else "",
                package=str(spec.get("package") or name),
                required=bool(spec.get("required", False)),
                supported=True,
                reason="" if installed else "未找到可执行文件",
            ))
        return result

    def detect_map(self, components: Iterable[str] | str | None = None) -> dict[str, NativeComponentStatus]:
        return {item.name: item for item in self.detect(components)}

    detect_components = detect
    inspect = detect

    def _manager(self, package_manager: str | None = None) -> tuple[str, str, str]:
        requested = _normal_name(package_manager or self.package_manager or "")
        if requested:
            if requested not in _PACKAGE_MANAGERS:
                raise ValueError(f"不支持的软件包管理器：{package_manager}")
            return _PACKAGE_MANAGERS[requested]
        # Debian derivatives commonly expose ``apt`` while minimal images
        # only ship ``apt-get``.  Both names use the same command semantics;
        # probe the user-facing binary first so generated diagnostics match
        # the host's available tool.
        for candidate in ("apt", "apt-get", "dnf", "pacman", "zypper", "apk", "yum"):
            if self.which(candidate):
                return _PACKAGE_MANAGERS.get(candidate, _PACKAGE_MANAGERS["apt-get"])
        raise RuntimeError("未检测到受支持的 Linux 软件包管理器")

    def install_command(
        self,
        component: str | Mapping[str, Any] | Sequence[str],
        *,
        package_manager: str | None = None,
        use_sudo: bool | None = None,
    ) -> list[str] | list[list[str]]:
        if isinstance(component, Sequence) and not isinstance(component, (str, bytes, bytearray, Mapping)):
            return [self.install_command(item, package_manager=package_manager, use_sudo=use_sudo) for item in component]  # type: ignore[list-item]
        if not self.is_linux:
            raise UnsupportedComponentError("原生组件安装命令仅支持 Linux")
        _name, spec = self._resolve_spec(component)  # type: ignore[arg-type]
        executable, install_word, _remove_word = self._manager(package_manager)
        prefix = ["sudo"] if (self.use_sudo if use_sudo is None else use_sudo) else []
        if executable == "pacman":
            return [*prefix, executable, install_word, "--needed", spec["package"]]
        return [*prefix, executable, install_word, "-y", spec["package"]]

    def uninstall_command(
        self,
        component: str | Mapping[str, Any] | Sequence[str],
        *,
        package_manager: str | None = None,
        use_sudo: bool | None = None,
    ) -> list[str] | list[list[str]]:
        if isinstance(component, Sequence) and not isinstance(component, (str, bytes, bytearray, Mapping)):
            return [self.uninstall_command(item, package_manager=package_manager, use_sudo=use_sudo) for item in component]  # type: ignore[list-item]
        if not self.is_linux:
            raise UnsupportedComponentError("原生组件卸载命令仅支持 Linux")
        _name, spec = self._resolve_spec(component)  # type: ignore[arg-type]
        executable, _install_word, remove_word = self._manager(package_manager)
        prefix = ["sudo"] if (self.use_sudo if use_sudo is None else use_sudo) else []
        if executable == "pacman":
            return [*prefix, executable, remove_word, spec["package"]]
        return [*prefix, executable, remove_word, "-y", spec["package"]]

    # Plural aliases are useful to UI code and keep command creation explicit.
    install_commands = install_command
    uninstall_commands = uninstall_command
    generate_install_command = install_command
    generate_uninstall_command = uninstall_command


def detect(components: Iterable[str] | str | None = None, **kwargs: Any) -> ComponentResults:
    return NativeComponentService(**kwargs).detect(components)


def install_command(component: str | Mapping[str, Any], **kwargs: Any) -> list[str] | list[list[str]]:
    return NativeComponentService(**kwargs).install_command(component)


def uninstall_command(component: str | Mapping[str, Any], **kwargs: Any) -> list[str] | list[list[str]]:
    return NativeComponentService(**kwargs).uninstall_command(component)


def detect_native_components(components: Iterable[str] | str | None = None, **kwargs: Any) -> list[NativeComponentStatus]:
    return detect(components, **kwargs)


def generate_install_command(component: str | Mapping[str, Any], **kwargs: Any) -> list[str] | list[list[str]]:
    return install_command(component, **kwargs)


def generate_uninstall_command(component: str | Mapping[str, Any], **kwargs: Any) -> list[str] | list[list[str]]:
    return uninstall_command(component, **kwargs)


__all__ = [
    "ComponentResults",
    "ComponentStatus",
    "NativeComponentService",
    "NativeComponentStatus",
    "UnsupportedComponentError",
    "detect",
    "detect_native_components",
    "generate_install_command",
    "generate_uninstall_command",
    "install_command",
    "uninstall_command",
]
