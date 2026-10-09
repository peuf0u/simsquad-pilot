#!/usr/bin/env python3
"""Run one worker command under a hard timebox, enforced deterministically.

Usage:
  run_worker.py --run-dir <worker dir> --timeout-seconds N -- <command> [args…]

Why this exists: the timebox is the orchestrator's one timing-critical rule,
and an LLM following a prose instruction cannot reliably fire a wall-clock
kill mid-step (a dry-run worker overran its budget by ~40% unkilled). This
script moves enforcement into deterministic code. Any fan-out model whose
worker is a subprocess (pi, `claude -p`, a CI runner) wraps the worker in
this; it is the harness-agnostic half of "timebox is a kill, not a request".

Behaviour, by how the worker ends:
  - timeout      → kill the whole process group, write
                   `<run-dir>/status` = "blocked: timeout"
  - non-zero exit→ write "error: worker exited <code>"
  - clean exit 0 → write nothing; the worker is expected to have produced
                   result.json, which the orchestrator validates next

The worker is started in its own session (setpgid) so a timeout kills any
idb/simctl/emulator children it spawned, not just the top process. This
script never raises on a worker failure — it always exits 0 after recording
the outcome, so a fan-out loop is not derailed by one bad worker. It exits
non-zero only on its own misuse (bad arguments).
"""

import argparse
import os
import signal
import subprocess
import sys
import time

TERM_GRACE_SECONDS = 5


def write_status(run_dir, text):
    try:
        with open(os.path.join(run_dir, "status"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError as exc:
        print(f"run_worker.py: could not write status: {exc}", file=sys.stderr)


def kill_group(proc):
    """SIGTERM the worker's process group, then SIGKILL anything that lingers."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=TERM_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--timeout-seconds", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("no worker command given after --")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")

    os.makedirs(args.run_dir, exist_ok=True)

    started = time.monotonic()
    proc = subprocess.Popen(command, start_new_session=True)
    try:
        code = proc.wait(timeout=args.timeout_seconds)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        elapsed = int(time.monotonic() - started)
        write_status(args.run_dir, "blocked: timeout")
        print(
            f"run_worker.py: worker killed after {elapsed}s "
            f"(timebox {args.timeout_seconds}s)",
            file=sys.stderr,
        )
        return 0

    if code != 0:
        write_status(args.run_dir, f"error: worker exited {code}")
        print(f"run_worker.py: worker exited {code}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
