from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


@dataclass
class TorchInfo:
    installed: bool
    torch: str = "未安装"
    cuda: str = "不可用"
    device: str = "未检测到"
    detail: str = ""


class VenvManager(str, Enum):
    VENV = "venv"
    POETRY = "poetry"
    PDM = "pdm"
    UV = "uv"
    CONDA = "conda"


MANAGER_LOCK_FILES: dict[VenvManager, str] = {
    VenvManager.POETRY: "poetry.lock",
    VenvManager.PDM: "pdm.lock",
    VenvManager.UV: "uv.lock",
    VenvManager.CONDA: "environment.yml",
}

MANAGER_LABELS: dict[VenvManager, str] = {
    VenvManager.VENV: "venv (标准库)",
    VenvManager.POETRY: "Poetry",
    VenvManager.PDM: "PDM",
    VenvManager.UV: "uv",
    VenvManager.CONDA: "Conda",
}

MANAGER_BINARIES: dict[VenvManager, str] = {
    VenvManager.VENV: "python3",
    VenvManager.POETRY: "poetry",
    VenvManager.PDM: "pdm",
    VenvManager.UV: "uv",
    VenvManager.CONDA: "conda",
}


@dataclass
class VenvManagerInfo:
    manager: VenvManager
    binary: str = ""
    available: bool = False
    has_lock: bool = False
    detail: str = ""


def detect_venv_managers(comfy_path: Path) -> dict[VenvManager, VenvManagerInfo]:
    result: dict[VenvManager, VenvManagerInfo] = {}
    for manager in VenvManager:
        binary_name = MANAGER_BINARIES[manager]
        binary = shutil.which(binary_name) or ""
        lock_name = MANAGER_LOCK_FILES.get(manager)
        has_lock = bool(lock_name and (comfy_path / lock_name).exists())
        result[manager] = VenvManagerInfo(
            manager=manager,
            binary=binary,
            available=bool(binary),
            has_lock=has_lock,
        )
    return result


def autodetect_venv_manager(comfy_path: Path) -> VenvManager:
    """Lock file takes priority; otherwise prefer uv if installed, then stdlib venv."""
    managers = detect_venv_managers(comfy_path)
    for manager in (VenvManager.POETRY, VenvManager.PDM, VenvManager.UV, VenvManager.CONDA):
        info = managers.get(manager)
        if info and info.has_lock:
            return manager
    if managers.get(VenvManager.UV) and managers[VenvManager.UV].available:
        return VenvManager.UV
    return VenvManager.VENV


def conda_env_name(comfy_path: Path) -> str:
    env_file = comfy_path / "environment.yml"
    if not env_file.exists():
        return ""
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                return stripped.split(":", 1)[1].strip().strip("\"'\"'")
    except Exception:
        pass
    return ""


