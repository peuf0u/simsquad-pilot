#!/usr/bin/env python3
"""Tests for scripts/run_worker.py timebox enforcement.

Run: python3 tests/test_run_worker.py
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_WORKER = os.path.join(ROOT, "scripts", "run_worker.py")


def run_worker(run_dir, timeout_seconds, worker_argv):
    return subprocess.run(
        [sys.executable, RUN_WORKER, "--run-dir", run_dir,
         "--timeout-seconds", str(timeout_seconds), "--", *worker_argv],
        capture_output=True, text=True,
    )


def status_of(run_dir):
    path = os.path.join(run_dir, "status")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


class RunWorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def worker_script(self, body):
        path = os.path.join(self.tmp.name, "worker.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(body))
        return [sys.executable, path]

    def test_clean_exit_writes_no_status(self):
        worker = self.worker_script("import sys; sys.exit(0)")
        proc = run_worker(self.run_dir, 10, worker)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(status_of(self.run_dir))

    def test_nonzero_exit_records_error(self):
        worker = self.worker_script("import sys; sys.exit(3)")
        proc = run_worker(self.run_dir, 10, worker)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(status_of(self.run_dir), "error: worker exited 3")

    def test_timeout_kills_and_marks_blocked(self):
        # Worker sleeps far past the 1s timebox; it must be killed and the
        # whole call must return well before the worker's own 30s sleep.
        worker = self.worker_script("import time; time.sleep(30)")
        start = time.monotonic()
        proc = run_worker(self.run_dir, 1, worker)
        elapsed = time.monotonic() - start
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(status_of(self.run_dir), "blocked: timeout")
        self.assertLess(elapsed, 15, "worker was not killed promptly")

    def test_timeout_kills_child_processes(self):
        # Worker spawns a long-lived grandchild then sleeps. After the kill,
        # the grandchild's pid must be gone — proves the whole group dies.
        pid_file = os.path.join(self.tmp.name, "child.pid")
        worker = self.worker_script(f"""
            import subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            open({pid_file!r}, "w").write(str(child.pid))
            time.sleep(30)
        """)
        proc = run_worker(self.run_dir, 1, worker)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Give the kill a moment to propagate.
        time.sleep(1)
        with open(pid_file, encoding="utf-8") as f:
            child_pid = int(f.read().strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)  # raises if the pid no longer exists

    def test_missing_command_is_usage_error(self):
        proc = subprocess.run(
            [sys.executable, RUN_WORKER, "--run-dir", self.run_dir,
             "--timeout-seconds", "5", "--"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
