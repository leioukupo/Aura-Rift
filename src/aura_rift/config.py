from __future__ import annotations

import json
import math
import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


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


def _dataclass_field_names(cls: type[Any]) -> set[str]:
    """Return public constructor fields for a configuration dataclass.

    Configuration files live longer than a launcher release.  Keeping this
    helper local (rather than calling ``Class(**data)``) lets us ignore fields
    introduced by a newer release while retaining them for a later save.
    """
    from dataclasses import fields

    return {
        item.name
        for item in fields(cls)
        if item.init and not item.name.startswith("_") and item.name != "extra"
    }


def _merge_config_values(
    cls: type[Any],
    data: object,
    defaults: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a persisted mapping into known values and forward-compatible extras."""
    if not isinstance(data, dict):
        return {}, {}
    known_names = _dataclass_field_names(cls)
    values = dict(defaults or {})
    extras: dict[str, Any] = {}
    for key, value in data.items():
        if key in known_names:
            values[key] = value
        else:
            extras[key] = value
    # ``extra`` was used by a couple of development snapshots.  Merge it into
    # the canonical forward-compatible bucket rather than losing its values.
    legacy_extra = data.get("extra")
    if isinstance(legacy_extra, dict):
        extras = {**legacy_extra, **extras}
    return values, extras


def _coerce_bool(value: object, default: bool = False) -> bool:
    """Coerce common JSON/UI representations without treating ``"false"`` as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "是", "启用"}:
            return True
        if normalized in {"0", "false", "no", "off", "否", "禁用", ""}:
            return False
    return default


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
    # Unknown keys are retained when a config is read and written again.  The
    # field is intentionally not emitted by ``asdict`` consumers directly;
    # use ``to_dict`` for serialization.
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: object) -> "LaunchOptions":
        defaults = {name: getattr(cls(), name) for name in _dataclass_field_names(cls)}
        values, extra = _merge_config_values(cls, data, defaults)
        for key in (
            "host", "vram_mode", "attention", "precision", "preview_method",
            "cache_strategy", "vae_precision", "text_enc_precision", "enable_cors",
            "output_directory", "input_directory", "extra_args",
        ):
            if values.get(key) is None:
                values[key] = defaults[key]
            elif not isinstance(values.get(key), str):
                values[key] = str(values[key])
        try:
            values["port"] = int(values.get("port", defaults["port"]))
        except (TypeError, ValueError):
            values["port"] = defaults["port"]
        for key in ("listen", "cpu_vae", "disable_auto_launch", "disable_smart_memory", "cuda_malloc"):
            values[key] = _coerce_bool(values.get(key), defaults[key])
        values["extra"] = extra
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        values = {
            name: getattr(self, name)
            for name in _dataclass_field_names(type(self))
        }
        values.update({key: value for key, value in self.extra.items() if key not in values})
        return values

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
        if self.preview_method and self.preview_method != "auto" and self.preview_method in {"none", "latent2rgb", "taesd"}:
            args.extend(["--preview-method", self.preview_method])
        if self.disable_auto_launch:
            args.append("--disable-auto-launch")

        cache_strategy = str(self.cache_strategy or "").strip().lower()
        if cache_strategy == "classic":
            args.append("--cache-classic")
        elif cache_strategy == "none":
            args.append("--cache-none")
        elif cache_strategy == "lru" or cache_strategy.startswith(("lru:", "lru=")):
            # ComfyUI's current parser declares ``--cache-lru`` as
            # ``type=int``.  The launcher UI exposes a simple LRU toggle, so
            # use a conservative one-entry default; power users can persist
            # ``lru:32`` (or ``lru=32``) in the config without producing an
            # argparse error.
            raw_limit = cache_strategy[4:] if cache_strategy != "lru" else "1"
            try:
                limit = int(raw_limit.strip())
            except (TypeError, ValueError):
                limit = 1
            args.extend(["--cache-lru", str(max(1, limit))])
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
            # A hand-edited config can contain an unmatched quote.  Do not
            # let that prevent the launcher from opening (or make a benign
            # settings migration reset the whole config); preserve the
            # whitespace-separated tokens as a conservative fallback.
            try:
                args.extend(shlex.split(self.extra_args))
            except ValueError:
                args.extend(self.extra_args.split())
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
    # Additional Linux/modern-ComfyUI switches.  They are appended after the
    # original field sequence to keep positional construction compatible with
    # early Aura-Rift releases.
    disable_cuda_malloc: bool = False
    disable_ipex_optimize: bool = False
    fp32_unet: bool = False
    bf16_unet: bool = False
    fp16_unet: bool = False
    fp8_e4m3fn_unet: bool = False
    fp8_e5m2_unet: bool = False
    gpu_only: bool = False
    cpu: bool = False
    # Empty means unset.  A bare ``--fast`` is represented by ``all``; a
    # comma/space-separated value may select the supported feature names.
    fast: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: object) -> "FullOptions":
        defaults = {name: getattr(cls(), name) for name in _dataclass_field_names(cls)}
        values, extra = _merge_config_values(cls, data, defaults)
        for key in {
            "cuda_device", "oneapi_device_selector", "cache_ram", "async_offload",
            "default_hashing_function", "tls_keyfile", "tls_certfile", "comfy_api_base",
            "database_url", "feature_flags", "base_directory", "temp_directory",
            "user_directory", "front_end_version", "front_end_root", "extra_model_paths_config",
            "whitelist_custom_nodes", "verbose", "fast",
        }:
            if values.get(key) is None:
                values[key] = defaults[key]
            elif not isinstance(values.get(key), str):
                values[key] = str(values[key])
        for key in {"default_device", "directml", "preview_size"}:
            raw_value = values.get(key, defaults[key])
            # Early UI snapshots serialized the DirectML combo's semantic
            # values instead of its integer payload.  Accept both spellings
            # during migration; unknown values still fall back safely.
            if key == "directml" and isinstance(raw_value, str):
                normalized = raw_value.strip().lower()
                if normalized in {"auto", "automatic", "on", "启用"}:
                    raw_value = -1
                elif normalized in {"off", "none", "disabled", "关闭", "禁用", ""}:
                    raw_value = -2
            try:
                values[key] = int(raw_value)
            except (TypeError, ValueError):
                values[key] = defaults[key]
        for key in {"reserve_vram", "vram_headroom", "max_upload_size"}:
            try:
                values[key] = float(values.get(key, defaults[key]))
            except (TypeError, ValueError):
                values[key] = defaults[key]
        for key in {
            "supports_fp8_compute", "disable_cuda_malloc", "disable_ipex_optimize", "enable_triton_backend", "force_channels_last", "fp16_intermediates",
            "fp32_unet", "fp64_unet", "bf16_unet", "fp16_unet", "fp8_e4m3fn_unet", "fp8_e5m2_unet",
            "fp8_e8m0fnu_unet", "force_non_blocking", "high_ram", "gpu_only", "cpu", "disable_async_offload",
            "disable_dynamic_vram", "enable_dynamic_vram", "fast_disk", "disable_pinned_memory", "deterministic",
            "use_sage_attention", "use_flash_attention", "disable_xformers", "force_upcast_attention",
            "dont_upcast_attention", "fp16_text_enc", "fp32_text_enc", "bf16_text_enc",
            "enable_compress_response_body", "enable_assets", "enable_asset_hashing", "mmap_torch_files",
            "disable_mmap", "dont_print_server", "disable_metadata", "disable_all_custom_nodes",
            "disable_api_nodes", "multi_user", "log_stdout", "enable_manager", "disable_manager_ui",
            "enable_manager_legacy_ui",
        }:
            values[key] = _coerce_bool(values.get(key), defaults[key])
        values["extra"] = extra
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        values = {
            name: getattr(self, name)
            for name in _dataclass_field_names(type(self))
        }
        values.update({key: value for key, value in self.extra.items() if key not in values})
        return values

    def to_args(
        self,
        *,
        cache_strategy: str = "",
        cuda_malloc: bool = False,
        vram_mode: str = "",
        attention: str = "",
        text_enc_precision: str = "",
    ) -> list[str]:
        args: list[str] = []
        if self.cuda_device.strip():
            # The ComfyUI CLI declares this option as ``type=int``.  Validate
            # the hand-edited value before putting it in argv so a malformed
            # config (or a multi-device string from another launcher) cannot
            # make the process fail during argparse startup.
            cuda_device = self.cuda_device.strip().replace(" ", "")
            if re.fullmatch(r"\d+", cuda_device):
                args += ["--cuda-device", cuda_device]
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
        if self.disable_ipex_optimize:
            args.append("--disable-ipex-optimize")
        if self.force_channels_last:
            args.append("--force-channels-last")
        if self.fp16_intermediates:
            args.append("--fp16-intermediates")
        # ComfyUI exposes these as one mutually-exclusive precision group.
        # Prefer the first option in this stable order when a hand-edited
        # configuration accidentally enables more than one.
        for enabled, flag in (
            (self.fp32_unet, "--fp32-unet"),
            (self.fp64_unet, "--fp64-unet"),
            (self.bf16_unet, "--bf16-unet"),
            (self.fp16_unet, "--fp16-unet"),
            (self.fp8_e4m3fn_unet, "--fp8_e4m3fn-unet"),
            (self.fp8_e5m2_unet, "--fp8_e5m2-unet"),
            (self.fp8_e8m0fnu_unet, "--fp8_e8m0fnu-unet"),
        ):
            if enabled:
                args.append(flag)
                break
        if self.force_non_blocking:
            args.append("--force-non-blocking")
        normalized_cache_strategy = str(cache_strategy or "").strip().lower()
        if self.cache_ram.strip() and normalized_cache_strategy not in {"classic", "lru", "none"} and not normalized_cache_strategy.startswith(("lru:", "lru=")):
            # ``--cache-ram`` accepts at most one optional float.  Older UI
            # snapshots allowed two space-separated thresholds; only the
            # first valid positive value can be represented by this ComfyUI
            # release, so discard the rest rather than shifting arguments.
            token = self.cache_ram.split()[0]
            try:
                threshold = float(token)
            except (TypeError, ValueError):
                threshold = 0.0
            if math.isfinite(threshold) and threshold > 0:
                args += ["--cache-ram", token]
        if self.high_ram:
            # ComfyUI treats this as the classic-cache shortcut.  It remains
            # a distinct persisted option so older launcher configurations can
            # round-trip without losing intent.
            args.append("--high-ram")
        if self.reserve_vram > 0:
            args += ["--reserve-vram", str(self.reserve_vram)]
        if self.vram_headroom > 0:
            args += ["--vram-headroom", str(self.vram_headroom)]
        async_emitted = False
        if self.async_offload == "on":
            args.append("--async-offload")
            async_emitted = True
        elif self.async_offload not in ("auto", "", "on"):
            try:
                streams = int(str(self.async_offload).strip())
            except (TypeError, ValueError):
                streams = 0
            if streams > 0:
                args += ["--async-offload", str(streams)]
                async_emitted = True
        if self.disable_async_offload and not async_emitted:
            args.append("--disable-async-offload")
        if self.disable_dynamic_vram:
            args.append("--disable-dynamic-vram")
        elif self.enable_dynamic_vram:
            args.append("--enable-dynamic-vram")
        if self.fast_disk:
            args.append("--fast-disk")
        if self.disable_pinned_memory:
            args.append("--disable-pinned-memory")
        if self.deterministic:
            args.append("--deterministic")
        fast_value = str(self.fast or "").strip().lower()
        if fast_value:
            valid_fast = {"fp16_accumulation", "fp8_matrix_mult", "cublas_ops", "autotune"}
            if fast_value in {"1", "true", "yes", "on", "all", "*"}:
                args.append("--fast")
            else:
                selected = [item for item in re.split(r"[\s,]+", fast_value) if item in valid_fast]
                if selected:
                    args.extend(["--fast", *dict.fromkeys(selected)])
        # All five attention modes belong to one ComfyUI argparse group.  The
        # compact launch page wins when it selected a mode; otherwise choose a
        # deterministic expert value even if a hand-edited config enabled both.
        launch_attention = str(attention or "").strip().lower()
        if launch_attention not in {"split", "quad", "pytorch"}:
            if self.use_sage_attention:
                args.append("--use-sage-attention")
            elif self.use_flash_attention:
                args.append("--use-flash-attention")
        if self.disable_xformers:
            args.append("--disable-xformers")
        if self.force_upcast_attention:
            args.append("--force-upcast-attention")
        elif self.dont_upcast_attention:
            args.append("--dont-upcast-attention")
        # Text-encoder precision is another mutually-exclusive group.  A
        # compact FP8 selection must not be combined with an expert FP16/32/
        # BF16 value.
        launch_text_precision = str(text_enc_precision or "").strip().lower()
        if launch_text_precision not in {"e4m3fn", "e5m2"}:
            if self.fp16_text_enc:
                args.append("--fp16-text-enc")
            elif self.fp32_text_enc:
                args.append("--fp32-text-enc")
            elif self.bf16_text_enc:
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
            # Kept as a migrated/forward-compatible setting.  The bundled
            # ComfyUI parser currently accepts this flag (newer releases may
            # use it for API routing), so preserve it when explicitly set.
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
        if self.default_hashing_function.strip() in {"md5", "sha1", "sha256", "sha512"}:
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
        if self.verbose.strip() in {"DEBUG", "DETAIL", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            args += ["--verbose", self.verbose.strip()]
        if self.log_stdout:
            args.append("--log-stdout")
        # The legacy Manager UI switch only has an effect when the Manager
        # feature itself is enabled.  The UI enforces this relationship, but
        # hand-edited/older configs must receive both flags as well.
        if self.enable_manager or self.enable_manager_legacy_ui:
            args.append("--enable-manager")
        # These two Manager UI modes form an argparse mutually-exclusive
        # group.  Prefer the explicit disable switch if a stale config has
        # accidentally persisted both values.
        if self.disable_manager_ui:
            args.append("--disable-manager-ui")
        elif self.enable_manager_legacy_ui:
            args.append("--enable-manager-legacy-ui")
        # These flags are mutually exclusive with the compact launch-page
        # vram/cuda controls.  Only emit them when the launch page left the
        # corresponding group in automatic mode.
        normalized_vram = str(vram_mode or "").strip().lower()
        if normalized_vram not in {"lowvram", "normalvram", "highvram", "novram"}:
            if self.gpu_only:
                args.append("--gpu-only")
            elif self.cpu:
                args.append("--cpu")
        if self.disable_cuda_malloc and not cuda_malloc:
            args.append("--disable-cuda-malloc")
        return args


# (label, index_url) — empty URL means official PyPI (no override).
PYPI_MIRRORS: list[tuple[str, str]] = [
    ("PyPI 官方源", ""),
    ("清华大学", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("中科大", "https://pypi.mirrors.ustc.edu.cn/simple"),
    ("阿里云", "https://mirrors.aliyun.com/pypi/simple"),
    ("南京大学", "https://mirror.nju.edu.cn/pypi/web/simple"),
    ("腾讯云", "https://mirrors.cloud.tencent.com/pypi/simple"),
    ("华为云", "https://mirrors.huaweicloud.com/repository/pypi/simple"),
]


@dataclass
class NetworkOptions:
    http_proxy: str = ""
    https_proxy: str = ""
    pypi_mirror: str = ""
    github_proxy: str = ""
    git_mirror: str = ""
    hf_mirror: str = ""
    extension_index_url: str = ""
    model_server: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: object) -> "NetworkOptions":
        defaults = {name: getattr(cls(), name) for name in _dataclass_field_names(cls)}
        values, extra = _merge_config_values(cls, data, defaults)
        # Prior releases persisted this as a boolean.  ``True`` historically
        # meant the Tsinghua mirror; preserve that behavior while accepting the
        # current URL string representation.
        mirror = values.get("pypi_mirror")
        if isinstance(mirror, bool):
            values["pypi_mirror"] = (
                "https://pypi.tuna.tsinghua.edu.cn/simple" if mirror else ""
            )
        for key in (
            "http_proxy", "https_proxy", "pypi_mirror", "github_proxy",
            "git_mirror", "hf_mirror", "extension_index_url", "model_server",
        ):
            if values.get(key) is None:
                values[key] = defaults[key]
            elif not isinstance(values.get(key), str):
                values[key] = str(values[key])
        values["extra"] = extra
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        values = {
            name: getattr(self, name)
            for name in _dataclass_field_names(type(self))
        }
        values.update({key: value for key, value in self.extra.items() if key not in values})
        return values

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
    # Keep the original positional constructor order stable.  Existing
    # integrations may still pass ``launch``, ``full`` and ``network`` as
    # positional arguments; newly introduced settings therefore live after
    # those legacy fields below.
    launch: LaunchOptions = field(default_factory=LaunchOptions)
    full: "FullOptions" = field(default_factory=FullOptions)
    network: NetworkOptions = field(default_factory=NetworkOptions)
    # Optional launcher data and safety preferences.  They deliberately have
    # inert defaults so old config files behave exactly as before.
    catalog_path: str = ""
    hotfixes_enabled: bool = True
    hotfix_states: dict[str, bool] = field(default_factory=dict)
    auto_check_dependencies: bool = True
    safe_mode: bool = False
    last_page: str = ""
    hotfix_backup_dir: str = ""
    # General settings introduced by the Linux UI.  Defaults are conservative
    # and preserve the behavior of pre-existing config files.
    proxy_enabled: bool = False
    proxy_for_git: bool = True
    proxy_for_pip: bool = True
    proxy_for_environment: bool = True
    proxy_for_models: bool = True
    default_browser: bool | str = True
    crash_auto_restart: bool = False
    dependency_integrity_check: bool = True
    component_conflict_check: bool = True
    duplicate_extension_check: bool = True
    huggingface_offline: bool = False
    window_geometry: dict[str, int] = field(default_factory=dict)
    window_maximized: bool = False
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | object) -> "AppConfig":
        cfg = cls()
        if not isinstance(data, dict):
            return cfg

        # Names used by pre-1.0 snapshots.  Migration is intentionally
        # lossless: aliases are consumed into the current field while any
        # unrelated key remains in ``extra`` and survives a save/load cycle.
        aliases = {
            "comfy": "comfy_path",
            "comfy_dir": "comfy_path",
            "comfy_directory": "comfy_path",
            "python_path": "python_path_override",
            "python": "python_path_override",
            "theme_mode": "theme",
            "locale": "language",
            "is_expert": "expert_mode",
            "environment_manager": "venv_manager",
            "env_manager": "venv_manager",
            "launcher_catalog_path": "catalog_path",
            "catalog": "catalog_path",
            "enable_hotfixes": "hotfixes_enabled",
            "check_dependencies": "auto_check_dependencies",
            "enable_proxy": "proxy_enabled",
            "use_proxy": "proxy_enabled",
            "browser": "default_browser",
            "auto_open_browser": "default_browser",
            "auto_restart": "crash_auto_restart",
            "dependency_check": "dependency_integrity_check",
            "integrity_check": "dependency_integrity_check",
            "check_component_conflicts": "component_conflict_check",
            "check_duplicate_extensions": "duplicate_extension_check",
            "geometry": "window_geometry",
            "maximized": "window_maximized",
        }
        normalized = dict(data)
        for old, new in aliases.items():
            if new not in normalized and old in normalized:
                normalized[new] = normalized[old]

        # Older files occasionally stored nested sections under *_options or
        # flattened network keys at the top level.
        section_aliases = {
            "launch_options": "launch",
            "full_options": "full",
            "network_options": "network",
            "proxy": "network",
        }
        for old, new in section_aliases.items():
            if new not in normalized and isinstance(normalized.get(old), dict):
                normalized[new] = normalized[old]
        if "network" not in normalized:
            flat_network = {
                key: normalized[key]
                for key in (
                    "http_proxy", "https_proxy", "pypi_mirror", "github_proxy",
                    "git_mirror", "hf_mirror", "extension_index_url", "model_server",
                )
                if key in normalized
            }
            if flat_network:
                normalized["network"] = flat_network

        known = _dataclass_field_names(cls)
        # Construct with defaults already present.  Avoid ``asdict`` here: it
        # recursively includes the private compatibility buckets.
        for key in known:
            if key in normalized and key not in {"launch", "full", "network", "extra"}:
                setattr(cfg, key, normalized[key])

        if isinstance(normalized.get("launch"), dict):
            cfg.launch = LaunchOptions.from_dict(normalized["launch"])
        if isinstance(normalized.get("network"), dict):
            cfg.network = NetworkOptions.from_dict(normalized["network"])
            if "proxy_enabled" not in normalized and any(
                (cfg.network.http_proxy, cfg.network.https_proxy,
                 cfg.network.pypi_mirror, cfg.network.github_proxy,
                 cfg.network.git_mirror, cfg.network.hf_mirror,
                 cfg.network.extension_index_url, cfg.network.model_server)
            ):
                # Prior versions had no master switch; preserve their
                # effective behavior when migrating.
                cfg.proxy_enabled = True
        if isinstance(normalized.get("full"), dict):
            cfg.full = FullOptions.from_dict(normalized["full"])

        cfg.extra = {
            key: value
            for key, value in normalized.items()
            if key not in known
            and key != "extra"
            and key not in aliases
            and key not in section_aliases
            and key not in {
                "http_proxy", "https_proxy", "pypi_mirror", "github_proxy",
                "git_mirror", "hf_mirror", "extension_index_url", "model_server",
            }
        }
        # Preserve an explicit extra bucket, with current values taking
        # precedence if a key appears in both places.
        if isinstance(data.get("extra"), dict):
            cfg.extra = {**data["extra"], **cfg.extra}

        # Normalize values that are commonly edited by hand.  A malformed
        # known value should not make ConfigStore discard otherwise valid
        # settings and unknown keys.
        for key in ("comfy_path", "python_path_override", "theme", "language", "catalog_path", "last_page", "hotfix_backup_dir"):
            value = getattr(cfg, key)
            if value is None:
                setattr(cfg, key, getattr(cls(), key))
            elif not isinstance(value, str):
                setattr(cfg, key, str(value))
        if cfg.theme.strip().lower() not in {"dark", "light"}:
            cfg.extra.setdefault("theme_invalid", cfg.theme)
            cfg.theme = "dark"
        else:
            cfg.theme = cfg.theme.strip().lower()
        for key in (
            "expert_mode", "hotfixes_enabled", "auto_check_dependencies", "safe_mode",
            "proxy_enabled", "proxy_for_git", "proxy_for_pip", "proxy_for_environment",
            "proxy_for_models", "crash_auto_restart", "dependency_integrity_check",
            "component_conflict_check", "duplicate_extension_check", "huggingface_offline",
            "window_maximized",
        ):
            setattr(cfg, key, _coerce_bool(getattr(cfg, key), getattr(cls(), key)))
        if not isinstance(cfg.window_geometry, dict):
            cfg.window_geometry = {}
        else:
            geometry: dict[str, int] = {}
            for name, value in cfg.window_geometry.items():
                try:
                    geometry[str(name)] = int(value)
                except (TypeError, ValueError):
                    continue
            cfg.window_geometry = geometry
        if not isinstance(cfg.hotfix_states, dict):
            cfg.hotfix_states = {}
        else:
            cfg.hotfix_states = {
                str(name): _coerce_bool(value, True)
                for name, value in cfg.hotfix_states.items()
            }
        if not isinstance(cfg.default_browser, (bool, str)):
            cfg.default_browser = bool(cfg.default_browser)

        # Keep malformed values from breaking launch generation.  Unknown
        # manager names are retained in ``extra`` but use the safe default.
        # Coerce before the membership check: hand-edited JSON can contain a
        # list/dict here, which would otherwise raise ``TypeError`` because
        # those values are unhashable and make ConfigStore discard the whole
        # configuration.
        if not isinstance(cfg.venv_manager, str):
            cfg.extra.setdefault("venv_manager_invalid", cfg.venv_manager)
            cfg.venv_manager = "venv"
        else:
            cfg.venv_manager = cfg.venv_manager.strip().lower()
            if cfg.venv_manager not in {"venv", "poetry", "pdm", "uv", "conda"}:
                cfg.extra.setdefault("venv_manager_invalid", cfg.venv_manager)
                cfg.venv_manager = "venv"
        if isinstance(cfg.default_browser, str):
            browser_value = cfg.default_browser.strip().lower()
            if browser_value in {"true", "yes", "on", "1", "是", "启用"}:
                cfg.default_browser = True
            elif browser_value in {"false", "no", "off", "0", "否", "禁用", ""}:
                cfg.default_browser = False
        return cfg

    def to_dict(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            "comfy_path": self.comfy_path,
            "python_path_override": self.python_path_override,
            "theme": self.theme,
            "language": self.language,
            "expert_mode": self.expert_mode,
            "venv_manager": self.venv_manager,
            # Keep the serialized order used by the pre-Linux launcher.  JSON
            # objects are semantically unordered, but a few integrations and
            # human-edited snapshots rely on this stable grouping.
            "launch": self.launch.to_dict(),
            "full": self.full.to_dict(),
            "network": self.network.to_dict(),
            "catalog_path": self.catalog_path,
            "hotfixes_enabled": self.hotfixes_enabled,
            "hotfix_states": dict(self.hotfix_states),
            "auto_check_dependencies": self.auto_check_dependencies,
            "safe_mode": self.safe_mode,
            "last_page": self.last_page,
            "hotfix_backup_dir": self.hotfix_backup_dir,
            "proxy_enabled": self.proxy_enabled,
            "proxy_for_git": self.proxy_for_git,
            "proxy_for_pip": self.proxy_for_pip,
            "proxy_for_environment": self.proxy_for_environment,
            "proxy_for_models": self.proxy_for_models,
            "default_browser": self.default_browser,
            "crash_auto_restart": self.crash_auto_restart,
            "dependency_integrity_check": self.dependency_integrity_check,
            "component_conflict_check": self.component_conflict_check,
            "duplicate_extension_check": self.duplicate_extension_check,
            "huggingface_offline": self.huggingface_offline,
            "window_geometry": dict(self.window_geometry),
            "window_maximized": self.window_maximized,
        }
        values.update({key: value for key, value in self.extra.items() if key not in values})
        return values


class ConfigStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else user_config_dir() / "config.json"

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        try:
            return AppConfig.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
        # Atomic replacement prevents an interrupted GUI shutdown from leaving
        # a truncated config that would silently reset all settings next run.
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temp_name).replace(self.path)
        except Exception:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
            raise


def bundled_markdown(
    name: str,
    *,
    include_package: bool = True,
    extra_paths: Iterable[str | Path] | None = None,
) -> str:
    """Load a markdown resource from local overrides or the package.

    ``include_package=False`` is useful for data-driven pages such as the
    announcement: callers can first inspect an external/catalog value and
    only use the packaged copy as a final fallback.  ``extra_paths`` lets a
    selected ComfyUI checkout provide a per-install markdown override while
    retaining the historical cwd/repository/user search order.
    """
    paths: list[Path] = []
    if extra_paths:
        paths.extend(Path(path).expanduser() for path in extra_paths)
    paths.extend(Path(base) / name for base in (Path.cwd(), repo_root(), user_config_dir()))
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
    if not include_package:
        return ""
    # Editable checkouts use the project-root files above; installed wheels
    # need an in-package fallback so the announcement/about pages do not go
    # blank when launched outside the repository.
    try:
        from importlib import resources

        resource = resources.files("aura_rift").joinpath("resources", name)
        if resource.is_file():
            return resource.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, OSError, TypeError, UnicodeError):
        pass
    return ""
