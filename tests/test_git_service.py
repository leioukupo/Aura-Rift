from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from aura_rift.services.git_service import GitChange, GitService, git_command_args


class GitServiceTests(unittest.TestCase):
    def make_repo(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Aura Test"], cwd=repo, check=True)
        return repo

    def git(self, repo: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        )
        return proc.stdout.strip()

    def commit_file(self, repo: Path, name: str, content: str, message: str = "commit") -> None:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git(repo, "add", name)
        self.git(repo, "commit", "-q", "-m", message)

    def test_changes_parse_utf8_and_space_paths(self) -> None:
        repo = self.make_repo()
        self.commit_file(repo, "空 格.txt", "base\n")
        (repo / "空 格.txt").write_text("changed\n", encoding="utf-8")
        (repo / "中文 新.txt").write_text("new\n", encoding="utf-8")

        changes = GitService(repo).changes(include_custom_nodes=True)
        by_path = {change.path: change for change in changes}

        self.assertEqual(by_path["空 格.txt"].status, " M")
        self.assertTrue(by_path["空 格.txt"].tracked)
        self.assertEqual(by_path["中文 新.txt"].status, "??")
        self.assertFalse(by_path["中文 新.txt"].tracked)

    def test_core_changes_ignore_custom_nodes_by_default(self) -> None:
        repo = self.make_repo()
        self.commit_file(repo, "main.py", "base\n")
        (repo / "main.py").write_text("changed\n", encoding="utf-8")
        custom = repo / "custom_nodes" / "Node Pack" / "local.txt"
        custom.parent.mkdir(parents=True)
        custom.write_text("local\n", encoding="utf-8")

        default_paths = GitService(repo).dirty_files()
        all_paths = GitService(repo).dirty_files(include_custom_nodes=True)

        self.assertEqual(default_paths, ["main.py"])
        self.assertIn("custom_nodes/Node Pack/local.txt", all_paths)

    def test_detached_fast_forward_returns_default_branch_sequence(self) -> None:
        repo = self.make_repo()
        self.commit_file(repo, "main.py", "one\n", "one")
        first = self.git(repo, "rev-parse", "HEAD")
        self.commit_file(repo, "main.py", "two\n", "two")
        self.git(repo, "checkout", "-q", first)

        commands = GitService(repo).fast_forward_commands(require_clean=False)

        self.assertEqual(
            commands,
            [
                ["fetch", "--all", "--tags", "--prune", "--force"],
                ["checkout", "master"],
                ["pull", "--ff-only"],
            ],
        )

    def test_branch_update_resets_to_remote_with_backup(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        origin = Path(tmp.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
        repo = self.make_repo()
        self.commit_file(repo, "main.py", "one\n", "one")
        head = self.git(repo, "rev-parse", "--short=12", "HEAD")
        self.git(repo, "remote", "add", "origin", str(origin))
        self.git(repo, "push", "-u", "origin", "master")

        commands = GitService(repo).fast_forward_commands(require_clean=False)

        self.assertEqual(
            commands,
            [
                ["fetch", "--all", "--tags", "--prune", "--force"],
                ["branch", "-f", f"aura-rift-backup/master-{head}", "HEAD"],
                ["reset", "--hard", "origin/master"],
            ],
        )

    def test_stash_and_discard_commands_are_pathsafe(self) -> None:
        changes = [
            GitChange(path="空 格.txt", status=" M", tracked=True),
            GitChange(path="中文 新.txt", status="??", tracked=False),
        ]

        self.assertEqual(
            GitService.stash_commands(changes, "msg"),
            [["stash", "push", "-u", "-m", "msg", "--", "空 格.txt", "中文 新.txt"]],
        )
        self.assertEqual(
            GitService.discard_commands(changes),
            [
                ["restore", "--staged", "--worktree", "--", "空 格.txt"],
                ["clean", "-fd", "--", "中文 新.txt"],
            ],
        )

    def test_github_proxy_rewrite_is_temporary_command_config(self) -> None:
        repo = self.make_repo()
        self.git(repo, "remote", "add", "origin", "https://github.com/user/repo.git")

        command = git_command_args(["pull", "--ff-only"], "https://gh-proxy.example/")

        self.assertEqual(self.git(repo, "config", "--get", "remote.origin.url"), "https://github.com/user/repo.git")
        self.assertEqual(command[:3], ["git", "-c", "color.ui=always"])
        self.assertIn(
            "url.https://gh-proxy.example/https://github.com/.insteadOf=https://github.com/",
            command,
        )
        self.assertIn(
            "url.https://gh-proxy.example/https://github.com/.insteadOf=git@github.com:",
            command,
        )
        self.assertEqual(command[-2:], ["pull", "--ff-only"])


if __name__ == "__main__":
    unittest.main()
