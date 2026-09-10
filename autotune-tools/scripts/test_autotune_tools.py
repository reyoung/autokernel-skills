"""Integration checks using temporary local Git repositories only."""

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
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
COMPLETE = MODULE["complete_attempt"]
GET_CONTEXT = MODULE["get_context"]

DEFAULT_BASELINES = {"latency": 20.0, "throughput": 50.0}
DEFAULT_COMPLETE_METRICS = {"latency": 12.0, "throughput": 56.0}


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
        self.meta_path = self.install_baseline_meta(self.repo)
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

    def install_baseline_meta(self, repo, benchmark_metrics=None):
        benchmark_metrics = benchmark_metrics or {
            "latency": {"unit": "ms", "direction": "minimize"},
            "throughput": {"unit": "1/s", "direction": "maximize"},
        }
        script = repo / "eval" / "evaluate.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("print('ok')\n", encoding="utf-8")
        checksum = hashlib.sha256(script.read_bytes()).hexdigest()
        meta = {
            "schema_version": 1,
            "protected_files": [{"path": "eval/evaluate.py", "checksum": checksum}],
            "evaluation": {
                "verify": {
                    "script": "eval/evaluate.py",
                    "command": ["python3", "eval/evaluate.py", "--mode", "verify"],
                },
                "benchmark": {
                    "script": "eval/evaluate.py",
                    "command": ["python3", "eval/evaluate.py", "--mode", "benchmark"],
                },
            },
            "metrics": {
                "verify": {"max_abs_error": {"unit": "1", "direction": "minimize"}},
                "benchmark": benchmark_metrics,
            },
        }
        path = repo / ".autokernel" / "baseline_meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        self.git("-C", repo, "add", ".")
        self.git("-C", repo, "commit", "-m", "baseline meta")
        return path

    def init(self, refs=(), user_prompt="优化 kernel 的性能，保持正确性。",
             baselines=None, max_attempts=None, baseline_meta=None):
        baselines = DEFAULT_BASELINES if baselines is None else baselines
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            INIT(self.repo, self.workspace, refs, user_prompt,
                 Path(baseline_meta or self.meta_path), baselines, max_attempts)

    def init_cli_args(self, workspace=None, baselines=None, max_attempts=None, meta=None):
        baselines = DEFAULT_BASELINES if baselines is None else baselines
        args = [str(CLI), "init-workspace", str(self.repo), str(workspace or self.workspace),
                "--baseline-meta", str(meta or self.meta_path)]
        for name, value in baselines.items():
            args.extend(["--metric", f"{name}={value}"])
        if max_attempts is not None:
            args.extend(["--max-attempts", str(max_attempts)])
        return args

    def worktrees(self):
        return self.git("-C", self.repo, "worktree", "list", "--porcelain")



