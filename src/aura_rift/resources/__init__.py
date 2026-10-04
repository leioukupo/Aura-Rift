"""Bundled visual resources and safe lookup helpers.

The binary files in this package are extracted from the local
``StableDiffusionWebUILauncher.Resources.dll`` shipped with ComfyUI-Aki v3.
Callers should use these helpers instead of assuming a source-tree path; the
same code works from an editable checkout and from an installed wheel.
"""

from __future__ import annotations

import hashlib
import importlib.resources as _resources
import tempfile
from pathlib import Path
import os


_PACKAGE = __name__
_CACHE_ROOT = Path(tempfile.gettempdir()) / "aura-rift-resources"


def _normalise_name(name: str | Path) -> str:
    value = str(name).replace("\\", "/").lstrip("/")
    # Keep lookup constrained to this package.  A traversal is a programming
    # error, not a reason to read an arbitrary user path.
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError(f"resource name escapes package: {name!r}")
    return "/".join(parts)


def resource_bytes(name: str | Path) -> bytes | None:
    """Read a bundled resource as bytes, returning ``None`` when absent."""

    key = _normalise_name(name)
    if not key:
        return None
    try:
        return _resources.files(_PACKAGE).joinpath(*key.split("/")).read_bytes()
    except (FileNotFoundError, IsADirectoryError, OSError, TypeError):
        return None


def resource_path(name: str | Path) -> Path | None:
    """Return a filesystem path for a bundled resource.

    Wheels normally expose resources as regular files.  For an importer that
    stores the package in a zip, the resource is copied to a per-user temp
    cache so the returned path remains valid after ``importlib`` closes its
    extraction context.  Missing resources return ``None``.
    """

    key = _normalise_name(name)
    if not key:
        return None
    try:
        traversable = _resources.files(_PACKAGE).joinpath(*key.split("/"))
        if not traversable.is_file():
            return None
        try:
            path = Path(traversable)
        except TypeError:
            path = None
        if path is not None and path.is_file():
            return path
        payload = traversable.read_bytes()
    except (FileNotFoundError, IsADirectoryError, OSError, TypeError):
        return None

    digest = hashlib.sha256(key.encode("utf-8") + b"\0" + payload).hexdigest()[:16]
    cached = _CACHE_ROOT / digest / Path(key).name
    try:
        if not cached.is_file() or cached.stat().st_size != len(payload):
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(payload)
        return cached
    except OSError:
        return None


def get_resource_path(name: str | Path) -> Path | None:
    """Backward-compatible alias for :func:`resource_path`."""

    return resource_path(name)


def resource_names(prefix: str = "") -> tuple[str, ...]:
    """List bundled resource names below *prefix* (recursively)."""

    root = _resources.files(_PACKAGE)
    prefix_key = _normalise_name(prefix)
    if prefix_key:
        root = root.joinpath(*prefix_key.split("/"))
    if not root.is_dir():
        return ()

    found: list[str] = []

    def walk(node, relative: str) -> None:
        try:
            children = tuple(node.iterdir())
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name == "__pycache__":
                continue
            child_rel = f"{relative}/{child.name}" if relative else child.name
            if child.is_dir():
                walk(child, child_rel)
            elif child.is_file() and child.suffix.lower() not in {".py", ".pyc"}:
                found.append(child_rel)

    walk(root, prefix_key)
    return tuple(sorted(found))


def find_resource(*names: str | Path) -> Path | None:
    """Return the first existing resource among *names*."""

    for name in names:
        path = resource_path(name)
        if path is not None:
            return path
    return None


def banner(name: str = "hanabi.jpg") -> Path | None:
    """Return a banner path, with the extracted Hanabi image as fallback."""

    return find_resource(name, "hanabi.jpg", "banner.jpg", "about_bg.jpg")


def avatar(name: str = "icon_minimi.png") -> Path | None:
    """Return the launcher avatar/icon path."""

    return find_resource(name, "icon_minimi.png", "avatar.png", "icon.png")


def font(name: str = "cascadiamono.ttf") -> Path | None:
    """Return a bundled font path, if present."""

    return find_resource(name, "cascadiamono.ttf", "segmdl2.ttf")


def load_pixmap(name: str, pixel_size: int | None = None):
    """Load a bundled image as ``QPixmap`` without making Qt mandatory.

    ``None`` is returned for absent/invalid images.  The local import keeps
    command-line and packaging tools usable in environments without PySide6.
    """

    path = resource_path(name)
    if path is None:
        return None
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap
    except ImportError:
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull() or pixel_size is None or pixel_size <= 0:
        return pixmap if not pixmap.isNull() else None
    return pixmap.scaled(pixel_size, pixel_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def register_font(name: str = "cascadiamono.ttf") -> int:
    """Register a bundled font with Qt and return its id (or ``-1``)."""

    path = resource_path(name)
    if path is None:
        return -1
    try:
        from PySide6.QtGui import QFontDatabase
    except ImportError:
        return -1
    return int(QFontDatabase.addApplicationFont(str(path)))


def register_system_cjk_font() -> int:
    """Register a locally available CJK font for deterministic Qt fallback.

    Linux distributions normally expose Noto CJK through fontconfig.  Minimal
    CI/offscreen images (and some portable Windows Qt bundles) do not expose
    those fonts to Qt's family list, even though a system font file exists.
    Registering a known path makes Chinese labels render instead of tofu while
    keeping the package free of a large duplicate CJK font.
    """
    candidates = [
        os.environ.get("AURA_RIFT_CJK_FONT", ""),
        # Linux distributions / containers.
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
        # Common portable/Windows installations.
        r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    try:
        from PySide6.QtGui import QFontDatabase
    except ImportError:
        return -1
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_file():
            continue
        try:
            font_id = int(QFontDatabase.addApplicationFont(str(path)))
        except (OSError, TypeError, ValueError):
            continue
        if font_id >= 0:
            return font_id
    return -1


__all__ = [
    "avatar",
    "banner",
    "find_resource",
    "font",
    "get_resource_path",
    "load_pixmap",
    "register_font",
    "register_system_cjk_font",
    "resource_bytes",
    "resource_names",
    "resource_path",
]
