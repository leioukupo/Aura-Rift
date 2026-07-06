from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TorchInfo:
    installed: bool
    torch: str = "未安装"
    cuda: str = "不可用"
    device: str = "未检测到"
    detail: str = ""


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


def resolve_python(comfy_path: Path, override: str = "") -> Path | str:
    if override:
        return override
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


def dependency_status(comfy_path: Path, python_override: str = "") -> dict[str, str]:
    python = resolve_python(comfy_path, python_override)
    return {
        "ComfyUI": "已选择" if (comfy_path / "main.py").exists() else "未安装或路径错误",
        "Python": str(python),
        "venv": "存在" if venv_python(comfy_path).exists() else "未创建",
        "pip": "存在" if venv_pip(comfy_path).exists() else "未创建",
        "git": shutil.which("git") or "未找到",
        "requirements.txt": "存在" if requirements_file(comfy_path).exists() else "未找到",
    }

