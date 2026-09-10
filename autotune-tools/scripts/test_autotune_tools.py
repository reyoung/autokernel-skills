"""Integration checks using temporary local Git repositories only."""

from contextlib import redirect_stdout, redirect_stderr
import io
import fcntl
import signal
import sys
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch


CLI = Path(__file__).with_name("autotune-tools")
MODULE = runpy.run_path(str(CLI))
INIT = MODULE["init_workspace"]
DOWNLOAD = MODULE["download_references"]
NEW = MODULE["new_attempt"]
LOCK = MODULE["workspace_lock"]


class RepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
            # Local submodule transports are allowed only inside these tests.
            "GIT_ALLOW_PROTOCOL": "file",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.repo = self.make_repo("source")
        self.workspace = self.root / "workspace with spaces"

    def git(self, *args):
        return subprocess.check_output(["git", *map(str, args)], text=True,
                                       stderr=subprocess.PIPE).strip()

    def make_repo(self, name):
        repo = self.root / name
        self.git("init", "-b", "main", repo)
        (repo / "content").write_text(name)
        self.git("-C", repo, "add", ".")
        self.git("-C", repo, "commit", "-m", "initial")
        return repo

    def init(self, refs=()):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            INIT(self.repo, self.workspace, refs)

    def worktrees(self):
        return self.git("-C", self.repo, "worktree", "list", "--porcelain")



