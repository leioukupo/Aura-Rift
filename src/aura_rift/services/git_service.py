from __future__ import annotations

import subprocess
import re
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


@dataclass(frozen=True)
class GitChange:
    path: str
    status: str
    tracked: bool = True

    @property
    def label(self) -> str:
        labels = {
            "??": "未跟踪",
            "A": "新增",
            "M": "修改",
            "D": "删除",
            "R": "重命名",
            "C": "复制",
            "U": "冲突",
        }
        keys = [c for c in self.status if c.strip()]
        if not keys:
            return "已修改"
        return "/".join(labels.get(k, k) for k in keys)


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


def git_command_args(args: list[str], github_proxy: str = "") -> list[str]:
    """Build a git command, optionally using a temporary GitHub mirror rewrite."""
    command = ["git", "-c", "color.ui=always"]
    proxy = github_proxy.strip()
    if proxy:
        base = proxy.rstrip("/") + "/https://github.com/"
        command.extend([
            "-c",
            f"url.{base}.insteadOf=https://github.com/",
            "-c",
            f"url.{base}.insteadOf=git@github.com:",
        ])
    command.extend(args)
    return command


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

    def current_commit_date(self) -> str:
        self.ensure_repo()
        if not self._has_head():
            return ""
        date = _run_git(self.repo_path, ["log", "-1", "--date=iso-strict", "--pretty=format:%cd"])
        return date.replace("T", " ").split("+")[0]

    def changes(self, include_custom_nodes: bool = False) -> list[GitChange]:
        """Return repository-relative paths with uncommitted changes.

        By default custom_nodes/ is excluded, because user-installed plugins
        live there and their modifications should never block ComfyUI version
        switches. Pass include_custom_nodes=True to include them.
        """
        self.ensure_repo()
        if not self._has_head():
            return []
        proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"],
            cwd=str(self.repo_path),
            text=False,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            message = proc.stderr.decode(errors="replace").strip() or "git status failed"
            raise GitError(message)
        changes: list[GitChange] = []
        for entry in proc.stdout.split(b"\0"):
            if not entry:
                continue
            if len(entry) < 4:
                continue
            status = entry[:2].decode("ascii", errors="replace")
            path = entry[3:].decode("utf-8", errors="surrogateescape")
            if not include_custom_nodes and (path == "custom_nodes" or path.startswith("custom_nodes/")):
                continue
            changes.append(GitChange(path=path, status=status, tracked=status != "??"))
        return changes

    def dirty_files(self, include_custom_nodes: bool = False) -> list[str]:
        return [change.path for change in self.changes(include_custom_nodes=include_custom_nodes)]

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

    def checkout_commands(self, revision: str, require_clean: bool = True) -> list[list[str]]:
        self.ensure_repo()
        if require_clean:
            err = self._dirty_error("切换版本")
            if err is not None:
                raise err
        return [["checkout", revision]]

    def _default_branch(self) -> str | None:
        """Best-effort local name of the repository's default branch.

        Used to recover from a detached HEAD: prefer the remote's published
        HEAD (origin/HEAD), then fall back to the usual master/main names.
        """
        try:
            ref = _run_git(
                self.repo_path,
                ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            )
        except GitError:
            ref = ""
        if ref:
            name = ref.removeprefix("origin/")
            if name:
                return name
        for name in ("master", "main"):
            for ref_spec in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
                try:
                    _run_git(self.repo_path, ["rev-parse", "--verify", "--quiet", ref_spec])
                    return name
                except GitError:
                    pass
        return None

    def _remote_ref_for_branch(self, branch: str) -> str | None:
        """Best-effort remote ref for a local branch.

        Prefer the configured upstream, then the common origin/<branch> layout.
        """
        try:
            upstream = _run_git(
                self.repo_path,
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{upstream}}"],
            )
        except GitError:
            upstream = ""
        if upstream:
            return upstream
        try:
            _run_git(
                self.repo_path,
                ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
            )
            return f"origin/{branch}"
        except GitError:
            return None

    def _backup_branch_name(self, branch: str) -> str:
        short = (self.current_commit() or "head")[:12]
        safe_branch = re.sub(r"[^A-Za-z0-9._/-]+", "-", branch).strip("./")
        safe_branch = re.sub(r"/+", "/", safe_branch).replace("..", ".")
        if not safe_branch:
            safe_branch = "detached"
        return f"aura-rift-backup/{safe_branch}-{short}"

    def fast_forward_commands(self, require_clean: bool = True) -> list[list[str]]:
        """Return git argument lists that update the repo to its remote tip.

        A plain ``git pull --ff-only`` fails after upstream rewrites history,
        which is common for some ComfyUI custom nodes. For branches with a
        known remote, make a lightweight backup ref to the current commit, then
        reset to the fetched remote tip. This handles normal fast-forwards and
        forced updates with one path while still preserving the old commit.

        Detached HEAD repos are moved back onto their default branch first.
        """
        self.ensure_repo()
        if require_clean:
            err = self._dirty_error("更新")
            if err is not None:
                raise err
        if not self._has_head():
            return [["pull", "--ff-only"]]

        branch = self.current_branch()
        if branch:
            remote_ref = self._remote_ref_for_branch(branch)
            if remote_ref:
                return [
                    ["fetch", "--all", "--tags", "--prune"],
                    ["branch", "-f", self._backup_branch_name(branch), "HEAD"],
                    ["reset", "--hard", remote_ref],
                ]
            return [["pull", "--ff-only"]]

        default = self._default_branch()
        if default is None:
            raise GitError(
                "当前不在任何分支上，且无法确定默认分支。\n"
                "请先在“版本”页选择分支并点击“切换分支”，然后再更新。"
            )
        remote_ref = self._remote_ref_for_branch(default)
        if remote_ref:
            return [
                ["fetch", "--all", "--tags", "--prune"],
                ["branch", "-f", self._backup_branch_name(default), "HEAD"],
                ["checkout", "-B", default, remote_ref],
            ]
        return [
            ["fetch", "--all", "--tags", "--prune"],
            ["checkout", default],
            ["pull", "--ff-only"],
        ]

    def pull_fast_forward(self) -> None:
        for args in self.fast_forward_commands():
            _run_git(self.repo_path, args, timeout=180)

    @staticmethod
    def stash_commands(changes: list[GitChange], message: str) -> list[list[str]]:
        if not changes:
            return []
        return [["stash", "push", "-u", "-m", message, "--", *(change.path for change in changes)]]

    @staticmethod
    def stash_pop_commands(changes: list[GitChange]) -> list[list[str]]:
        if not changes:
            return []
        return [["stash", "pop"]]

    @staticmethod
    def discard_commands(changes: list[GitChange]) -> list[list[str]]:
        if not changes:
            return []
        tracked = [change.path for change in changes if change.tracked]
        untracked = [change.path for change in changes if not change.tracked]
        commands: list[list[str]] = []
        if tracked:
            commands.append(["restore", "--staged", "--worktree", "--", *tracked])
        if untracked:
            commands.append(["clean", "-fd", "--", *untracked])
        return commands
