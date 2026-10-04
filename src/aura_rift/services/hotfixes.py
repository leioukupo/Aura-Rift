"""Safe, opt-in hotfix management for Linux ComfyUI trees.

Catalog metadata is not executable code.  A patch is considered applicable
only when it contains an explicit Linux payload made of validated relative
paths and (preferably) SHA-256 hashes.  Every existing file is backed up before
the first replacement and a manifest makes revert deterministic.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from aura_rift.config import AppConfig
from aura_rift.services.catalog import LauncherCatalog


class HotfixError(RuntimeError):
    """Base error for rejected or failed hotfix operations."""


class UnsafePatchError(HotfixError):
    """Raised when a patch attempts path traversal or an unsafe operation."""


@dataclass
class HotfixResult:
    ok: bool
    name: str
    message: str
    backup: str = ""
    files: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "name": self.name,
            "message": self.message,
            "backup": self.backup,
            "files": list(self.files),
        }


@dataclass
class HotfixDefinition:
    name: str
    description: str = ""
    version: str = "全部"
    payload: Any = None
    enabled: bool = True
    visible: bool = True
    core_type: list[str] = field(default_factory=list)
    engine_type: list[str] = field(default_factory=list)
    since: list[str] = field(default_factory=list)
    until: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)


_PATCH_ROOT = ".aura-rift-hotfixes"
_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_id(value: object) -> str:
    text = _ID_RE.sub("-", str(value or "")).strip(".-")
    return text[:100] or "hotfix"


def _as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None and str(item)]
    return []


def _as_bool(value: object, default: bool = True) -> bool:
    """Parse catalog booleans without treating ``"false"`` as truthy."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled", "启用"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled", "禁用", ""}:
            return False
    return default


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HotfixManager:
    """List and apply/revert catalog-defined Linux hotfixes.

    Constructor arguments are intentionally permissive for UI integrations:
    ``catalog`` may be a LauncherCatalog, raw mapping, or a list of patch
    definitions; ``config`` is optional.
    """

    def __init__(
        self,
        root: str | Path,
        config: AppConfig | None = None,
        catalog: LauncherCatalog | Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
        *,
        patches: Iterable[Mapping[str, Any]] | None = None,
        backup_dir: str | Path | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.config = config
        self.platform_name = (platform_name or platform.system()).lower()
        self._lock = threading.RLock()
        self._catalog = catalog
        if patches is not None:
            self._patches_source: Any = list(patches)
        elif isinstance(catalog, (list, tuple)):
            self._patches_source = list(catalog)
        else:
            self._patches_source = None
        configured_backup = getattr(config, "hotfix_backup_dir", "") if config else ""
        self.backup_root = Path(backup_dir or configured_backup or (self.root / _PATCH_ROOT))
        if not self.backup_root.is_absolute():
            self.backup_root = self.root / self.backup_root
        self.backup_root = self.backup_root.resolve()

    @property
    def is_linux(self) -> bool:
        return self.platform_name.startswith("linux")

    def _raw_patches(self) -> list[Mapping[str, Any]]:
        source = self._patches_source
        if source is None:
            source = self._catalog
        if source is None:
            path = getattr(self.config, "catalog_path", "") if self.config else ""
            if not path and self.config:
                configured_root = str(getattr(self.config, "comfy_path", "") or "").strip()
                if configured_root:
                    candidate = Path(configured_root).expanduser() / ".launcher" / "data.json"
                    if candidate.is_file():
                        path = str(candidate)
            source = LauncherCatalog(path or None)
        if isinstance(source, LauncherCatalog):
            source = source.get("hotfixes", [])
        elif isinstance(source, Mapping):
            source = source.get("hotfixes", source.get("patches", []))
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes, Mapping)):
            return []
        return [item for item in source if isinstance(item, Mapping)]

    @staticmethod
    def _definition(raw: Mapping[str, Any]) -> HotfixDefinition:
        name = str(raw.get("name") or raw.get("id") or raw.get("title") or "").strip()
        payload = raw.get("linux")
        if payload is None:
            payload = raw.get("linux_payload", raw.get("payloads", raw.get("files")))
            if isinstance(payload, Mapping) and "linux" in payload:
                payload = payload.get("linux")
        if payload is None and isinstance(raw.get("platforms"), Mapping):
            payload = raw["platforms"].get("linux")
        metadata = dict(raw)
        return HotfixDefinition(
            name=name,
            description=str(raw.get("description") or raw.get("detail") or ""),
            version=str(raw.get("version") or raw.get("versions") or raw.get("applicable") or "全部"),
            payload=payload,
            enabled=_as_bool(raw.get("enabled", True), True),
            visible=_as_bool(raw.get("visible", True), True),
            core_type=_as_list(raw.get("core_type")),
            engine_type=_as_list(raw.get("engine_type")),
            since=_as_list(raw.get("since")),
            until=_as_list(raw.get("until")),
            metadata=metadata,
        )

    def _definitions(self) -> list[HotfixDefinition]:
        result: list[HotfixDefinition] = []
        seen: set[str] = set()
        for raw in self._raw_patches():
            item = self._definition(raw)
            if not item.name or item.name in seen:
                continue
            seen.add(item.name)
            result.append(item)
        return result

    def _manifest_paths(self, definition: HotfixDefinition) -> list[Path]:
        patch_dir = (self.backup_root / _safe_id(definition.name)).resolve()
        if not self._inside(self.backup_root, patch_dir):
            raise UnsafePatchError("补丁备份路径无效")
        if not patch_dir.exists():
            return []
        return sorted(patch_dir.glob("manifest-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    def _manifest_active(self, manifest_path: Path) -> tuple[bool, str]:
        """Check that every target still contains the bytes installed by a patch."""
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            records = payload.get("files", [])
            if not isinstance(records, list) or not records:
                return False, "补丁清单没有文件记录"
            for record in records:
                if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
                    return False, "补丁清单记录无效"
                target = (self.root / record["path"]).resolve()
                if (
                    not self._inside(self.root, target)
                    or self._has_symlink_component(record["path"])
                    or not target.is_file()
                ):
                    return False, f"目标文件不存在：{record['path']}"
                expected = str(record.get("new_sha256") or "").lower()
                if not expected or _sha256(target.read_bytes()) != expected:
                    return False, f"目标文件已被修改：{record['path']}"
            return True, ""
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return False, f"补丁清单无法读取：{exc}"

    @staticmethod
    def _inside(parent: Path, child: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    def _has_symlink_component(self, relative: str | Path) -> bool:
        """Return whether a relative path traverses a symlink below ``root``.

        Checking only the final path is insufficient: replacing a parent
        directory with a symlink between apply and revert could redirect the
        supposedly confined operation outside the ComfyUI tree.
        """
        cursor = self.root
        for part in Path(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return True
        return False

    def _current_revision(self) -> str:
        head = self.root / ".git" / "HEAD"
        if not head.exists():
            return ""
        try:
            import subprocess

            proc = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return proc.stdout.strip() if proc.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def _availability(self, definition: HotfixDefinition) -> tuple[bool, str]:
        if not definition.enabled:
            return False, "补丁已在清单中禁用"
        user_states = getattr(self.config, "hotfix_states", {}) if self.config else {}
        if isinstance(user_states, Mapping) and definition.name in user_states and not _as_bool(user_states[definition.name], True):
            return False, "补丁已由用户禁用"
        if not self.is_linux:
            return False, "补丁仅支持 Linux"
        if not definition.payload:
            return False, "清单没有 Linux 补丁载荷"
        if not definition.visible:
            return False, "补丁未公开"
        if definition.core_type or definition.engine_type:
            # These selectors are metadata from the Windows launcher.  They do
            # not identify a ComfyUI core, so only reject explicit unsupported
            # values rather than guessing.
            core = str(getattr(self.config, "core_type", "") or "") if self.config else ""
            engine = str(getattr(self.config, "engine_type", "") or "") if self.config else ""
            if definition.core_type and core and core not in definition.core_type:
                return False, "当前核心类型不匹配"
            if definition.engine_type and engine and engine not in definition.engine_type:
                return False, "当前后端类型不匹配"
        revision = self._current_revision()
        if revision:
            if definition.since and not any(revision.startswith(item) for item in definition.since):
                return False, "当前版本早于补丁适用范围"
            if definition.until and any(revision.startswith(item) for item in definition.until):
                return False, "当前版本已包含该修复"
        try:
            self._payload_files(definition)
        except Exception as exc:
            return False, str(exc)
        return True, ""

    def _payload_files(self, definition: HotfixDefinition) -> list[dict[str, Any]]:
        payload = definition.payload
        if isinstance(payload, Mapping):
            # Mapping path -> content/metadata is a compact schema.
            if "files" in payload and isinstance(payload["files"], (list, tuple, Mapping)):
                payload = payload["files"]
            elif "path" in payload or "target" in payload:
                payload = [payload]
            else:
                payload = [{"path": key, "content": value} for key, value in payload.items()]
        if not isinstance(payload, (list, tuple)):
            raise UnsafePatchError("Linux 补丁载荷格式无效")
        result: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, str):
                raise UnsafePatchError("补丁文件缺少内容")
            if not isinstance(item, Mapping):
                raise UnsafePatchError("补丁文件条目无效")
            raw_path = item.get("path") or item.get("target") or item.get("file")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise UnsafePatchError("补丁文件缺少相对路径")
            # Catalogs are exchanged between Windows and Linux launchers.
            # Normalize separators before validation so a payload such as
            # ``..\\outside.py`` cannot be interpreted as a harmless filename
            # on POSIX and then escape when the same definition is reused on
            # another platform.
            normalized_path = raw_path.replace("\\", "/")
            if re.match(r"^[A-Za-z]:", normalized_path):
                raise UnsafePatchError(f"补丁路径必须是相对路径：{raw_path}")
            relative = Path(normalized_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise UnsafePatchError(f"补丁路径越界：{raw_path}")
            if any(part in {"", "."} for part in relative.parts):
                relative = Path(*[part for part in relative.parts if part not in {"", "."}])
            if not relative.parts or str(relative) in {".", ""}:
                raise UnsafePatchError("补丁路径无效")
            target = (self.root / relative).resolve()
            if not self._inside(self.root, target):
                raise UnsafePatchError(f"补丁路径越界：{raw_path}")
            # Never follow a symlink supplied by an installation tree.  Check
            # every component, not just the final file: a symlinked parent
            # could redirect a relative payload outside the intended tree.
            if self._has_symlink_component(relative):
                raise UnsafePatchError(f"拒绝写入符号链接：{raw_path}")
            content = item.get("content", item.get("data"))
            if content is None and "content_base64" in item:
                content = item.get("content_base64")
                encoding = "base64"
            else:
                encoding = str(item.get("encoding") or "utf-8").lower()
            if isinstance(content, str):
                try:
                    data = base64.b64decode(content, validate=True) if encoding == "base64" else content.encode("utf-8")
                except (ValueError, UnicodeError) as exc:
                    raise UnsafePatchError(f"补丁内容编码无效：{raw_path}") from exc
            elif isinstance(content, (bytes, bytearray)):
                data = bytes(content)
            else:
                raise UnsafePatchError(f"补丁文件缺少内容：{raw_path}")
            expected = str(item.get("sha256") or item.get("hash") or "").lower().strip()
            if not expected:
                raise UnsafePatchError(f"补丁文件缺少 SHA-256 校验值：{raw_path}")
            if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
                raise UnsafePatchError(f"补丁校验值无效：{raw_path}")
            digest = _sha256(data)
            if expected != digest:
                raise UnsafePatchError(f"补丁校验失败：{raw_path}")
            mode = item.get("mode")
            mode_value: int | None = None
            if mode is not None:
                try:
                    mode_value = int(str(mode), 8) if isinstance(mode, str) else int(mode)
                except (TypeError, ValueError) as exc:
                    raise UnsafePatchError(f"补丁权限无效：{raw_path}") from exc
                if mode_value < 0 or mode_value > 0o777:
                    raise UnsafePatchError(f"补丁权限无效：{raw_path}")
            result.append({"path": str(relative), "target": target, "data": data, "sha256": digest, "mode": mode_value})
        if not result:
            raise UnsafePatchError("Linux 补丁没有文件")
        return result

    def _find(self, name_or_patch: str | Mapping[str, Any] | HotfixDefinition) -> HotfixDefinition:
        if isinstance(name_or_patch, HotfixDefinition):
            return name_or_patch
        if isinstance(name_or_patch, Mapping):
            return self._definition(name_or_patch)
        name = str(name_or_patch or "").strip()
        for item in self._definitions():
            if item.name == name:
                return item
        raise HotfixError(f"未找到补丁：{name}")

    def list(self, *, include_unavailable: bool = True) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for definition in self._definitions():
            available, reason = self._availability(definition)
            manifests = self._manifest_paths(definition)
            applied = False
            stale_reason = ""
            if manifests:
                applied, stale_reason = self._manifest_active(manifests[0])
            if not include_unavailable and not available:
                continue
            rows.append({
                "name": definition.name,
                "id": definition.name,
                "title": definition.name,
                "description": definition.description,
                "version": definition.version,
                "versions": definition.version,
                "available": available,
                "supported": available,
                # ``enabled`` is the catalog's opt-in switch.  ``applied`` is
                # the on-disk state; keeping both avoids confusing a listed
                # patch with one that has actually modified the tree.
                "enabled": definition.enabled and not (
                    isinstance(getattr(self.config, "hotfix_states", {}), Mapping)
                    and definition.name in getattr(self.config, "hotfix_states", {})
                    and not _as_bool(getattr(self.config, "hotfix_states", {}).get(definition.name), True)
                ),
                "catalog_enabled": definition.enabled,
                "user_enabled": not (
                    isinstance(getattr(self.config, "hotfix_states", {}), Mapping)
                    and definition.name in getattr(self.config, "hotfix_states", {})
                    and not _as_bool(getattr(self.config, "hotfix_states", {}).get(definition.name), True)
                ),
                "applied": applied,
                "active": applied,
                "visible": definition.visible,
                "reason": reason or stale_reason,
                "unavailable_reason": reason or stale_reason,
                "manifest": str(manifests[0]) if manifests else "",
                "stale": bool(manifests) and not applied,
            })
        return rows

    def status(
        self,
        name_or_patch: str | Mapping[str, Any] | HotfixDefinition | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any] | None:
        """Return the inventory, or one normalized row when a name is given.

        The UI uses the inventory form, while a few integrations ask for the
        state of one patch after applying/reverting it.  Supporting both keeps
        the service's original zero-argument contract and avoids making those
        callers duplicate the name lookup logic.
        """
        rows = self.list()
        if name_or_patch is None:
            return rows
        if isinstance(name_or_patch, HotfixDefinition):
            name = name_or_patch.name
        elif isinstance(name_or_patch, Mapping):
            name = str(name_or_patch.get("name") or name_or_patch.get("id") or "")
        else:
            name = str(name_or_patch)
        return next((row for row in rows if row.get("name") == name), None)

    def set_enabled(self, name_or_patch: str | Mapping[str, Any] | HotfixDefinition, enabled: bool) -> bool:
        """Persist a per-patch user opt-in state when an AppConfig is supplied."""
        if self.config is None:
            raise HotfixError("没有可保存的配置对象")
        definition = self._find(name_or_patch)
        states = getattr(self.config, "hotfix_states", None)
        if not isinstance(states, dict):
            states = {}
            self.config.hotfix_states = states
        states[definition.name] = bool(enabled)
        return bool(enabled)

    def _backup_manifest(self, definition: HotfixDefinition) -> tuple[Path, list[dict[str, Any]]]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        patch_dir = (self.backup_root / _safe_id(definition.name)).resolve()
        if not self._inside(self.backup_root, patch_dir):
            raise UnsafePatchError("补丁备份目录无效")
        patch_dir.mkdir(parents=True, exist_ok=True)
        manifest = patch_dir / f"manifest-{stamp}.json"
        records: list[dict[str, Any]] = []
        return manifest, records

    def apply(self, name_or_patch: str | Mapping[str, Any] | HotfixDefinition) -> HotfixResult:
        definition = self._find(name_or_patch)
        with self._lock:
            available, reason = self._availability(definition)
            if not available:
                raise HotfixError(reason or "补丁不可用")
            existing = self._manifest_paths(definition)
            if existing:
                active, detail = self._manifest_active(existing[0])
                if active:
                    return HotfixResult(True, definition.name, f"补丁已应用：{definition.name}", str(existing[0]))
                raise HotfixError(detail or "补丁目标已被修改，请先处理现有备份")
            files = self._payload_files(definition)
            manifest_path, records = self._backup_manifest(definition)
            temp_files: list[Path] = []
            try:
                for item in files:
                    target: Path = item["target"]
                    if target.exists() and not target.is_file():
                        raise UnsafePatchError(f"目标不是普通文件：{item['path']}")
                    backup_path = manifest_path.parent / "files" / item["path"]
                    if target.exists():
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup_path, follow_symlinks=False)
                    record = {
                        "path": item["path"],
                        "backup": str(backup_path.relative_to(manifest_path.parent)) if target.exists() else "",
                        "existed": target.exists(),
                        "original_sha256": _sha256(target.read_bytes()) if target.exists() else "",
                        "mode": (target.stat().st_mode & 0o777) if target.exists() else None,
                        "new_sha256": item["sha256"],
                    }
                    records.append(record)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".aura-tmp", dir=str(target.parent))
                    temp_path = Path(temp_name)
                    temp_files.append(temp_path)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(item["data"])
                        stream.flush()
                        os.fsync(stream.fileno())
                    if item["mode"] is not None:
                        os.chmod(temp_path, item["mode"])
                    temp_path.replace(target)
                    temp_files.remove(temp_path)
                manifest_payload = {
                    "name": definition.name,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "root": str(self.root),
                    "revision": self._current_revision(),
                    "files": records,
                }
                self._atomic_json(manifest_path, manifest_payload)
                return HotfixResult(True, definition.name, f"已应用补丁：{definition.name}", str(manifest_path), [r["path"] for r in records])
            except Exception:
                for temp in temp_files:
                    temp.unlink(missing_ok=True)
                self._rollback_records(manifest_path.parent, records, protect=False)
                # Empty failed patch directories are harmless but avoid leaving
                # misleading manifests around.
                try:
                    if not any(manifest_path.parent.iterdir()):
                        manifest_path.parent.rmdir()
                except OSError:
                    pass
                raise

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            Path(temp_name).replace(path)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def _rollback_records(
        self,
        patch_dir: Path,
        records: list[dict[str, Any]],
        *,
        protect: bool = True,
    ) -> list[str]:
        """Restore records and return paths that could not be safely changed.

        A user may edit a file after a patch was applied.  Reverting must not
        silently delete/overwrite that edit, so the protected path first
        verifies each target against the hash written by ``apply``.  Internal
        rollback after a failed atomic apply passes ``protect=False`` because
        those writes belong entirely to the failed operation.
        """
        conflicts: list[str] = []
        if protect:
            for record in records:
                if not isinstance(record, Mapping):
                    conflicts.append("<无效记录>")
                    continue
                raw_path = record.get("path")
                if not isinstance(raw_path, str) or not raw_path:
                    conflicts.append("<无效路径>")
                    continue
                target = (self.root / raw_path).resolve()
                if not self._inside(self.root, target) or self._has_symlink_component(raw_path):
                    conflicts.append(raw_path)
                    continue
                expected = str(record.get("new_sha256") or "").lower()
                if not expected:
                    conflicts.append(raw_path)
                    continue
                if not target.is_file() or _sha256(target.read_bytes()) != expected:
                    conflicts.append(raw_path)
            if conflicts:
                return conflicts
        errors: list[str] = []
        for record in reversed(records):
            if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
                errors.append("<无效记录>")
                continue
            target = (self.root / record["path"]).resolve()
            if (
                not self._inside(self.root, target)
                or self._has_symlink_component(record["path"])
            ):
                errors.append(str(record.get("path", "")))
                continue
            backup = (patch_dir / record.get("backup", "")).resolve() if record.get("backup") else None
            try:
                if record.get("existed") and backup and self._inside(patch_dir, backup) and backup.is_file():
                    original_digest = str(record.get("original_sha256") or "").lower()
                    if original_digest and _sha256(backup.read_bytes()) != original_digest:
                        errors.append(str(record.get("path", "")))
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target, follow_symlinks=False)
                elif not record.get("existed"):
                    target.unlink(missing_ok=True)
                elif record.get("existed"):
                    errors.append(str(record.get("path", "")))
            except OSError:
                errors.append(str(record.get("path", "")))
        return errors

    def revert(self, name_or_patch: str | Mapping[str, Any] | HotfixDefinition) -> HotfixResult:
        definition = self._find(name_or_patch)
        with self._lock:
            manifests = self._manifest_paths(definition)
            if not manifests:
                return HotfixResult(True, definition.name, f"补丁未应用：{definition.name}")
            manifest_path = manifests[0]
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                records = payload.get("files", [])
                if not isinstance(records, list):
                    raise HotfixError("补丁清单损坏")
                valid_records = [record for record in records if isinstance(record, Mapping)]
                problems = self._rollback_records(manifest_path.parent, valid_records)
                if problems:
                    raise HotfixError("目标文件已被修改或无法恢复：" + "、".join(problems))
                manifest_path.unlink()
                return HotfixResult(True, definition.name, f"已撤销补丁：{definition.name}", files=[str(r.get("path", "")) for r in valid_records])
            except Exception as exc:
                raise HotfixError(f"撤销补丁失败：{exc}") from exc


def list_hotfixes(root: str | Path, **kwargs: Any) -> list[dict[str, Any]]:
    return HotfixManager(root, **kwargs).list()


__all__ = [
    "HotfixDefinition",
    "HotfixError",
    "HotfixManager",
    "HotfixResult",
    "UnsafePatchError",
    "list_hotfixes",
]
