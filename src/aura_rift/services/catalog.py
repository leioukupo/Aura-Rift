"""Reader for the data bundle shipped with ComfyUI-Aki.

The upstream launcher appends a small integrity/signature trailer to
``data.json``.  It is not valid JSON after the trailer, so using
``json.loads`` directly makes otherwise healthy installations look corrupt.
This module parses the first JSON value and deliberately ignores bytes after
it while still providing a deterministic, offline fallback.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from aura_rift.config import repo_root, user_config_dir


DEFAULT_CATALOG: dict[str, Any] = {
    "version": "builtin",
    "front_page_announcement": "",
    "model_server": "",
    "mirrors": {},
    "utilities": [],
    "native_component_requirements": {
        "platform_independent": {},
        "platform_dependent": {},
    },
    "hotfixes": [],
    "torch_versions": [],
    "branches": [],
    "stable_versions": {},
    "onnxruntime_releases": [],
}


def _candidate_paths(
    *,
    catalog_path: str | Path | None = None,
    comfy_path: str | Path | None = None,
) -> list[Path]:
    """Return catalog candidates in user/project precedence order.

    The Windows launcher keeps ``data.json`` beside the selected ComfyUI
    checkout.  Aura-Rift also has an explicit override in ``AppConfig``;
    accepting both here keeps all consumers (not just the UI) on the same
    catalog.  Empty strings are intentionally ignored -- ``Path("")`` would
    otherwise resolve to the current directory and make a missing override
    look like a valid file.
    """
    candidates: list[Path] = []
    if catalog_path is not None and str(catalog_path).strip():
        candidates.append(Path(catalog_path).expanduser())
    if comfy_path is not None and str(comfy_path).strip():
        candidates.append(Path(comfy_path).expanduser() / ".launcher" / "data.json")
    root = repo_root()
    candidates.extend([
        Path.cwd() / ".launcher" / "data.json",
        root / "ComfyUI-aki-v3" / ".launcher" / "data.json",
        root / ".launcher" / "data.json",
        user_config_dir() / "data.json",
    ])
    # Preserve ordering while avoiding duplicate paths (notably when the
    # configured checkout is the repository checkout itself).
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.expanduser())
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _bundled_fallback() -> dict[str, Any]:
    """Load the small offline catalog shipped in the Python package."""
    try:
        from importlib import resources
        raw = resources.files("aura_rift").joinpath("resources", "launcher_data.json").read_bytes()
        value = _decode_json_prefix(raw)
        return value if isinstance(value, dict) else copy.deepcopy(DEFAULT_CATALOG)
    except Exception:
        return copy.deepcopy(DEFAULT_CATALOG)


def _decode_json_prefix(raw: bytes | str) -> Any:
    """Decode one JSON value from *raw*, accepting arbitrary trailing bytes."""
    if isinstance(raw, bytes):
        # The catalog itself is UTF-8.  ``replace`` is intentional: a binary
        # signature may contain an incomplete code point after the JSON value.
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = raw.lstrip("\ufeff")
    text = text.lstrip()
    decoder = json.JSONDecoder()
    try:
        value, _end = decoder.raw_decode(text)
        return value
    except json.JSONDecodeError as first_error:
        # Be tolerant of a log/header accidentally prepended by a downloader,
        # but only search for JSON object/array starts (never evaluate text).
        starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        for index in sorted(starts):
            try:
                value, _end = decoder.raw_decode(text[index:])
                return value
            except json.JSONDecodeError:
                continue
        raise first_error


def read_data(
    path: str | Path | bytes | bytearray | None = None,
    *,
    fallback: Mapping[str, Any] | None = None,
    strict: bool = False,
    catalog_path: str | Path | None = None,
    comfy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read a launcher catalog from disk.

    ``path`` may be a filesystem path or raw JSON bytes, which is convenient
    for tests and callers that already downloaded the bundle.  Missing files,
    malformed JSON, and a non-object root use a deep-copied fallback unless
    ``strict`` is true, in which case the original exception is raised.
    """
    default = copy.deepcopy(dict(_bundled_fallback() if fallback is None else fallback))
    if path is None:
        selected = next(
            (candidate for candidate in _candidate_paths(catalog_path=catalog_path, comfy_path=comfy_path)
             if candidate.is_file()),
            None,
        )
        if selected is None:
            return default
        path = selected

    try:
        if isinstance(path, (bytes, bytearray)):
            raw = bytes(path)
        else:
            raw = Path(path).read_bytes()
        value = _decode_json_prefix(raw)
        if not isinstance(value, dict):
            raise ValueError("launcher catalog root must be a JSON object")
        # Do not let a malformed section poison the whole service.  The raw
        # schema is intentionally open-ended, but a shallow dict is required.
        return value
    except Exception:
        if strict:
            raise
        return default


