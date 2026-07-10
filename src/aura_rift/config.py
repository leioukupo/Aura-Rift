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
    cache_strategy: str = "auto"
    disable_smart_memory: bool = False
    vae_precision: str = "auto"
    text_enc_precision: str = "auto"
    cuda_malloc: bool = False
    enable_cors: str = ""
    output_directory: str = ""
    input_directory: str = ""
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

        cache_flags = {
            "classic": "--cache-classic",
            "lru": "--cache-lru",
            "none": "--cache-none",
        }
        if self.cache_strategy in cache_flags:
            args.append(cache_flags[self.cache_strategy])
        if self.disable_smart_memory:
            args.append("--disable-smart-memory")

        vae_flags = {
            "bf16": "--bf16-vae",
            "fp16": "--fp16-vae",
            "fp32": "--fp32-vae",
        }
        if self.vae_precision in vae_flags:
            args.append(vae_flags[self.vae_precision])

        text_enc_flags = {
            "e4m3fn": "--fp8_e4m3fn-text-enc",
            "e5m2": "--fp8_e5m2-text-enc",
        }
        if self.text_enc_precision in text_enc_flags:
            args.append(text_enc_flags[self.text_enc_precision])

        if self.cuda_malloc:
            args.append("--cuda-malloc")
        if self.enable_cors.strip():
            args.extend(["--enable-cors-header", self.enable_cors.strip()])
        if self.output_directory.strip():
            args.extend(["--output-directory", self.output_directory.strip()])
        if self.input_directory.strip():
            args.extend(["--input-directory", self.input_directory.strip()])

        if self.extra_args.strip():
            args.extend(shlex.split(self.extra_args))
        return args


