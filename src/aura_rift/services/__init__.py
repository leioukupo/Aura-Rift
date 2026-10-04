"""Service layer for Aura-Rift.

Imports are kept lightweight; the public helpers below do not require Qt.
"""

from .catalog import LauncherCatalog, load_catalog, read_data
from .comfy import (
    command_environment,
    filter_cli_args,
    model_download_environment,
    platform_blocked_cli_flags,
    supported_cli_flags,
)
from .hotfixes import HotfixManager
from .native_components import NativeComponentService
from .troubleshooter import Troubleshooter

__all__ = [
    "HotfixManager",
    "LauncherCatalog",
    "NativeComponentService",
    "Troubleshooter",
    "load_catalog",
    "command_environment",
    "filter_cli_args",
    "model_download_environment",
    "platform_blocked_cli_flags",
    "read_data",
    "supported_cli_flags",
]