class WorkspaceTests(RepositoryTestCase):
    def test_initialization_keeps_dirty_source_unchanged(self):
        (self.repo / "content").write_text("dirty")
        (self.repo / "untracked").write_text("untracked")
        before = self.git("-C", self.repo, "status", "--porcelain")
        self.init()
        self.assertEqual((self.workspace / "best/content").read_text(), "source")
        self.assertFalse((self.workspace / "best/untracked").exists())
        self.assertTrue((self.workspace / "best").is_dir())
        self.assertFalse((self.workspace / "best").is_symlink())
        self.assertEqual(self.git("-C", self.workspace / "best", "rev-parse", "HEAD"),
                         self.git("-C", self.repo, "rev-parse", "HEAD"))
        self.assertEqual((self.workspace / "baseline").read_text(),
                         self.git("-C", self.repo, "rev-parse", "HEAD") + "\n")
        self.assertEqual(before, self.git("-C", self.repo, "status", "--porcelain"))
        self.assertTrue((self.workspace / "attempts").is_dir())
        self.assertTrue((self.workspace / "reference").is_dir())
        self.assertTrue((self.workspace / "baseline_meta.json").is_file())
        target = json.loads((self.workspace / "target-metric.json").read_text(encoding="utf-8"))
        self.assertEqual(target["metrics"]["latency"]["baseline"], 20.0)
        self.assertEqual(target["metrics"]["throughput"]["direction"], "maximize")
        self.assertNotIn("max_attempts", target)
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM Attempts").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM Metrics").fetchone(), (0,))
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("INSERT INTO Attempts (Plan, Status, BaseCommitSHA) VALUES ('plan', 'running', ?)",
                       (self.git("-C", self.repo, "rev-parse", "HEAD"),))
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
        result = subprocess.run([str(CLI), "init-workspace", "--help"],
                                capture_output=True, text=True)
        self.assertIn("baseline-meta", result.stdout)
        self.assertIn("max-attempts", result.stdout)
        result = subprocess.run(
            [str(CLI), "init-workspace", str(self.root / "missing"), str(self.workspace),
             "--baseline-meta", str(self.meta_path), "--metric", "latency=1"],
            input="test", capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.workspace.exists())

    def test_initialization_saves_user_prompt_verbatim(self):
        prompt = "  优化 CUDA kernel\r\n约束：保持正确性，目标加速 2 倍。\n\n" * 10000
        result = subprocess.run(
            self.init_cli_args(),
            input=prompt.encode("utf-8"), capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.workspace / "user_prompt.md").read_bytes(), prompt.encode("utf-8"))

    def test_user_prompt_is_required_and_nonblank(self):
        for prompt in ("", " \t\n"):
            with self.subTest(prompt=prompt):
                result = subprocess.run(
                    self.init_cli_args(),
                    input=prompt, capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("stdin", result.stderr)
                self.assertFalse(self.workspace.exists())
        with self.assertRaises(ValueError):
            self.init(user_prompt="")

    def test_init_requires_meta_metrics_and_checksums(self):
        with self.assertRaises(ValueError):
            self.init(baselines={})
        with self.assertRaisesRegex(ValueError, "不在 baseline meta"):
            self.init(baselines={"missing": 1.0})
        bad_meta = self.root / "bad-meta.json"
        bad_meta.write_text(self.meta_path.read_text(encoding="utf-8").replace(
            json.loads(self.meta_path.read_text(encoding="utf-8"))["protected_files"][0]["checksum"],
            "0" * 64,
        ), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.init(baseline_meta=bad_meta)
        result = subprocess.run(
            self.init_cli_args(max_attempts=3, baselines={"latency": 9.5}),
            input="prompt", capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        target = json.loads((self.workspace / "target-metric.json").read_text(encoding="utf-8"))
        self.assertEqual(set(target["metrics"]), {"latency"})
        self.assertEqual(target["max_attempts"], 3)
        self.assertEqual(target["metrics"]["latency"]["baseline"], 9.5)


class InitializedWorkspaceTestCase(RepositoryTestCase):
    def setUp(self):
        super().setUp()
        self.init()

    def rows(self):
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            return db.execute("SELECT ID, Plan, Status, CreatedAt, UpdatedAt FROM Attempts ORDER BY ID").fetchall()

    def new(self, plan="优化计划\n"):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            NEW(self.workspace, plan)

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


class AttemptTests(InitializedWorkspaceTestCase):
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
        self.assertTrue((self.workspace / "best").is_dir())
        self.assertFalse((self.workspace / "best").is_symlink())
        (repo / "content").write_text("better")
        self.git("-C", repo, "commit", "-am", "better")
        with redirect_stdout(io.StringIO()):
            COMPLETE(self.workspace, 1, DEFAULT_COMPLETE_METRICS, "summary", "details")
        self.new("next")
        next_repo = self.workspace / "attempts/attempt-00002/repo"
        self.assertEqual((next_repo / "content").read_text(), "better")
        self.assertEqual(self.git("-C", next_repo, "rev-parse", "HEAD"),
                         self.git("-C", self.workspace / "best", "rev-parse", "HEAD"))
        detached = subprocess.run(["git", "-C", str(next_repo), "symbolic-ref", "HEAD"],
                                  capture_output=True)
        self.assertNotEqual(detached.returncode, 0)

    def test_dirty_best_rejected(self):
        best = self.workspace / "best"
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
        best = self.workspace / "best"
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
            process = self.start_waiting_cli(
                self.init_cli_args(workspace=other)[1:],
                plan=b"original prompt",
            )
            self.assertIsNone(process.poll())
            self.assertEqual([p.name for p in other.iterdir()], [".autotune.lock"])
        _, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertTrue((other / "autotune.db").is_file())
        self.assertEqual((other / "user_prompt.md").read_text(), "original prompt")

    def test_max_attempts_blocks_new_attempt(self):
        other = self.root / "limited-workspace"
        self.workspace = other
        self.init(max_attempts=1)
        self.new("only")
        with self.assertRaisesRegex(ValueError, "max_attempts"):
            self.new("blocked")


class CompletionTests(InitializedWorkspaceTestCase):
    def candidate(self, attempt_id=1):
        self.new(f"plan {attempt_id}")
        repo = self.workspace / "attempts" / f"attempt-{attempt_id:05d}" / "repo"
        (repo / "content").write_text(f"optimized {attempt_id}")
        self.git("-C", repo, "commit", "-am", "first change")
        (repo / "extra").write_text(f"new file {attempt_id}")
        self.git("-C", repo, "add", "extra")
        self.git("-C", repo, "commit", "-m", "second change")
        return repo

    def improving_metrics(self, attempt_id=1):
        # Each successive completed Attempt must beat the previous best.
        return {"latency": 12.0 - attempt_id, "throughput": 56.0 + attempt_id}

    def complete(self, attempt_id=1, metrics=None, summary="summary", details="details",
                 fail=False, allow_regression=False):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            COMPLETE(self.workspace, attempt_id,
                     self.improving_metrics(attempt_id) if metrics is None else metrics,
                     summary, details, fail=fail, allow_regression=allow_regression)

    def complete_args(self, attempt_id=1, metrics=None, allow_regression=False, fail=False):
        metrics = self.improving_metrics(attempt_id) if metrics is None else metrics
        args = ["complete-attempt", str(self.workspace), str(attempt_id),
                "--metric", json.dumps(metrics), "--summary", "summary"]
        if allow_regression:
            args.append("--allow-regression")
        if fail:
            args.append("--fail")
        return args

    def completion_row(self, attempt_id=1):
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            return db.execute(
                "SELECT Status, BaseCommitSHA, SquashCommitSHA FROM Attempts WHERE ID = ?",
                (attempt_id,),
            ).fetchone()

    def test_squash_creates_one_commit_and_records_sha(self):
        repo = self.candidate()
        best = self.workspace / "best"
        old = self.git("-C", best, "rev-parse", "HEAD")
        head = self.git("-C", repo, "rev-parse", "HEAD")
        inode = best.stat().st_ino
        result = subprocess.run([str(CLI), *self.complete_args()],
                                input="details", capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        squash = self.git("-C", best, "rev-parse", "HEAD")
        self.assertIn(squash, result.stdout)
        self.assertNotEqual(squash, head)
        self.assertEqual(self.git("-C", best, "rev-list", "--parents", "-n", "1", squash),
                         f"{squash} {old}")
        self.assertEqual(self.git("-C", best, "rev-parse", "HEAD^{tree}"),
                         self.git("-C", repo, "rev-parse", "HEAD^{tree}"))
        self.assertEqual(self.git("-C", best, "status", "--porcelain"), "")
        self.assertEqual(self.completion_row(), ("completed", old, squash))
        self.assertEqual(self.git("-C", repo, "rev-parse", "HEAD"), head)
        self.assertEqual((self.workspace / "baseline").read_text(), old + "\n")
        self.assertEqual(self.git("-C", self.repo, "rev-parse", "HEAD"), old)
        self.assertEqual(best.stat().st_ino, inode)
        # A second accepted Attempt extends the same best worktree by one commit.
        self.candidate(2)
        self.complete(2)
        next_sha = self.completion_row(2)[2]
        self.assertEqual(self.git("-C", best, "rev-parse", "HEAD^"), squash)
        self.assertEqual(self.git("-C", best, "rev-parse", "HEAD"), next_sha)
        self.assertEqual((self.workspace / "baseline").read_text(), old + "\n")

    def test_stale_attempt_and_repeated_completion_rejected(self):
        self.candidate()
        self.candidate(2)
        self.complete()
        best = self.git("-C", self.workspace / "best", "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "best 已改变"):
            self.complete(2)
        with self.assertRaisesRegex(ValueError, "completed"):
            self.complete(1)
        self.assertEqual(self.completion_row(2)[0], "running")
        self.assertIsNone(self.completion_row(2)[2])
        self.assertEqual(self.git("-C", self.workspace / "best", "rev-parse", "HEAD"), best)

    def test_dirty_attempt_and_best_rejected(self):
        repo = self.candidate()
        original = self.completion_row()
        for target in (repo, self.workspace / "best"):
            for kind in ("unstaged", "staged", "untracked"):
                with self.subTest(target=target, kind=kind):
                    file = target / ("untracked" if kind == "untracked" else "content")
                    file.write_text("dirty")
                    if kind == "staged":
                        self.git("-C", target, "add", "content")
                    with self.assertRaisesRegex(ValueError, "未提交"):
                        self.complete()
                    self.assertEqual(self.completion_row(), original)
                    self.assertEqual(file.read_text(), "dirty")
                    if kind == "untracked":
                        file.unlink()
                    else:
                        self.git("-C", target, "restore", "--source=HEAD", "--staged", "--worktree", "content")

    def test_git_and_database_failures_restore_best(self):
        repo = self.candidate()
        best = self.workspace / "best"
        previous = self.git("-C", best, "rev-parse", "HEAD")
        candidate_head = self.git("-C", repo, "rev-parse", "HEAD")
        original_git = COMPLETE.__globals__["git"]
        original_connect = sqlite3.connect

        class BadCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("database commit failure")

        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=BadCommit)

        def fail_after(operation):
            def run(*args):
                result = original_git(*args)
                if args[2] == operation:
                    raise RuntimeError(f"failure after {operation}")
                return result
            return run

        def fail_commit(*args):
            if args[2] == "commit":
                raise RuntimeError("git commit failed")
            return original_git(*args)

        for failure in (patch.dict(COMPLETE.__globals__, {"git": fail_commit}),
                        patch.dict(COMPLETE.__globals__, {"git": fail_after("merge")}),
                        patch.dict(COMPLETE.__globals__, {"git": fail_after("commit")}),
                        patch.object(sqlite3, "connect", connect)):
            with failure, self.assertRaises((RuntimeError, sqlite3.Error)):
                self.complete()
            self.assertEqual(self.completion_row(), ("running", previous, None))
            self.assertEqual(self.git("-C", best, "rev-parse", "HEAD"), previous)
            self.assertEqual(self.git("-C", best, "status", "--porcelain"), "")
            self.assertEqual(self.git("-C", repo, "rev-parse", "HEAD"), candidate_head)
            self.assertEqual((self.workspace / "baseline").read_text(), previous + "\n")
        self.complete()

    def test_no_change_missing_and_failed_attempts_rejected(self):
        self.new()
        with self.assertRaisesRegex(ValueError, "没有可合入"):
            self.complete()
        for attempt_id in (0, -1, 99):
            with self.assertRaises(ValueError):
                self.complete(attempt_id)
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            db.execute("UPDATE Attempts SET Status='failed' WHERE ID=1")
        with self.assertRaisesRegex(ValueError, "failed"):
            self.complete()

    def test_complete_requires_squash_sha_in_database(self):
        self.new()
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE Attempts SET Status='completed' WHERE ID=1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE Attempts SET SquashCommitSHA='unexpected' WHERE ID=1")

    def test_completion_and_creation_share_lock(self):
        self.candidate()
        original = self.completion_row()
        with LOCK(self.workspace):
            process = self.start_waiting_cli(self.complete_args())
            self.assertIsNone(process.poll())
            self.assertEqual(self.completion_row(), original)
            self.assertEqual(self.git("-C", self.workspace / "best", "rev-parse", "HEAD"), original[1])
            self.assertFalse((self.workspace / "attempts/attempt-00001/result.json").exists())
            self.assertFalse((self.workspace / "attempts/attempt-00001/result.md").exists())
            with sqlite3.connect(self.workspace / "autotune.db") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM Metrics").fetchone(), (0,))
                self.assertEqual(db.execute("SELECT Summary, Details FROM Attempts WHERE ID=1").fetchone(),
                                 (None, None))
        _, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.new("after squash")
        self.assertEqual(self.completion_row(2)[1], self.completion_row(1)[2])

    def test_old_layout_rejected_without_migration(self):
        baseline = self.workspace / "baseline"
        baseline.unlink()
        self.git("-C", self.repo, "worktree", "move", self.workspace / "best", baseline)
        (self.workspace / "best").symlink_to("baseline")
        before = self.worktrees()
        for call in (self.new, self.complete):
            with self.assertRaisesRegex(ValueError, "旧版 workspace"):
                call()
        self.assertTrue(baseline.is_dir())
        self.assertEqual(os.readlink(self.workspace / "best"), "baseline")
        self.assertEqual(self.worktrees(), before)

    def test_concurrent_completions_accept_only_one_base(self):
        self.candidate()
        self.candidate(2)
        with LOCK(self.workspace):
            processes = [self.start_waiting_cli(self.complete_args(i))
                         for i in (1, 2)]
            self.assertTrue(all(p.poll() is None for p in processes))
            self.assertEqual([self.completion_row(i)[0] for i in (1, 2)], ["running", "running"])
        for process in processes:
            process.communicate(timeout=10)
        self.assertCountEqual([p.returncode for p in processes], [0, 1])
        self.assertCountEqual([self.completion_row(i)[0] for i in (1, 2)], ["running", "completed"])
        winner = next(self.completion_row(i)[2] for i in (1, 2) if self.completion_row(i)[0] == "completed")
        self.assertEqual(self.git("-C", self.workspace / "best", "rev-parse", "HEAD"), winner)

    def test_old_database_rejected_without_migration(self):
        path = self.workspace / "autotune.db"
        path.unlink()
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE Attempts (ID INTEGER PRIMARY KEY, Plan TEXT, Status TEXT)")
        before = path.read_bytes()
        for call in (self.new, self.complete):
            with self.assertRaisesRegex(ValueError, "旧版或无效"):
                call()
        self.assertEqual(path.read_bytes(), before)

    def test_result_inputs_are_saved_to_database_and_files(self):
        self.candidate()
        self.new("another candidate")
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            db.execute("INSERT INTO Metrics (AttemptID, Name, Value) VALUES (1, 'old', 99)")
            db.execute("INSERT INTO Metrics (AttemptID, Name, Value) VALUES (2, 'keep', 100)")
        metrics = {"latency": 11.0, "throughput": 60.0}
        summary = "优化延迟，吞吐量提高"
        details = "第一行\r\n\n## 验证\n正确性通过。\n\n"
        result = subprocess.run(
            [str(CLI), "complete-attempt", str(self.workspace), "1",
             "--metric", json.dumps(metrics), "--summary", summary],
            input=details.encode("utf-8"), capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        attempt = self.workspace / "attempts/attempt-00001"
        saved = json.loads((attempt / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, {"status": "completed", "metrics": metrics, "summary": summary, "details": details,
                                 "squash_commit_sha": self.completion_row()[2]})
        self.assertEqual((attempt / "result.md").read_bytes(),
                         f"# Summary\n\n{summary}\n\n# Details\n\n{details}".encode("utf-8"))
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            self.assertEqual(db.execute("SELECT Summary, Details FROM Attempts WHERE ID=1").fetchone(),
                             (summary, details))
            self.assertEqual(dict(db.execute("SELECT Name, Value FROM Metrics WHERE AttemptID=1")), metrics)
            self.assertEqual(db.execute("SELECT Name, Value FROM Metrics WHERE AttemptID=2").fetchall(),
                             [("keep", 100)])

    def test_invalid_metric_and_summary_rejected_before_modification(self):
        self.candidate()
        old = self.completion_row()
        invalid_metrics = ['{}', '[]', 'null', '{', '{"x": true}', '{"x": "12"}',
                           '{"x": NaN}', '{"x": Infinity}', '{"x": 1e999}',
                           '{" ": 12}', '{"x": 1, "x": 2}']
        invalid_options = [["--metric", value, "--summary", "summary"] for value in invalid_metrics]
        invalid_options += [["--metric", '{"x": 12}', "--summary", value]
                            for value in ("", " \n\t")]
        invalid_options += [["--metric", '{"x": 12}'], ["--summary", "summary"]]
        for options in invalid_options:
            with self.subTest(options=options):
                result = subprocess.run([str(CLI), "complete-attempt", str(self.workspace), "1", *options],
                                        input=b"detail", capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.completion_row(), old)
                self.assertEqual(self.git("-C", self.workspace / "best", "rev-parse", "HEAD"), old[1])
                self.assertFalse((self.workspace / "attempts/attempt-00001/result.json").exists())
        for summary in ("", " \n\t"):
            with self.assertRaisesRegex(ValueError, "--summary"):
                self.complete(summary=summary)

    def test_result_files_and_database_restored_after_failure(self):
        self.candidate()
        attempt = self.workspace / "attempts/attempt-00001"
        before = self.completion_row()
        original_replace = COMPLETE.__globals__["replace_file"]
        original_connect = sqlite3.connect

        class BadCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("database commit failure")

        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=BadCommit)

        def fail_after_write(name):
            failed = False

            def replace(path, content, mode=0o644):
                nonlocal failed
                original_replace(path, content, mode)
                if path.name == name and not failed:
                    failed = True
                    raise OSError("file write failure")
            return replace

        for existing in (False, True):
            with sqlite3.connect(self.workspace / "autotune.db") as db:
                db.execute("UPDATE Attempts SET Summary='old summary', Details='old detail' WHERE ID=1")
                db.execute("DELETE FROM Metrics WHERE AttemptID=1")
                db.execute("INSERT INTO Metrics (AttemptID, Name, Value) VALUES (1, 'old metric', 5)")
            old_files = {"result.json": b'{"old": true}\n', "result.md": b"old report\r\n"}
            if existing:
                for name, content in old_files.items():
                    (attempt / name).write_bytes(content)
                    (attempt / name).chmod(0o640)
            for failure in (
                patch.dict(COMPLETE.__globals__, {"replace_file": fail_after_write("result.json")}),
                patch.dict(COMPLETE.__globals__, {"replace_file": fail_after_write("result.md")}),
                patch.object(sqlite3, "connect", connect),
            ):
                with self.subTest(existing=existing), failure, self.assertRaises((OSError, sqlite3.Error)):
                    self.complete()
                self.assertEqual(self.completion_row(), before)
                self.assertEqual(self.git("-C", self.workspace / "best", "rev-parse", "HEAD"), before[1])
                self.assertEqual(self.git("-C", self.workspace / "best", "status", "--porcelain"), "")
                for name, content in old_files.items():
                    if existing:
                        self.assertEqual((attempt / name).read_bytes(), content)
                        self.assertEqual((attempt / name).stat().st_mode & 0o777, 0o640)
                    else:
                        self.assertFalse((attempt / name).exists())
                self.assertEqual(list(attempt.glob(".result.*")), [])
                with sqlite3.connect(self.workspace / "autotune.db") as db:
                    self.assertEqual(db.execute("SELECT Summary, Details FROM Attempts WHERE ID=1").fetchone(),
                                     ("old summary", "old detail"))
                    self.assertEqual(db.execute("SELECT Name, Value FROM Metrics WHERE AttemptID=1").fetchall(),
                                     [("old metric", 5)])
        self.complete()  # Rollback released the transaction and lock; a retry succeeds.

    def test_result_symlink_is_not_followed(self):
        self.candidate()
        target = self.root / "outside-result.json"
        target.write_text("keep")
        (self.workspace / "attempts/attempt-00001/result.json").symlink_to(target)
        before = self.completion_row()
        with self.assertRaisesRegex(ValueError, "普通文件"):
            self.complete()
        self.assertEqual(target.read_text(), "keep")
        self.assertEqual(self.completion_row(), before)

    def test_fail_records_results_with_stale_base_and_dirty_worktrees(self):
        repo = self.candidate()
        self.candidate(2)
        self.complete(2)
        best = self.workspace / "best"
        best_head = self.git("-C", best, "rev-parse", "HEAD")
        base = self.completion_row()[1]
        self.assertNotEqual(best_head, base)
        for target in (repo, best):
            (target / "content").write_text("dirty content")
            (target / "untracked").write_text("untracked content")
        before = {target: self.git("-C", target, "status", "--porcelain") for target in (repo, best)}
        result = subprocess.run([str(CLI), *self.complete_args(fail=True)],
                                input="失败详情\n", text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("failed", result.stdout)
        self.assertEqual(self.completion_row(), ("failed", base, None))
        self.assertEqual(self.git("-C", best, "rev-parse", "HEAD"), best_head)
        for target, status in before.items():
            self.assertEqual(self.git("-C", target, "status", "--porcelain"), status)
            self.assertEqual((target / "content").read_text(), "dirty content")
            self.assertEqual((target / "untracked").read_text(), "untracked content")
        attempt = self.workspace / "attempts/attempt-00001"
        saved = json.loads((attempt / "result.json").read_text())
        self.assertEqual(saved, {"status": "failed", "metrics": self.improving_metrics(1),
                                 "summary": "summary", "details": "失败详情\n", "squash_commit_sha": None})
        self.assertIn("失败详情\n", (attempt / "result.md").read_text())
        with sqlite3.connect(self.workspace / "autotune.db") as db:
            self.assertEqual(dict(db.execute("SELECT Name, Value FROM Metrics WHERE AttemptID=1")), saved["metrics"])
            self.assertEqual(db.execute("SELECT Summary, Details FROM Attempts WHERE ID=1").fetchone(),
                             ("summary", "失败详情\n"))

    def test_fail_without_candidate_changes_waits_for_lock(self):
        self.new()
        before = self.completion_row()
        with LOCK(self.workspace):
            process = self.start_waiting_cli([*self.complete_args(), "--fail"])
            self.assertIsNone(process.poll())
            self.assertEqual(self.completion_row(), before)
            self.assertFalse((self.workspace / "attempts/attempt-00001/result.json").exists())
        _, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(self.completion_row(), ("failed", before[1], None))
        self.assertEqual(self.git("-C", self.workspace / "best", "rev-parse", "HEAD"), before[1])

    def test_failed_result_write_rolls_back_without_touching_best(self):
        self.new()
        best = self.workspace / "best"
        (best / "content").write_text("pending edits")
        original = self.completion_row()
        original_replace = COMPLETE.__globals__["replace_file"]
        original_connect = sqlite3.connect

        def write_then_fail(path, content, mode=0o644):
            original_replace(path, content, mode)
            if path.name == "result.md":
                raise OSError("failed to write report")

        class BadCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("database commit failure")

        def connect(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=BadCommit)

        for failure in (patch.dict(COMPLETE.__globals__, {"replace_file": write_then_fail}),
                        patch.object(sqlite3, "connect", connect)):
            with failure, self.assertRaises((OSError, sqlite3.Error)):
                self.complete(fail=True)
            self.assertEqual(self.completion_row(), original)
            self.assertEqual((best / "content").read_text(), "pending edits")
            for name in ("result.json", "result.md"):
                self.assertFalse((self.workspace / "attempts/attempt-00001" / name).exists())
            with sqlite3.connect(self.workspace / "autotune.db") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM Metrics").fetchone(), (0,))
                self.assertEqual(db.execute("SELECT Summary, Details FROM Attempts WHERE ID=1").fetchone(),
                                 (None, None))
        self.complete(fail=True)

    def test_pareto_and_required_metrics(self):
        self.candidate()
        with self.assertRaisesRegex(ValueError, "恰好覆盖"):
            self.complete(metrics={"latency": 10.0})
        with self.assertRaisesRegex(ValueError, "提升"):
            self.complete(metrics={"latency": 20.0, "throughput": 50.0})
        # Single mild regression (<=1%) is ok when another metric improves.
        self.complete(metrics={"latency": 20.1, "throughput": 60.0})
        self.candidate(2)
        # >1% regression requires --allow-regression.
        with self.assertRaisesRegex(ValueError, "allow-regression"):
            self.complete(2, metrics={"latency": 25.0, "throughput": 70.0})
        self.complete(2, metrics={"latency": 25.0, "throughput": 70.0}, allow_regression=True)

    def test_get_context_markdown(self):
        (self.workspace / "reference" / "README.md").write_text(
            "# Refs\n\nFlashAttention for tiling.\n", encoding="utf-8",
        )
        empty = io.StringIO()
        with redirect_stdout(empty):
            GET_CONTEXT(self.workspace)
        text = empty.getvalue()
        self.assertIn("# Autotune Context", text)
        self.assertIn("## Workspace", text)
        self.assertIn(f"- path: `{self.workspace.resolve()}`", text)
        self.assertIn("目录结构大约为：", text)
        self.assertIn("baseline_meta.json", text)
        self.assertIn("## Best 相对初始 baseline", text)
        self.assertIn("尚无合入 Attempt", text)
        self.assertIn("## User Prompt", text)
        self.assertIn("优化 kernel 的性能", text)
        self.assertIn("## Recent Attempts", text)
        self.assertIn("（尚无 Attempt）", text)
        self.assertIn("## Reference README", text)
        self.assertIn("FlashAttention for tiling.", text)
        self.candidate()
        self.complete()
        self.candidate(2)
        self.complete(2, fail=True)
        result = subprocess.run(
            [str(CLI), "get-context", str(self.workspace)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("attempt-00002", result.stdout)
        self.assertIn("attempt-00001", result.stdout)
        self.assertIn("(completed)", result.stdout)
        self.assertIn("(failed)", result.stdout)
        self.assertIn("相对提升", result.stdout)
        self.assertIn(str(self.workspace / "attempts/attempt-00001"), result.stdout)
        # stdout must be markdown only: no CLI status chatter mixed in.
        self.assertTrue(result.stdout.startswith("# Autotune Context\n"))


if __name__ == "__main__":
    unittest.main()
