from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


class DirtyRepositoryError(GitError):
    pass


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

    def is_dirty(self) -> bool:
        self.ensure_repo()
        return bool(_run_git(self.repo_path, ["status", "--porcelain"]))

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
        if self.is_dirty() and not allow_dirty:
            raise DirtyRepositoryError("检测到未提交的本地修改，已阻止切换版本")
        _run_git(self.repo_path, ["checkout", revision], timeout=120)

    def pull_fast_forward(self) -> None:
        self.ensure_repo()
        if self.is_dirty():
            raise DirtyRepositoryError("检测到未提交的本地修改，已阻止更新")
        _run_git(self.repo_path, ["pull", "--ff-only"], timeout=180)

