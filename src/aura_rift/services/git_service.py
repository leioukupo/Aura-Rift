from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


class DirtyRepositoryError(GitError):
    """Raised when a local change would block a version movement.

    `files` optionally carries the offending paths so the UI can list them.
    """

    def __init__(self, message: str, files: list[str] | None = None) -> None:
        super().__init__(message)
        self.files = files or []


@dataclass
class CommitInfo:
    short_hash: str
    full_hash: str
    subject: str
    date: str
    current: bool = False


def _run_git(path: Path, args: list[str], timeout: int = 30) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(path),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "git command failed"
        raise GitError(message)
    return proc.stdout.strip()


class GitService:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    @property
    def exists(self) -> bool:
        return (self.repo_path / ".git").exists()

    def ensure_repo(self) -> None:
        if not self.exists:
            raise GitError("当前目录不是 Git 仓库")

    def _has_head(self) -> bool:
        """True if HEAD points at a resolvable commit (repo has at least one commit)."""
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
            cwd=str(self.repo_path),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        return proc.returncode == 0

    def remote_url(self) -> str:
        self.ensure_repo()
        try:
            return _run_git(self.repo_path, ["config", "--get", "remote.origin.url"])
        except GitError:
            return "未设置"

    def current_branch(self) -> str:
        self.ensure_repo()
        try:
            return _run_git(self.repo_path, ["branch", "--show-current"])
        except GitError:
            return "(detached)"

    def current_commit(self) -> str:
        self.ensure_repo()
        if not self._has_head():
            return ""
        return _run_git(self.repo_path, ["rev-parse", "HEAD"])

    def dirty_files(self, include_custom_nodes: bool = False) -> list[str]:
        """Return repository-relative paths with uncommitted changes.

        By default custom_nodes/ is excluded, because user-installed plugins
        live there and their modifications should never block ComfyUI version
        switches. Pass include_custom_nodes=True to include them.
        """
        self.ensure_repo()
        if not self._has_head():
            return []
        # Use subprocess directly (not _run_git) because git status --porcelain uses a leading space to mean "not staged", and _run_git strips it.
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--no-renames"],
            cwd=str(self.repo_path),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or "git status failed")
        raw = proc.stdout
        files: list[str] = []
        for line in raw.splitlines():
            if not line:
                continue
            path = line[3:]  # drop the two status chars + a space
            # quoted when the path contains whitespace/non-ascii
            if path.startswith('"') and path.endswith('"'):
                from shlex import shlex
                try:
                    tokens = list(shlex(path, posix=True))
                    if tokens:
                        path = tokens[0]
                except ValueError:
                    path = path.strip('"')
            # only the path part before ' -> ' for renames (we passed --no-renames
            # so this should be the plain path, but be defensive)
            path = path.split(" -> ", 1)[-1]
            if not include_custom_nodes and (path == "custom_nodes" or path.startswith("custom_nodes/")):
                continue
            files.append(path)
        return files

    def is_dirty(self, include_custom_nodes: bool = False) -> bool:
        self.ensure_repo()
        return bool(self.dirty_files(include_custom_nodes=include_custom_nodes))

    def _dirty_error(self, action: str) -> DirtyRepositoryError | None:
        """Build a DirtyRepositoryError listing the dirty ComfyUI core files,
        or None when the repo (minus custom_nodes) is clean."""
        files = self.dirty_files()
        if not files:
            return None
        listing = "\n".join("  · " + f for f in files[:30])
        extra = f"\n  …等共 {len(files)} 个" if len(files) > 30 else ""
        return DirtyRepositoryError(
            f"检测到未提交的本地修改，已阻止{action}。\n以下 ComfyUI 本体文件改动未提交:\n" + listing + extra,
            files,
        )

    def assert_clean(self, action: str = "操作") -> None:
        """Raise DirtyRepositoryError if ComfyUI core files have uncommitted changes.

        custom_nodes/ is ignored so plugin modifications never block the
        operation; only ComfyUI's own tracked files are reported.
        """
        self.ensure_repo()
        err = self._dirty_error(action)
        if err is not None:
            raise err

    def branches(self) -> list[str]:
        self.ensure_repo()
        output = _run_git(self.repo_path, ["branch", "--all", "--format=%(refname:short)"])
        names: list[str] = []
        for line in output.splitlines():
            name = line.strip()
            if not name or "HEAD ->" in name:
                continue
            if name.startswith("origin/"):
                name = name.removeprefix("origin/")
            if name not in names:
                names.append(name)
        return names or [self.current_branch()]

    def commits(self, limit: int = 120) -> list[CommitInfo]:
        self.ensure_repo()
        if not self._has_head():
            return []
        current = self.current_commit()
        fmt = "%h%x1f%H%x1f%cd%x1f%s"
        output = _run_git(
            self.repo_path,
            ["log", f"--max-count={limit}", "--date=iso-strict", f"--pretty=format:{fmt}"],
        )
        items: list[CommitInfo] = []
        for line in output.splitlines():
            parts = line.split("\x1f", 3)
            if len(parts) != 4:
                continue
            short_hash, full_hash, date, subject = parts
            items.append(
                CommitInfo(
                    short_hash=short_hash,
                    full_hash=full_hash,
                    date=date.replace("T", " ").split("+")[0],
                    subject=subject,
                    current=full_hash == current,
                )
            )
        return items

    def tags(self, limit: int = 50) -> list[CommitInfo]:
        self.ensure_repo()
        current = self.current_commit() or ""
        fmt = "%(refname:short)|%(objectname)|%(creatordate:iso-strict)|%(contents:subject)"
        output = _run_git(
            self.repo_path,
            ["for-each-ref", "--sort=-creatordate", f"--count={limit}", f"--format={fmt}", "refs/tags"],
        )
        items: list[CommitInfo] = []
        for line in output.splitlines():
            parts = line.split("|", 3)
            if len(parts) != 4:
                continue
            tag_name, full_hash, date, subject = parts
            items.append(
                CommitInfo(
                    short_hash=tag_name,
                    full_hash=full_hash,
                    date=date.replace("T", " ").split("+")[0],
                    subject=subject or tag_name,
                    current=full_hash == current,
                )
            )
        return items

    def fetch(self) -> None:
        self.ensure_repo()
        _run_git(self.repo_path, ["fetch", "--all", "--tags", "--prune"], timeout=180)

    def checkout(self, revision: str, allow_dirty: bool = False) -> None:
        self.ensure_repo()
        if not allow_dirty:
            err = self._dirty_error("切换版本")
            if err is not None:
                raise err
        _run_git(self.repo_path, ["checkout", revision], timeout=120)

    def pull_fast_forward(self) -> None:
        self.ensure_repo()
        err = self._dirty_error("更新")
        if err is not None:
            raise err
        _run_git(self.repo_path, ["pull", "--ff-only"], timeout=180)
