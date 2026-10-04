from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


REGISTRY_URL = "https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main/custom-node-list.json"


@dataclass
class ExtensionEntry:
    title: str
    reference: str
    author: str
    repository_url: str
    description: str
    category: str = "其他"
    installed: bool = False


def _parse_node_list(data: object, category: str = "") -> list[ExtensionEntry]:
    """Parse a ComfyUI-Manager custom-node-list.json structure (dict or list)."""
    nodes: list[dict] = []
    if isinstance(data, dict):
        nodes = data.get("custom_nodes", [])
    elif isinstance(data, list):
        nodes = data
    entries: list[ExtensionEntry] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        title = node.get("title", "") or node.get("name", "")
        reference = node.get("reference", "") or ""
        author = node.get("author", "")
        files = node.get("files", [])
        # Manager registries normally use a list, while older/custom mirrors
        # occasionally emit one repository URL as a scalar.  Indexing a
        # scalar would keep only its first character and silently create a
        # broken install target, so normalize both shapes explicitly.
        if isinstance(files, str):
            repo_url = files.strip() or reference
        elif isinstance(files, (list, tuple)):
            repo_url = files[0] if files else reference
        else:
            repo_url = reference
        # Keep the model string-only even for partially malformed mirrors;
        # search/installed-directory matching relies on `.lower()` later.
        title = str(title or "")
        reference = str(reference or "")
        author = str(author or "")
        repo_url = str(repo_url or "")
        if not repo_url:
            continue
        desc = node.get("description", "")
        if isinstance(desc, list):
            desc = " ".join(str(d) for d in desc)
        cat = node.get("category", category or "其他")
        # Mirrors occasionally encode category as null/list metadata.  The
        # search model is intentionally string-only, so normalize that field
        # just like title/author above rather than failing on ``.lower()``.
        cat = str(cat or category or "其他")
        entries.append(ExtensionEntry(
            title=title,
            reference=reference,
            author=author,
            repository_url=repo_url,
            description=str(desc)[:300],
            category=cat,
        ))
    return entries


def load_local_extensions(manager_path: Path) -> list[ExtensionEntry]:
    """Load extensions from a locally installed ComfyUI-Manager database.

    Reads the top-level custom-node-list.json and the node_db subdirectories,
    deduplicating by repository URL.
    """
    if not manager_path.exists():
        return []
    seen: set[str] = set()
    entries: list[ExtensionEntry] = []

    # Top-level file (largest, ~5000 entries)
    top_file = manager_path / "custom-node-list.json"
    if top_file.exists():
        try:
            for entry in _parse_node_list(json.loads(top_file.read_text(encoding="utf-8"))):
                key = entry.repository_url.lower()
                if key and key not in seen:
                    seen.add(key)
                    entries.append(entry)
        except Exception:
            pass

    # node_db subdirectories (categorized)
    node_db = manager_path / "node_db"
    if node_db.exists():
        for sub in sorted(node_db.iterdir()):
            fpath = sub / "custom-node-list.json"
            if not fpath.exists():
                continue
            try:
                for entry in _parse_node_list(
                    json.loads(fpath.read_text(encoding="utf-8")),
                    category=sub.name,
                ):
                    key = entry.repository_url.lower()
                    if key and key not in seen:
                        seen.add(key)
                        entries.append(entry)
            except Exception:
                pass

    entries.sort(key=lambda e: e.title.lower())
    return entries


def fetch_remote_extensions(timeout: int = 30, registry_url: str = REGISTRY_URL) -> list[ExtensionEntry]:
    """Fetch extensions from the online ComfyUI-Manager registry (fallback)."""
    # ``curl`` is optional on desktop Linux (and absent on many test/CI
    # images).  Missing executables, timeouts, and a malformed response must
    # all fall through to urllib instead of aborting the registry page.
    try:
        curl = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), registry_url],
            text=True, capture_output=True, timeout=timeout + 5,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        curl = None
    if curl is not None and curl.returncode == 0 and curl.stdout.strip():
        try:
            parsed = _parse_node_list(json.loads(curl.stdout))
            if parsed:
                return parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    from urllib.request import urlopen
    try:
        with urlopen(registry_url, timeout=timeout) as resp:
            return _parse_node_list(json.loads(resp.read().decode()))
    except Exception:
        return []


def get_extensions(
    comfy_path: Path,
    timeout: int = 30,
    registry_url: str | None = None,
) -> list[ExtensionEntry]:
    """Get available extensions from local Manager DB, falling back to remote."""
    manager_path = comfy_path / "custom_nodes" / "ComfyUI-Manager"
    if manager_path.exists():
        local = load_local_extensions(manager_path)
        if local:
            return local
    return fetch_remote_extensions(timeout, registry_url or REGISTRY_URL)


def mark_installed(entries: list[ExtensionEntry], custom_nodes_dir: Path) -> list[ExtensionEntry]:
    """Mark which extensions are already installed."""
    if not custom_nodes_dir.exists():
        return entries
    installed_dirs: set[str] = set()
    for child in custom_nodes_dir.iterdir():
        if child.is_dir():
            # ComfyUI-Manager disables a node by appending ``.disabled`` to
            # its directory name.  Treat that node as installed while keeping
            # the enabled state available to callers through the suffix.
            name = child.name.lower()
            if name.endswith(".disabled"):
                name = name[: -len(".disabled")]
            installed_dirs.add(name)
    for entry in entries:
        dir_name = entry.repository_url.rstrip("/").split("/")[-1].removesuffix(".git").lower()
        entry.installed = dir_name in installed_dirs
    return entries


def search_entries(entries: list[ExtensionEntry], query: str) -> list[ExtensionEntry]:
    """Filter entries by any text field."""
    if not query.strip():
        return entries
    q = query.strip().lower()
    return [
        e for e in entries
        if q in e.title.lower()
        or q in e.reference.lower()
        or q in e.author.lower()
        or q in e.category.lower()
        or q in e.description.lower()
    ]