class WorkspaceTests(RepositoryTestCase):
    def test_initialization_keeps_dirty_source_unchanged(self):
        (self.repo / "content").write_text("dirty")
        (self.repo / "untracked").write_text("untracked")
        before = self.git("-C", self.repo, "status", "--porcelain")
        self.init()
        self.assertEqual((self.workspace / "baseline/content").read_text(), "source")
        self.assertFalse((self.workspace / "baseline/untracked").exists())
        self.assertEqual(os.readlink(self.workspace / "best"), "baseline")
        self.assertEqual(self.git("-C", self.workspace / "baseline", "rev-parse", "HEAD"),
                         self.git("-C", self.repo, "rev-parse", "HEAD"))
        self.assertEqual(before, self.git("-C", self.repo, "status", "--porcelain"))
        self.assertTrue((self.workspace / "attempts").is_dir())
        self.assertTrue((self.workspace / "reference").is_dir())
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM Attempts").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM Metrics").fetchone(), (0,))
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("INSERT INTO Attempts (Plan, Status) VALUES ('plan', 'running')")
            db.execute("INSERT INTO Metrics (AttemptID, Name, Value) VALUES (1, 'latency', 1.5)")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO Metrics (AttemptID) VALUES (99)")

    def test_references_versions_and_nested_submodules(self):
        leaf = self.make_repo("leaf")
        middle = self.make_repo("middle")
        self.git("-C", middle, "submodule", "add", leaf, "nested")
        self.git("-C", middle, "commit", "-am", "nested submodule")
        reference = self.make_repo("reference")
        initial = self.git("-C", reference, "rev-parse", "HEAD")
        self.git("-C", reference, "tag", "v1")
        self.git("-C", reference, "checkout", "-b", "feature")
        self.git("-C", reference, "submodule", "add", middle, "modules/middle")
        self.git("-C", reference, "commit", "-am", "add submodule")
        feature = self.git("-C", reference, "rev-parse", "HEAD")
        self.git("-C", reference, "checkout", "main")
        self.init([(str(reference), "feature", "branch"),
                   (str(reference), "v1", "tag"),
                   (str(reference), initial, "sha")])
        for name, expected in (("branch", feature), ("tag", initial), ("sha", initial)):
            dest = self.workspace / "reference" / name
            self.assertEqual(self.git("-C", dest, "rev-parse", "HEAD"), expected)
        nested = self.workspace / "reference/branch/modules/middle/nested/content"
        self.assertEqual(nested.read_text(), "leaf")

    def test_downloads_overlap(self):
        barrier = threading.Barrier(3)
        seen = []

        def clone(ref, root):
            barrier.wait(timeout=5)
            seen.append(ref[2])

        with patch.dict(DOWNLOAD.__globals__, {"clone_reference": clone}):
            DOWNLOAD([("url", "rev", str(i)) for i in range(3)], self.root)
        self.assertCountEqual(seen, ["0", "1", "2"])

    def test_reject_nonempty_and_invalid_names_before_mutation(self):
        self.workspace.mkdir()
        sentinel = self.workspace / "keep"
        sentinel.write_text("original")
        before = self.worktrees()
        with self.assertRaises(ValueError):
            self.init()
        self.assertEqual(sentinel.read_text(), "original")
        sentinel.unlink()
        for names in (("../escape",), ("/absolute",), (".",), ("same", "same")):
            with self.subTest(names=names), self.assertRaises(ValueError):
                self.init([(str(self.repo), "HEAD", name) for name in names])
            self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertEqual(self.worktrees(), before)

    def test_failed_download_rolls_back_and_preserves_existing_empty_directory(self):
        for existing in (False, True):
            for revision in ("missing-version", "HEAD"):
                with self.subTest(existing=existing, revision=revision):
                    if existing:
                        self.workspace.mkdir(exist_ok=True)
                    before = self.worktrees()
                    url = self.repo if revision != "HEAD" else self.root / "missing-repo"
                    with self.assertRaises(RuntimeError):
                        self.init([(str(url), revision, "bad"),
                                   (str(self.repo), "HEAD", "good")])
                    self.assertEqual(self.worktrees(), before)
                    self.assertEqual([p.name for p in self.workspace.iterdir()], [".autotune.lock"])

    def test_invalid_repo(self):
        self.repo = self.root / "missing"
        with self.assertRaises(ValueError):
            self.init()
        self.assertFalse(self.workspace.exists())

    def test_failure_after_worktree_registration_rolls_back(self):
        original_git = INIT.__globals__["git"]
        before = self.worktrees()

        def fail_after_add(*args):
            result = original_git(*args)
            if "worktree" in args and "add" in args:
                raise RuntimeError("failure after registration")
            return result

        with patch.dict(INIT.__globals__, {"git": fail_after_add}):
            with self.assertRaises(RuntimeError):
                self.init()
        self.assertEqual(self.worktrees(), before)
        self.assertEqual([p.name for p in self.workspace.iterdir()], [".autotune.lock"])
        self.init()  # A failed initialization can be retried with the same lock inode.

    def test_cli_help_and_error_exit(self):
        for args in ([], ["--help"], ["init-workspace", "--help"]):
            result = subprocess.run([str(CLI), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("init-workspace", result.stdout)
        result = subprocess.run([str(CLI), "init-workspace", str(self.root / "missing"),
                                 str(self.workspace)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.workspace.exists())


class AttemptTests(RepositoryTestCase):
    def setUp(self):
        super().setUp()
        self.init()

    def rows(self):
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            return db.execute("SELECT ID, Plan, Status, CreatedAt, UpdatedAt FROM Attempts ORDER BY ID").fetchall()

    def new(self, plan="优化计划\n"):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            NEW(self.workspace, plan)

    def test_plan_and_derivation_from_current_best(self):
        plan = "# 优化\r\n第一步\n第二步\n\n"
        result = subprocess.run([str(CLI), "new-attempt", str(self.workspace)],
                                input=plan.encode(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        attempt = self.workspace / "attempts/attempt-00001"
        self.assertIn(str(attempt).encode(), result.stdout)
        self.assertEqual((attempt / "plan.md").read_bytes(), plan.encode())
        row = self.rows()[0]
        self.assertEqual(row[:3], (1, plan, "running"))
        self.assertTrue(row[3] and row[4])
        repo = attempt / "repo"
        self.assertEqual(self.git("-C", repo, "rev-parse", "HEAD"),
                         self.git("-C", self.workspace / "best", "rev-parse", "HEAD"))
        self.assertEqual(os.readlink(self.workspace / "best"), "baseline")
        (repo / "content").write_text("better")
        self.git("-C", repo, "commit", "-am", "better")
        with LOCK(self.workspace):
            (self.workspace / "best").unlink()
            (self.workspace / "best").symlink_to("attempts/attempt-00001/repo")
        self.new("next")
        next_repo = self.workspace / "attempts/attempt-00002/repo"
        self.assertEqual((next_repo / "content").read_text(), "better")
        self.assertEqual(self.git("-C", next_repo, "rev-parse", "HEAD"),
                         self.git("-C", repo, "rev-parse", "HEAD"))
        detached = subprocess.run(["git", "-C", str(next_repo), "symbolic-ref", "HEAD"],
                                  capture_output=True)
        self.assertNotEqual(detached.returncode, 0)

    def test_dirty_best_rejected(self):
        best = self.workspace / "baseline"
        before = self.worktrees()
        for kind in ("unstaged", "staged", "untracked"):
            with self.subTest(kind=kind):
                file = best / ("untracked" if kind == "untracked" else "content")
                file.write_text("change")
                if kind == "staged":
                    self.git("-C", best, "add", "content")
                with self.assertRaisesRegex(ValueError, "未提交"):
                    self.new()
                self.assertEqual(self.rows(), [])
                self.assertEqual(list((self.workspace / "attempts").iterdir()), [])
                if kind == "untracked":
                    file.unlink()
                else:
                    self.git("-C", best, "restore", "--source=HEAD", "--staged", "--worktree", "content")
        self.assertEqual(self.worktrees(), before)

    def test_ignored_files_are_allowed(self):
        best = self.workspace / "baseline"
        (best / ".gitignore").write_text("cache\n")
        self.git("-C", best, "add", ".gitignore")
        self.git("-C", best, "commit", "-m", "ignore cache")
        (best / "cache").write_text("ignored")
        self.new()
        self.assertEqual(len(self.rows()), 1)

    def test_empty_plan_and_invalid_workspace(self):
        for plan in ("", " \n\t"):
            with self.assertRaises(ValueError):
                self.new(plan)
        missing = self.root / "missing"
        with self.assertRaises(ValueError):
            NEW(missing, "plan")
        self.assertFalse(missing.exists())
        (self.workspace / "autotune.db").unlink()
        with self.assertRaises(ValueError):
            self.new()
        self.assertFalse((self.workspace / "autotune.db").exists())

    def test_existing_attempt_directory_is_preserved(self):
        attempt = self.workspace / "attempts/attempt-00001"
        attempt.mkdir()
        (attempt / "keep").write_text("original")
        with self.assertRaises(FileExistsError):
            self.new()
        self.assertEqual((attempt / "keep").read_text(), "original")
        self.assertEqual(self.rows(), [])

    def test_worktree_and_commit_failures_roll_back(self):
        original_git = NEW.__globals__["git"]
        original_connect = sqlite3.connect
        before = self.worktrees()

        def fail_after_add(*args):
            result = original_git(*args)
            if "worktree" in args and "add" in args:
                raise RuntimeError("worktree failure")
            return result

        class BadCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("commit failure")

        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=BadCommit)

        for failure in (patch.dict(NEW.__globals__, {"git": fail_after_add}),
                        patch.object(sqlite3, "connect", connect)):
            with failure, self.assertRaises((RuntimeError, sqlite3.Error)):
                self.new()
            self.assertEqual(self.rows(), [])
            self.assertEqual(list((self.workspace / "attempts").iterdir()), [])
            self.assertEqual(self.worktrees(), before)
        self.new()  # The lock and database transaction were released after failure.

    def start_waiting_cli(self, args, plan=b"plan\n"):
        # Announce immediately before the real flock call: no timing-based guesses.
        driver = r"""
import fcntl, runpy, sys
module = runpy.run_path(sys.argv[1])
original = fcntl.flock
def observed(fd, operation):
    if operation == fcntl.LOCK_EX:
        print('waiting', flush=True)
    return original(fd, operation)
fcntl.flock = observed
sys.argv = sys.argv[1:]
sys.exit(module['main']())
"""
        process = subprocess.Popen([sys.executable, "-c", driver, str(CLI), *args],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        self.addCleanup(self.stop_process, process)
        process.stdin.write(plan)
        process.stdin.close()
        process.stdin = None
        self.assertEqual(process.stdout.readline(), b"waiting\n")
        return process

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def test_processes_wait_and_create_unique_attempts(self):
        inode = (self.workspace / ".autotune.lock").stat().st_ino
        with LOCK(self.workspace):
            processes = [self.start_waiting_cli(["new-attempt", str(self.workspace)],
                                               f"plan {i}".encode()) for i in range(2)]
            self.assertTrue(all(p.poll() is None for p in processes))
            self.assertEqual(self.rows(), [])
            self.assertEqual(list((self.workspace / "attempts").iterdir()), [])
        for process in processes:
            _, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual([r[0] for r in self.rows()], [1, 2])
        self.assertCountEqual([r[1] for r in self.rows()], ["plan 0", "plan 1"])
        self.assertEqual((self.workspace / ".autotune.lock").stat().st_ino, inode)

    def test_cancel_lock_wait(self):
        with LOCK(self.workspace):
            process = self.start_waiting_cli(["new-attempt", str(self.workspace)])
            process.send_signal(signal.SIGINT)
            _, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 130, stderr)
            self.assertEqual(self.rows(), [])
        self.new()

    def test_init_uses_same_lock(self):
        other = self.root / "other-workspace"
        other.mkdir()
        with LOCK(other):
            process = self.start_waiting_cli(["init-workspace", str(self.repo), str(other)])
            self.assertIsNone(process.poll())
            self.assertEqual([p.name for p in other.iterdir()], [".autotune.lock"])
        _, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertTrue((other / "autotune.db").is_file())


if __name__ == "__main__":
    unittest.main()