@dataclass
class FullOptions:
    """All remaining ComfyUI CLI parameters not covered by LaunchOptions.

    Empty/zero values mean 'unset' so no flag is emitted. Booleans map to a
    single store_true flag. Strings/ints are emitted only when non-empty.
    """
    # --- TNE / device ---
    cuda_device: str = ""
    default_device: int = -1
    directml: int = -2  # -2 off, -1 auto (no arg), >=0 device id
    oneapi_device_selector: str = ""
    supports_fp8_compute: bool = False
    enable_triton_backend: bool = False
    force_channels_last: bool = False
    fp16_intermediates: bool = False
    fp64_unet: bool = False
    fp8_e8m0fnu_unet: bool = False
    force_non_blocking: bool = False
    # --- performance / vram / cache ---
    cache_ram: str = ""  # space-separated floats
    high_ram: bool = False
    reserve_vram: float = 0.0
    vram_headroom: float = 0.0
    async_offload: str = "auto"  # auto / on / <N>
    disable_async_offload: bool = False
    disable_dynamic_vram: bool = False
    enable_dynamic_vram: bool = False
    fast_disk: bool = False
    disable_pinned_memory: bool = False
    deterministic: bool = False
    # --- attention ---
    use_sage_attention: bool = False
    use_flash_attention: bool = False
    disable_xformers: bool = False
    force_upcast_attention: bool = False
    dont_upcast_attention: bool = False
    # --- text encoder precision (extra variants) ---
    fp16_text_enc: bool = False
    fp32_text_enc: bool = False
    bf16_text_enc: bool = False
    # --- preview ---
    preview_size: int = 512
    # --- network / server ---
    tls_keyfile: str = ""
    tls_certfile: str = ""
    max_upload_size: float = 100.0
    enable_compress_response_body: bool = False
    comfy_api_base: str = ""
    database_url: str = ""
    enable_assets: bool = False
    enable_asset_hashing: bool = False
    feature_flags: str = ""  # comma-separated KEY[=VALUE]
    # --- directories ---
    base_directory: str = ""
    temp_directory: str = ""
    user_directory: str = ""
    front_end_version: str = ""
    front_end_root: str = ""
    extra_model_paths_config: str = ""  # space-separated paths
    # --- misc ---
    default_hashing_function: str = ""  # md5/sha1/sha256/sha512
    mmap_torch_files: bool = False
    disable_mmap: bool = False
    dont_print_server: bool = False
    disable_metadata: bool = False
    disable_all_custom_nodes: bool = False
    whitelist_custom_nodes: str = ""
    disable_api_nodes: bool = False
    multi_user: bool = False
    verbose: str = ""  # DEBUG/INFO/WARNING/ERROR/CRITICAL
    log_stdout: bool = False
    enable_manager: bool = False
    disable_manager_ui: bool = False
    enable_manager_legacy_ui: bool = False

    def to_args(self) -> list[str]:
        args: list[str] = []
        if self.cuda_device.strip():
            args += ["--cuda-device", self.cuda_device.strip()]
        if self.default_device >= 0:
            args += ["--default-device", str(self.default_device)]
        if self.directml == -1:
            args.append("--directml")
        elif self.directml >= 0:
            args += ["--directml", str(self.directml)]
        if self.oneapi_device_selector.strip():
            args += ["--oneapi-device-selector", self.oneapi_device_selector.strip()]
        if self.supports_fp8_compute:
            args.append("--supports-fp8-compute")
        if self.enable_triton_backend:
            args.append("--enable-triton-backend")
        if self.force_channels_last:
            args.append("--force-channels-last")
        if self.fp16_intermediates:
            args.append("--fp16-intermediates")
        if self.fp64_unet:
            args.append("--fp64-unet")
        if self.fp8_e8m0fnu_unet:
            args.append("--fp8_e8m0fnu-unet")
        if self.force_non_blocking:
            args.append("--force-non-blocking")
        if self.cache_ram.strip():
            args += ["--cache-ram", *self.cache_ram.split()]
        if self.high_ram:
            args.append("--high-ram")
        if self.reserve_vram > 0:
            args += ["--reserve-vram", str(self.reserve_vram)]
        if self.vram_headroom > 0:
            args += ["--vram-headroom", str(self.vram_headroom)]
        if self.async_offload == "on":
            args.append("--async-offload")
        elif self.async_offload not in ("auto", "", "on"):
            args += ["--async-offload", str(self.async_offload)]
        if self.disable_async_offload:
            args.append("--disable-async-offload")
        if self.disable_dynamic_vram:
            args.append("--disable-dynamic-vram")
        if self.enable_dynamic_vram:
            args.append("--enable-dynamic-vram")
        if self.fast_disk:
            args.append("--fast-disk")
        if self.disable_pinned_memory:
            args.append("--disable-pinned-memory")
        if self.deterministic:
            args.append("--deterministic")
        if self.use_sage_attention:
            args.append("--use-sage-attention")
        if self.use_flash_attention:
            args.append("--use-flash-attention")
        if self.disable_xformers:
            args.append("--disable-xformers")
        if self.force_upcast_attention:
            args.append("--force-upcast-attention")
        if self.dont_upcast_attention:
            args.append("--dont-upcast-attention")
        if self.fp16_text_enc:
            args.append("--fp16-text-enc")
        if self.fp32_text_enc:
            args.append("--fp32-text-enc")
        if self.bf16_text_enc:
            args.append("--bf16-text-enc")
        if self.preview_size and self.preview_size != 512:
            args += ["--preview-size", str(self.preview_size)]
        if self.tls_keyfile.strip():
            args += ["--tls-keyfile", self.tls_keyfile.strip()]
        if self.tls_certfile.strip():
            args += ["--tls-certfile", self.tls_certfile.strip()]
        if self.max_upload_size and self.max_upload_size != 100.0:
            args += ["--max-upload-size", str(self.max_upload_size)]
        if self.enable_compress_response_body:
            args.append("--enable-compress-response-body")
        if self.comfy_api_base.strip():
            args += ["--comfy-api-base", self.comfy_api_base.strip()]
        if self.database_url.strip():
            args += ["--database-url", self.database_url.strip()]
        if self.enable_assets:
            args.append("--enable-assets")
        if self.enable_asset_hashing:
            args.append("--enable-asset-hashing")
        if self.feature_flags.strip():
            for flag in self.feature_flags.split(","):
                flag = flag.strip()
                if flag:
                    args += ["--feature-flag", flag]
        if self.base_directory.strip():
            args += ["--base-directory", self.base_directory.strip()]
        if self.temp_directory.strip():
            args += ["--temp-directory", self.temp_directory.strip()]
        if self.user_directory.strip():
            args += ["--user-directory", self.user_directory.strip()]
        if self.front_end_version.strip():
            args += ["--front-end-version", self.front_end_version.strip()]
        if self.front_end_root.strip():
            args += ["--front-end-root", self.front_end_root.strip()]
        if self.extra_model_paths_config.strip():
            args += ["--extra-model-paths-config", *self.extra_model_paths_config.split()]
        if self.default_hashing_function.strip():
            args += ["--default-hashing-function", self.default_hashing_function.strip()]
        if self.mmap_torch_files:
            args.append("--mmap-torch-files")
        if self.disable_mmap:
            args.append("--disable-mmap")
        if self.dont_print_server:
            args.append("--dont-print-server")
        if self.disable_metadata:
            args.append("--disable-metadata")
        if self.disable_all_custom_nodes:
            args.append("--disable-all-custom-nodes")
        if self.whitelist_custom_nodes.strip():
            args += ["--whitelist-custom-nodes", *self.whitelist_custom_nodes.split()]
        if self.disable_api_nodes:
            args.append("--disable-api-nodes")
        if self.multi_user:
            args.append("--multi-user")
        if self.verbose.strip():
            args += ["--verbose", self.verbose.strip()]
        if self.log_stdout:
            args.append("--log-stdout")
        if self.enable_manager:
            args.append("--enable-manager")
        if self.disable_manager_ui:
            args.append("--disable-manager-ui")
        if self.enable_manager_legacy_ui:
            args.append("--enable-manager-legacy-ui")
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
    venv_manager: str = "venv"
    launch: LaunchOptions = field(default_factory=LaunchOptions)
    full: "FullOptions" = field(default_factory=FullOptions)
    network: NetworkOptions = field(default_factory=NetworkOptions)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        cfg = cls()
        for key in ("comfy_path", "python_path_override", "theme", "language", "expert_mode", "venv_manager"):
            if key in data:
                setattr(cfg, key, data[key])
        if isinstance(data.get("launch"), dict):
            cfg.launch = LaunchOptions(**{**asdict(cfg.launch), **data["launch"]})
        if isinstance(data.get("network"), dict):
            cfg.network = NetworkOptions(**{**asdict(cfg.network), **data["network"]})
        if isinstance(data.get("full"), dict):
            cfg.full = FullOptions(**{**asdict(cfg.full), **data["full"]})
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