def conda_python_path(comfy_path: Path, env_name: str = "") -> str:
    """Resolve the actual python binary path inside a conda env."""
    conda = shutil.which("conda")
    if not conda:
        return ""
    if not env_name:
        env_name = conda_env_name(comfy_path)
    if not env_name:
        return ""
    try:
        proc = subprocess.run(
            [conda, "run", "-n", env_name, "which", "python"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        candidate = proc.stdout.strip().splitlines()
        if candidate and Path(candidate[-1]).exists():
            return candidate[-1]
    except Exception:
        pass
    return ""


def venv_dir(comfy_path: Path) -> Path:
    return comfy_path / ".venv"


def venv_python(comfy_path: Path) -> Path:
    if os.name == "nt":
        return venv_dir(comfy_path) / "Scripts" / "python.exe"
    return venv_dir(comfy_path) / "bin" / "python"


def venv_pip(comfy_path: Path) -> Path:
    if os.name == "nt":
        return venv_dir(comfy_path) / "Scripts" / "pip.exe"
    return venv_dir(comfy_path) / "bin" / "pip"


def resolve_python(
    comfy_path: Path,
    override: str = "",
    manager: "VenvManager | str" = VenvManager.VENV,
) -> Path | str:
    if override:
        return override
    if isinstance(manager, str):
        manager = VenvManager(manager)
    if manager == VenvManager.CONDA:
        conda_py = conda_python_path(comfy_path)
        if conda_py:
            return conda_py
        # fall through to .venv or system python
    candidate = venv_python(comfy_path)
    if candidate.exists():
        return candidate
    return sys.executable


def requirements_file(comfy_path: Path) -> Path:
    return comfy_path / "requirements.txt"


def detect_gpu() -> list[str]:
    devices: list[str] = []
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            out = subprocess.run(
                [nvidia, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            ).stdout.strip()
            devices.extend([line.strip() for line in out.splitlines() if line.strip()])
        except Exception:
            pass

    rocm = shutil.which("rocminfo")
    if rocm:
        devices.append("ROCm 可用")

    lspci = shutil.which("lspci")
    if lspci:
        try:
            out = subprocess.run(
                [lspci],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            ).stdout
            for line in out.splitlines():
                low = line.lower()
                if "vga" in low or "3d controller" in low:
                    if "nvidia" in low or "amd" in low:
                        devices.append(line.strip())
        except Exception:
            pass
    return devices or ["未检测到独立 GPU"]


def inspect_torch(python: Path | str) -> TorchInfo:
    code = (
        "import json\n"
        "try:\n"
        " import torch\n"
        " data={'installed': True, 'torch': torch.__version__, 'cuda': getattr(torch.version, 'cuda', None) or '不可用'}\n"
        " if torch.cuda.is_available():\n"
        "  data['device']=torch.cuda.get_device_name(0)\n"
        " else:\n"
        "  data['device']='未检测到 CUDA 设备'\n"
        " print(json.dumps(data, ensure_ascii=False))\n"
        "except Exception as exc:\n"
        " print(json.dumps({'installed': False, 'detail': str(exc)}, ensure_ascii=False))\n"
    )
    try:
        proc = subprocess.run(
            [str(python), "-c", code],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        data = json.loads(proc.stdout.strip() or "{}")
        return TorchInfo(
            installed=bool(data.get("installed")),
            torch=data.get("torch", "未安装"),
            cuda=data.get("cuda", "不可用"),
            device=data.get("device", "未检测到"),
            detail=data.get("detail", ""),
        )
    except Exception as exc:
        return TorchInfo(installed=False, detail=str(exc))


def venv_manager_status(comfy_path: Path, preferred: "VenvManager | str | None" = None) -> str:
    """Short human-readable string describing the active venv manager and its detection."""
    if preferred is None:
        preferred = autodetect_venv_manager(comfy_path)
    if isinstance(preferred, str):
        preferred = VenvManager(preferred)
    managers = detect_venv_managers(comfy_path)
    info = managers.get(preferred, VenvManagerInfo(manager=preferred))
    lock_name = MANAGER_LOCK_FILES.get(preferred, "")
    detail = f"{MANAGER_LABELS[preferred]}"
    if info.has_lock:
        detail += f"（检测到 {lock_name}）"
    elif info.available:
        detail += "（已安装，无锁文件）"
    else:
        detail += "（未安装）"
    return detail


def dependency_status(
    comfy_path: Path,
    python_override: str = "",
    venv_manager: "VenvManager | str | None" = None,
) -> dict[str, str]:
    mgr = autodetect_venv_manager(comfy_path) if venv_manager is None else VenvManager(venv_manager)
    python = resolve_python(comfy_path, python_override, mgr)
    return {
        "ComfyUI": "已选择" if (comfy_path / "main.py").exists() else "未安装或路径错误",
        "Python": str(python),
        "venv": "存在" if venv_python(comfy_path).exists() else "未创建",
        "pip": "存在" if venv_pip(comfy_path).exists() else "未创建",
        "git": shutil.which("git") or "未找到",
        "requirements.txt": "存在" if requirements_file(comfy_path).exists() else "未找到",
        "环境管理器": venv_manager_status(comfy_path, mgr),
    }



@dataclass
class RequirementRef:
    """A single requirement line that the check considers missing/unmet."""
    name: str
    line: str
    specifier: str = ""


@dataclass
class DependencyCheck:
    """Result of pre-launch dependency verification.

    `missing_files` maps each requirements file with at least one missing
    package to the list of addresses missing in that file. Files without any
    missing items are not present so callers can install just what's needed.
    """
    all_files: list[Path] = field(default_factory=list)
    missing_files: dict[Path, list[RequirementRef]] = field(default_factory=dict)
    installed_count: int = 0
    total_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.missing_files

    @property
    def total_missing(self) -> int:
        return sum(len(v) for v in self.missing_files.values())

    def summary(self) -> str:
        if self.ok:
            return f"依赖检查通过：共 {self.total_count} 个依赖，已全部安装。"
        names = "，".join(str(k.parent.name or k.name) for k in self.missing_files)
        return f"发现 {self.total_missing} 个未满足的依赖，分布在 {len(self.missing_files)} 个文件：{names}"


def iter_requirements_files(comfy_path: Path) -> list[Path]:
    """Collect ComfyUI's own requirements.txt and every custom_nodes one."""
    files: list[Path] = []
    main = comfy_path / "requirements.txt"
    if main.exists():
        files.append(main)
    custom_nodes = comfy_path / "custom_nodes"
    if custom_nodes.is_dir():
        for child in sorted(custom_nodes.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir():
                req = child / "requirements.txt"
                if req.exists():
                    files.append(req)
    return files


# Python script run inside the comfy venv to verify requirement satisfaction.
# Kept dependency-light: prefers `packaging` for strict specifier matching and
# falls back to name-only presence checking when it's not installed.
_CHECK_SCRIPT = r'''
import json, sys, re
try:
    from packaging.requirements import Requirement
    try:
        from packaging.specifiers import SpecifierSet
    except Exception:
        SpecifierSet = None
except Exception:
    Requirement = None
    SpecifierSet = None
import importlib.metadata as md

def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()

try:
    installed = {}
    versions = {}
    for dist in md.distributions():
        n = dist.metadata.get("Name", "")
        if n:
            installed[_norm(n)] = n
            try:
                versions[_norm(n)] = dist.version
            except Exception:
                pass
except Exception:
    installed = {}
    versions = {}

def parse_line(line):
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if s.startswith("-"):
        return None  # pip flag
    if "://" in s and "@" in s:
        return None  # vcs url
    if s.startswith("git+") or s.startswith("hg+") or s.startswith("svn+"):
        return None
    if Requirement is not None:
        try:
            req = Requirement(s)
            name = req.name
            spec = str(req.specifier) if req.specifier else ""
            # Cancel requirements whose environment marker excludes this platform.
            if req.marker is not None and not req.marker.evaluate():
                return None
            return _norm(name), name, spec
        except Exception:
            pass
    m = re.match(r"^([A-Za-z0-9_.-]+)(\[[^\]]+\])?(.*)$", s)
    if m:
        name = m.group(1)
        rest = m.group(3).strip()
        # Without packaging, drop anything with an environment marker (we
        # can't safely evaluate it) rather than risk a false positive.
        if ";" in rest:
            return None
        return _norm(name), name, rest.strip()
    return None

def satisfies(version, spec):
    if not spec or SpecifierSet is None or version is None:
        return True
    try:
        return SpecifierSet(spec).contains(version)
    except Exception:
        return True

results = []
for path in sys.argv[1:]:
    file_result = {"file": path, "missing": [], "ok": []}
    try:
        raw = open(path, encoding="utf-8", errors="replace").read()
    except Exception as e:
        file_result["error"] = str(e)
        results.append(file_result)
        continue
    for line in raw.splitlines():
        p = parse_line(line)
        if p is None:
            continue
        norm, name, spec = p
        inst_version = versions.get(norm)
        if norm in installed and satisfies(inst_version, spec):
            file_result["ok"].append({"name": name, "line": line, "specifier": spec})
        else:
            file_result["missing"].append({"name": name, "line": line, "specifier": spec, "installed_version": inst_version or ""})
    results.append(file_result)
print(json.dumps({"results": results}, ensure_ascii=False))
'''


def check_dependencies(
    comfy_path: Path,
    python: Path | str,
    timeout: int = 25,
) -> DependencyCheck:
    """Verify ComfyUI + custom_nodes requirements against the installed venv.

    Returns a structured result; never raises — failures degrade to "no check"
    so launching is never blocked by the precheck itself.
    """
    files = iter_requirements_files(comfy_path)
    check = DependencyCheck(all_files=files)
    if not files:
        return check
    try:
        proc = subprocess.run(
            [str(python), "-c", _CHECK_SCRIPT, *[str(f) for f in files]],
            cwd=str(comfy_path),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        data = json.loads(proc.stdout.strip() or "{}")
    except Exception as exc:  # noqa: BLE001 - never block launch on a check failure
        check.installed_count = -1
        check.missing_files = {}
        return check
    for entry in data.get("results", []):
        path = Path(entry["file"])
        present = 0
        missing: list[RequirementRef] = []
        for item in entry.get("missing", []):
            missing.append(RequirementRef(
                name=item.get("name", ""),
                line=item.get("line", ""),
                specifier=item.get("specifier", ""),
            ))
        present = len(entry.get("ok", []))
        check.total_count += missing.__len__() + present
        check.installed_count += present
        if missing:
            check.missing_files[path] = missing
    return check
