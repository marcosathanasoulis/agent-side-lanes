from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest

from side_lane import worktrees


class WorktreeTests(unittest.TestCase):
    def make_repo(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        (repo / "file.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
        return repo

    def test_creates_unique_dedicated_branch_and_worktree(self) -> None:
        repo = self.make_repo()
        (repo / "INPROCESS.md").write_text("# Active work\n", encoding="utf-8")
        lane = worktrees.create_worktree(
            repo, "Parser Task", now=datetime(2026, 8, 29, 12, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(lane.branch, "side-lane/parser-task-20260829123000")
        self.assertTrue((lane.worktree / ".git").exists())
        self.assertNotEqual(lane.worktree, repo)
        claim = (repo / "INPROCESS.md").read_text(encoding="utf-8")
        self.assertIn(lane.branch, claim)

    def test_refuses_dirty_coordinator_checkout(self) -> None:
        repo = self.make_repo()
        (repo / "file.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(worktrees.WorktreeError, "dirty"):
            worktrees.create_worktree(repo, "task")

    def test_cleanup_refuses_dirty_or_unmerged_lane(self) -> None:
        repo = self.make_repo()
        lane = worktrees.create_worktree(
            repo, "task", now=datetime(2026, 8, 29, 12, 31, tzinfo=timezone.utc)
        )
        (lane.worktree / "new.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(worktrees.WorktreeError, "dirty"):
            worktrees.remove_worktree(lane)
        (lane.worktree / "new.txt").unlink()
        with self.assertRaisesRegex(worktrees.WorktreeError, "unmerged"):
            worktrees.remove_worktree(lane)


if __name__ == "__main__":
    unittest.main()