def load_catalog(
    path: str | Path | bytes | bytearray | None = None,
    *,
    fallback: Mapping[str, Any] | None = None,
    strict: bool = False,
    catalog_path: str | Path | None = None,
    comfy_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compatibility alias for :func:`read_data`."""
    return read_data(
        path,
        fallback=fallback,
        strict=strict,
        catalog_path=catalog_path,
        comfy_path=comfy_path,
    )


class LauncherCatalog(Mapping[str, Any]):
    """Lazy-ish mapping facade around the launcher data bundle.

    The object exposes common sections as attributes (``announcement``,
    ``utilities``, ``hotfixes`` and so on) while retaining mapping behavior for
    integrations that expect the original JSON dictionary.
    """

    def __init__(
        self,
        path: str | Path | bytes | bytearray | None = None,
        *,
        fallback: Mapping[str, Any] | None = None,
        strict: bool = False,
        data: Mapping[str, Any] | None = None,
        config: Any | None = None,
        comfy_path: str | Path | None = None,
    ) -> None:
        # ``config`` is duck-typed to avoid importing AppConfig (which would
        # create a config -> catalog import cycle for callers that only need
        # configuration migration).  Explicit ``path`` always wins.
        config_catalog = getattr(config, "catalog_path", "") if config is not None else ""
        config_comfy = getattr(config, "comfy_path", "") if config is not None else ""
        if path is None and str(config_catalog or "").strip():
            path = str(config_catalog).strip()
        self._comfy_path = comfy_path or config_comfy or None
        self.path = None if isinstance(path, (bytes, bytearray)) else (
            Path(path).expanduser() if path is not None and str(path).strip() else None
        )
        self._raw_path = bytes(path) if isinstance(path, (bytes, bytearray)) else None
        self._fallback = copy.deepcopy(dict(_bundled_fallback() if fallback is None else fallback))
        self.strict = strict
        self._data: dict[str, Any] = dict(data) if isinstance(data, Mapping) else {}
        self._loaded = data is not None
        if not self._loaded:
            self.load()

    @classmethod
    def from_config(cls, config: Any | None = None, *, fallback: Mapping[str, Any] | None = None,
                    strict: bool = False) -> "LauncherCatalog":
        """Construct a catalog using an AppConfig-like object.

        ``catalog_path`` is preferred; otherwise ``<comfy_path>/.launcher``
        is checked before the normal checkout/package candidates.
        """
        return cls(None, fallback=fallback, strict=strict, config=config)

    @staticmethod
    def read_data(
        path: str | Path | bytes | bytearray | None = None,
        *,
        fallback: Mapping[str, Any] | None = None,
        strict: bool = False,
        catalog_path: str | Path | None = None,
        comfy_path: str | Path | None = None,
    ) -> dict[str, Any]:
        return read_data(
            path,
            fallback=fallback,
            strict=strict,
            catalog_path=catalog_path,
            comfy_path=comfy_path,
        )

    def load(self, *, force: bool = False) -> dict[str, Any]:
        if self._loaded and not force:
            return self._data
        source = self._raw_path if self._raw_path is not None else self.path
        # A stale explicit override should not hide a valid catalog beside the
        # selected ComfyUI checkout.  Keep the override first in candidate
        # order, then continue through the normal fallbacks when it is absent.
        missing_override = self.path is not None and not self.path.is_file() and not self.strict
        self._data = read_data(
            None if missing_override else source,
            fallback=self._fallback,
            strict=self.strict,
            catalog_path=self.path if missing_override else None,
            comfy_path=self._comfy_path,
        )
        self._loaded = True
        return self._data

    def reload(self, *, force: bool = True) -> dict[str, Any]:
        """Read the selected catalog again (forced by default).

        Keep the old ``reload(force=...)`` calling convention supplied by the
        method alias while making a plain ``reload()`` do what callers expect.
        """
        return self.load(force=force)

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @property
    def announcement(self) -> str:
        value = self._data.get("front_page_announcement", self._data.get("announcement", ""))
        return str(value or "")

    @property
    def utilities(self) -> Any:
        return self._data.get("utilities", [])

    @property
    def tools(self) -> Any:
        """Alias used by older catalog consumers for the utilities section."""
        return self.utilities

    @property
    def pytorch_versions(self) -> Any:
        """Alias for the catalog's Torch/PyTorch version list."""
        return self._data.get("torch_versions", [])

    @property
    def torch_versions(self) -> Any:
        """Short alias used by the original launcher data adapter."""
        return self.pytorch_versions

    @property
    def mirrors(self) -> Mapping[str, Any]:
        """Return the raw mirror section without exposing mutable internals."""
        value = self._data.get("mirrors", {})
        return dict(value) if isinstance(value, Mapping) else {}

    @property
    def branches(self) -> list[dict[str, Any]]:
        """Normalized development-branch entries."""
        return self.version_entries("comfyui", stable=False)

    @property
    def stable_versions(self) -> Mapping[str, Any]:
        value = self._data.get("stable_versions", {})
        return dict(value) if isinstance(value, Mapping) else {}

    @property
    def hotfixes(self) -> list[Any]:
        value = self._data.get("hotfixes", [])
        return value if isinstance(value, list) else []

    @property
    def patches(self) -> list[Any]:
        """Alias for hotfix entries."""
        return self.hotfixes

    @property
    def native_component_requirements(self) -> dict[str, Any]:
        value = self._data.get("native_component_requirements", {})
        return value if isinstance(value, dict) else {}

    @property
    def pip_indexes(self) -> list[dict[str, Any]]:
        """Normalized PyPI mirror entries from the catalog."""
        mirrors = self._data.get("mirrors", {})
        value = mirrors.get("pip_index", []) if isinstance(mirrors, Mapping) else []
        return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

    @property
    def extension_index_urls(self) -> dict[str, str]:
        mirrors = self._data.get("mirrors", {})
        value = mirrors.get("extension_index_url", {}) if isinstance(mirrors, Mapping) else {}
        if isinstance(value, str):
            return {"comfyui": value}
        return {
            str(key): str(url)
            for key, url in value.items()
            if str(key).strip() and isinstance(url, str) and url.strip()
        } if isinstance(value, Mapping) else {}

    def extension_index_url(self, core: str = "comfyui") -> str:
        """Return the extension registry URL for *core*, if catalogued."""
        return self.extension_index_urls.get(str(core or "comfyui"), "")

    @property
    def hf_catalog(self) -> Any:
        mirrors = self._data.get("mirrors", {})
        return mirrors.get("hf_mirror", {}) if isinstance(mirrors, Mapping) else {}

    @property
    def git_mirrors(self) -> list[dict[str, Any]]:
        """Return normalized source/destination Git mirror rules.

        绘世's catalog stores repository mirrors as ``src`` (one or more
        repository prefixes) and ``dest`` (the corresponding mirror prefix),
        rather than as a single universal proxy URL.  Keeping that distinction
        here prevents a destination such as ``.../comfyui`` from being
        accidentally concatenated with a complete GitHub URL.
        """
        mirrors = self._data.get("mirrors", {})
        raw = mirrors.get("git_mirrors", []) if isinstance(mirrors, Mapping) else []
        if not isinstance(raw, list):
            return []
        result: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            dest = str(item.get("dest", "") or "").strip()
            sources = item.get("src", item.get("sources", []))
            if isinstance(sources, str):
                sources = [sources]
            if not dest or not isinstance(sources, (list, tuple)):
                continue
            clean_sources = [str(source).strip() for source in sources if str(source).strip()]
            if clean_sources:
                result.append({"src": clean_sources, "dest": dest})
        return result

    def resolve_git_url(self, url: str, mirror: str | None = None) -> str:
        """Resolve *url* through a selected catalog Git mirror.

        ``mirror`` may be the exact catalog destination, a source alias, or a
        generic URL prefix ending in ``/``.  If no safe rule matches, the
        original URL is returned unchanged.
        """
        original = str(url or "").strip()
        selected = str(mirror or "").strip()
        if not original or not selected:
            return original
        rules = self.git_mirrors
        for rule in rules:
            destination = str(rule.get("dest", "")).strip()
            selected_normalized = selected.rstrip("/")
            source_aliases: set[str] = set()
            for item in rule.get("src", []):
                alias = str(item).strip().rstrip("/")
                if not alias:
                    continue
                source_aliases.add(alias)
                if "://" not in alias and not alias.startswith("git@"):
                    source_aliases.add("https://github.com/" + alias)
            if selected_normalized != destination.rstrip("/") and selected_normalized not in source_aliases:
                continue
            for source in rule.get("src", []):
                source_text = str(source).strip().rstrip("/")
                variants = [source_text]
                if "://" not in source_text and not source_text.startswith("git@"):
                    variants.append("https://github.com/" + source_text)
                for variant in variants:
                    if (
                        original == variant
                        or original.startswith(variant + "/")
                        or original.startswith(variant + ".git")
                    ):
                        suffix = original[len(variant):]
                        return destination.rstrip("/") + suffix
        # A trailing slash unambiguously denotes a generic proxy prefix; keep
        # support for the older Aura-Rift setting format.
        if selected.endswith("/") and selected.startswith(("http://", "https://")):
            return selected.rstrip("/") + "/" + original
        return original

    def git_mirror_rule_pairs(self, mirror: str | None = None) -> list[tuple[str, str]]:
        """Return validated ``(source, destination)`` pairs for Git config."""
        selected = str(mirror or "").strip()
        pairs: list[tuple[str, str]] = []
        for rule in self.git_mirrors:
            destination = str(rule.get("dest", "")).strip()
            aliases: set[str] = set()
            for item in rule.get("src", []):
                alias = str(item).strip().rstrip("/")
                if not alias:
                    continue
                aliases.add(alias)
                if "://" not in alias and not alias.startswith("git@"):
                    aliases.add("https://github.com/" + alias)
            if selected and selected.rstrip("/") not in {destination.rstrip("/"), *aliases}:
                continue
            for source in rule.get("src", []):
                source_text = str(source).strip()
                if not source_text:
                    continue
                variants = [source_text]
                if "://" not in source_text and not source_text.startswith("git@"):
                    variants.append("https://github.com/" + source_text)
                pairs.extend((variant, destination) for variant in variants)
        return pairs

    def version_entries(self, core: str = "comfyui", stable: bool = True) -> list[dict[str, Any]]:
        """Normalize catalog version/branch metadata for the version page."""
        if stable:
            source = self._data.get("stable_versions", {})
            values = source.get(core, []) if isinstance(source, Mapping) else []
        else:
            values = self._data.get("branches", [])
        if not isinstance(values, list):
            return []
        result: list[dict[str, Any]] = []
        for value in values:
            if not isinstance(value, Mapping):
                continue
            result.append(dict(value))
        return result

    def section(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    def get_section(self, name: str, default: Any = None) -> Any:
        return self.section(name, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __getattr__(self, name: str) -> Any:
        # Convenient access to less common sections such as torch_versions and
        # branches.  Raise AttributeError correctly for introspection tools.
        data = self.__dict__.get("_data", {})
        if name in data:
            return data[name]
        raise AttributeError(name)


__all__ = ["DEFAULT_CATALOG", "LauncherCatalog", "load_catalog", "read_data"]
