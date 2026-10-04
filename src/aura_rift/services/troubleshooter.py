"""Read-only ComfyUI environment diagnostics.

The scanner intentionally avoids importing user code or running shell snippets.
It checks paths, Python/Git executables, virtual-environment layout, common
dependency declarations, and basic filesystem health.  A tiny optional repair
API handles only safe, local directory creation and never edits source files.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from aura_rift.config import AppConfig


@dataclass
class DiagnosticIssue:
    name: str
    description: str
    severity: str = "warning"
    code: str = ""
    fixable: bool = False
    can_fix: bool = False
    path: str = ""
    details: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.code:
            self.code = self.name
        self.can_fix = self.fixable

    @property
    def title(self) -> str:
        return self.name

    @property
    def message(self) -> str:
        return self.description

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.name,
            "description": self.description,
            "message": self.description,
            "severity": self.severity,
            "code": self.code,
            "fixable": self.fixable,
            "can_fix": self.can_fix,
            "path": self.path,
            **self.details,
        }


Issue = DiagnosticIssue


@dataclass
class DiagnosticReport:
    issues: list[DiagnosticIssue] = field(default_factory=list)
    root: str = ""
    scanned_at: str = ""
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def findings(self) -> list[DiagnosticIssue]:
        return self.issues

    def __iter__(self):
        return iter(self.issues)

    def __len__(self) -> int:
        return len(self.issues)

    def __getitem__(self, index: int) -> DiagnosticIssue:
        return self.issues[index]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "scanned_at": self.scanned_at,
            "issues": [item.to_dict() for item in self.issues],
            "findings": [item.to_dict() for item in self.issues],
            "checks": dict(self.checks),
        }


ScanResult = DiagnosticReport


def _version(command: str, timeout: float = 3.0) -> str:
    try:
        proc = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (proc.stdout or proc.stderr or "").strip()
        return output.splitlines()[0][:240] if output else ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


class Troubleshooter:
    """Perform safe local checks against a ComfyUI directory."""

    def __init__(
        self,
        root: str | Path | None = None,
        config: AppConfig | None = None,
        *,
        python: str | Path | None = None,
        timeout: float = 4.0,
        platform_name: str | None = None,
    ) -> None:
        self.config = config
        configured = getattr(config, "comfy_path", "") if config else ""
        self.root = Path(root or configured or Path.cwd()).expanduser().resolve()
        self.python = Path(python).expanduser() if python else None
        self.timeout = timeout
        self.platform_name = (platform_name or platform.system()).lower()
        self._last_report: DiagnosticReport | None = None

    @property
    def is_linux(self) -> bool:
        return self.platform_name.startswith("linux")

    def _python_candidates(self) -> list[Path]:
        if self.python:
            return [self.python]
        configured = str(getattr(self.config, "python_path_override", "") or "") if self.config else ""
        candidates: list[Path] = []
        if configured:
            configured_path = Path(configured).expanduser()
            # Settings accepts either an interpreter file or a virtualenv
            # directory.  Normalize the latter before scanning; otherwise a
            # valid override is skipped by the ``is_file`` check below and a
            # random system Python is reported instead.
            if configured_path.is_dir():
                candidate = configured_path / ("Scripts/python.exe" if self.platform_name.startswith("win") else "bin/python")
                if candidate.exists():
                    configured_path = candidate
            candidates.append(configured_path)
        for relative in (
            ".venv/bin/python",
            ".venv/bin/python3",
            "venv/bin/python",
            "venv/bin/python3",
            ".venv/Scripts/python.exe",
            "venv/Scripts/python.exe",
        ):
            candidates.append(self.root / relative)
        commands = ("python", "python3") if self.platform_name.startswith("win") else ("python3", "python")
        for command in commands:
            resolved = shutil.which(command)
            if resolved:
                # WindowsApps aliases are placeholders that report as files
                # but cannot execute Python; leave them for the final warning
                # only when no real interpreter is available.
                if self.platform_name.startswith("win") and "windowsapps" in str(resolved).lower():
                    continue
                candidates.append(Path(resolved))
        # Preserve ordering but remove duplicates.
        seen: set[str] = set()
        return [item for item in candidates if not (str(item) in seen or seen.add(str(item)))]

    def _python_path(self) -> Path | None:
        for candidate in self._python_candidates():
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _issue(
        self,
        issues: list[DiagnosticIssue],
        name: str,
        description: str,
        *,
        severity: str = "warning",
        code: str = "",
        fixable: bool = False,
        path: Path | None = None,
        **details: Any,
    ) -> None:
        issues.append(DiagnosticIssue(
            name=name,
            description=description,
            severity=severity,
            code=code or name,
            fixable=fixable,
            path=str(path) if path else "",
            details=details,
        ))

    def scan(self, root: str | Path | None = None) -> DiagnosticReport:
        if root is not None:
            self.root = Path(root).expanduser().resolve()
        issues: list[DiagnosticIssue] = []
        checks: dict[str, Any] = {}
        root = self.root

        if not root.exists():
            self._issue(issues, "项目目录不存在", f"未找到 ComfyUI 目录：{root}", severity="error", code="missing_root", fixable=False, path=root)
            report = DiagnosticReport(issues, str(root), datetime.now(timezone.utc).isoformat(), checks)
            self._last_report = report
            return report
        if not root.is_dir():
            self._issue(issues, "项目路径不是目录", f"路径不是文件夹：{root}", severity="error", code="root_not_directory", path=root)
            report = DiagnosticReport(issues, str(root), datetime.now(timezone.utc).isoformat(), checks)
            self._last_report = report
            return report

        main_py = root / "main.py"
        checks["root"] = str(root)
        checks["main_py"] = main_py.exists()
        if not main_py.is_file():
            self._issue(issues, "缺少 main.py", "当前目录没有 ComfyUI 的 main.py，无法启动。", severity="error", code="missing_main", path=main_py)

        requirements = root / "requirements.txt"
        checks["requirements"] = requirements.exists()
        if not requirements.is_file():
            self._issue(issues, "缺少 requirements.txt", "未找到 ComfyUI 本体依赖清单。", severity="warning", code="missing_requirements", path=requirements)
        elif not os.access(requirements, os.R_OK):
            self._issue(issues, "依赖清单不可读", "requirements.txt 没有读取权限。", severity="error", code="requirements_unreadable", path=requirements)

        python_path = self._python_path()
        checks["python"] = str(python_path) if python_path else ""
        if python_path is None:
            self._issue(issues, "未找到 Python 环境", "没有找到可用于启动 ComfyUI 的 Python 解释器。", severity="error", code="missing_python", fixable=False)
        else:
            if not os.access(python_path, os.X_OK) and self.is_linux:
                self._issue(issues, "Python 不可执行", f"解释器没有执行权限：{python_path}", severity="error", code="python_not_executable", path=python_path)
            version = _version(str(python_path), self.timeout)
            checks["python_version"] = version
            if not version:
                self._issue(issues, "Python 无法运行", f"无法读取解释器版本：{python_path}", severity="error", code="python_failed", path=python_path)

        git = shutil.which("git")
        checks["git"] = git or ""
        if not git:
            self._issue(issues, "未找到 Git", "版本管理和插件更新需要 Git。", severity="warning", code="missing_git")
        elif (root / ".git").exists():
            checks["git_version"] = _version(git, self.timeout)
            try:
                proc = subprocess.run([git, "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, timeout=self.timeout, check=False)
                if proc.returncode != 0:
                    self._issue(issues, "Git 仓库异常", (proc.stderr or "无法读取 Git 状态").strip(), severity="warning", code="git_status_failed", path=root)
            except (OSError, subprocess.SubprocessError):
                self._issue(issues, "Git 状态检查失败", "读取 Git 仓库状态时发生错误。", severity="warning", code="git_status_failed", path=root)
        else:
            self._issue(issues, "不是 Git 仓库", "当前 ComfyUI 目录没有 .git，无法使用版本更新功能。", severity="info", code="not_git_repo", path=root)

        custom_nodes = root / "custom_nodes"
        checks["custom_nodes"] = custom_nodes.exists()
        if custom_nodes.exists() and custom_nodes.is_dir():
            normalized_names: dict[str, list[str]] = {}
            for child in sorted(custom_nodes.iterdir(), key=lambda item: item.name.lower()):
                if not child.is_dir() or child.is_symlink():
                    continue
                normalized = re.sub(r"[-_.\s]+", "", child.name).casefold()
                normalized_names.setdefault(normalized, []).append(child.name)
                req = child / "requirements.txt"
                if req.exists() and not os.access(req, os.R_OK):
                    self._issue(issues, "插件依赖清单不可读", f"扩展 {child.name} 的 requirements.txt 没有读取权限。", severity="warning", code="extension_requirements_unreadable", path=req)
            if bool(getattr(self.config, "duplicate_extension_check", True)):
                for names in normalized_names.values():
                    if len(names) > 1:
                        self._issue(
                            issues,
                            "疑似重复扩展",
                            "发现名称近似的扩展目录：" + "、".join(names),
                            severity="warning",
                            code="duplicate_extension",
                            path=custom_nodes,
                            extensions=names,
                        )
        elif not custom_nodes.exists():
            self._issue(issues, "缺少 custom_nodes 目录", "自定义节点目录尚未创建。", severity="info", code="missing_custom_nodes", fixable=True, path=custom_nodes)

        for directory in ("models", "input", "output", "user"):
            path = root / directory
            if path.exists() and not path.is_dir():
                self._issue(issues, f"{directory} 路径异常", f"{directory} 应为目录。", severity="warning", code="directory_not_directory", path=path)
            elif not path.exists():
                self._issue(issues, f"缺少 {directory} 目录", f"{directory} 目录尚未创建。", severity="info", code=f"missing_{directory}", fixable=True, path=path)

        if not os.access(root, os.R_OK):
            self._issue(issues, "项目目录不可读", "当前用户无法读取项目目录。", severity="error", code="root_unreadable", path=root)
        if not os.access(root, os.W_OK):
            self._issue(issues, "项目目录不可写", "当前用户无法写入项目目录；安装插件或更新可能失败。", severity="warning", code="root_unwritable", path=root)

        try:
            usage = shutil.disk_usage(root)
            checks["disk_free"] = usage.free
            if usage.free < 512 * 1024 * 1024:
                self._issue(issues, "磁盘空间不足", f"可用空间约 {usage.free / 1024**3:.2f} GiB，建议至少保留 512 MiB。", severity="warning", code="low_disk_space", path=root)
        except OSError:
            pass

        if python_path and requirements.is_file() and bool(getattr(self.config, "dependency_integrity_check", True)):
            try:
                from aura_rift.services.environment import check_dependencies

                dependencies = check_dependencies(root, python_path, timeout=max(1, int(self.timeout * 5)))
                checks["dependency_total"] = dependencies.total_count
                checks["dependency_missing"] = dependencies.total_missing
                if dependencies.installed_count < 0:
                    self._issue(issues, "依赖检查未完成", "Python 环境未能返回有效的依赖扫描结果。", severity="warning", code="dependency_check_failed")
                for req_file, missing in dependencies.missing_files.items():
                    names = [item.name or item.line for item in missing]
                    self._issue(
                        issues,
                        "Python 依赖未满足",
                        f"{req_file.parent.name or req_file.name} 缺少：" + "、".join(names[:8]) + (" 等" if len(names) > 8 else ""),
                        severity="warning",
                        code="missing_dependencies",
                        fixable=True,
                        path=req_file,
                        packages=names,
                    )
            except Exception as exc:
                self._issue(issues, "依赖检查失败", str(exc), severity="warning", code="dependency_check_failed")

        if python_path and bool(getattr(self.config, "component_conflict_check", True)):
            try:
                # Keep the probe self-contained and whitespace-stable.  It is
                # executed by the selected ComfyUI interpreter, so avoid
                # importing any launcher modules into that environment.
                probe_code = (
                    "import importlib.metadata as m\n"
                    "names=[]\n"
                    "for n in ('torch','xformers'):\n"
                    "    try:\n"
                    "        names.append(n+'='+m.version(n))\n"
                    "    except m.PackageNotFoundError:\n"
                    "        pass\n"
                    "print('\\n'.join(names))"
                )
                probe = subprocess.run(
                    [str(python_path), "-c", probe_code],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
                packages = {}
                for line in probe.stdout.splitlines():
                    if "=" in line:
                        name, value = line.split("=", 1)
                        packages[name.strip()] = value.strip()
                checks["torch"] = packages.get("torch", "")
                checks["xformers"] = packages.get("xformers", "")
                if "xformers" in packages and "torch" not in packages:
                    self._issue(
                        issues,
                        "xFormers 缺少 PyTorch",
                        "检测到 xFormers，但当前 Python 环境没有 PyTorch；二者无法正常协同。",
                        severity="warning",
                        code="component_conflict",
                        path=python_path,
                    )
            except (OSError, subprocess.SubprocessError, ValueError):
                pass

        report = DiagnosticReport(issues, str(root), datetime.now(timezone.utc).isoformat(), checks)
        self._last_report = report
        return report

    def fix(self, issue: DiagnosticIssue | Mapping[str, Any]) -> str:
        """Perform one of the few safe local repairs, returning a message."""
        code = issue.get("code", issue.get("name", "")) if isinstance(issue, Mapping) else getattr(issue, "code", getattr(issue, "name", ""))
        if code in {"missing_root", "root_not_directory"}:
            raise ValueError("项目路径无效，无法自动修复")
        if code == "missing_requirements":
            raise ValueError("不会自动生成依赖清单")
        if code in {"directory_not_directory"}:
            raise ValueError("不会覆盖已有文件")
        if code in {"missing_custom_nodes", "missing_models", "missing_input", "missing_output", "missing_user"}:
            name = str(code).removeprefix("missing_")
            path = (self.root / name).resolve()
            if not self._inside(self.root, path):
                raise ValueError("目录路径无效")
            path.mkdir(parents=True, exist_ok=True)
            return f"已创建目录：{path}"
        raise ValueError("该问题没有安全的自动修复动作")

    repair = fix

    @staticmethod
    def _inside(parent: Path, child: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False


def scan(root: str | Path | None = None, config: AppConfig | None = None, **kwargs: Any) -> DiagnosticReport:
    return Troubleshooter(root, config, **kwargs).scan()


__all__ = [
    "DiagnosticIssue",
    "DiagnosticReport",
    "Issue",
    "ScanResult",
    "Troubleshooter",
    "scan",
]
