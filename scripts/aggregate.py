#!/usr/bin/env python3
"""Merge per-device result.json files into report.json (stdlib only).

Usage: aggregate.py <run-dir>   # writes report JSON to stdout

Expects in <run-dir>:
  run.json                 — written by the orchestrator after `devices`:
                             {run_id, feature_id, squad: {name}, started_at,
                              devices: [DeviceView rows]}
  <platform>-<id>/         — one worker dir per device, containing
    result.json            — the worker's output (validated upstream)
    status                 — optional orchestrator override, e.g.
                             "blocked: timeout" or "error: invalid result"

Devices in run.json with no usable result.json are reported with
worker_status error (or blocked, per the status file) and empty payloads.
All artifact paths are rewritten relative to <run-dir>.
"""

import json
import os
import sys
from datetime import datetime, timezone

EMPTY_SMOKE = {"status": "skipped", "checks": []}


def device_dir_name(row):
    return f"{row['platform']}-{row['id']}"


def rewrite_paths(payload, prefix):
    """Prefix worker-relative artifact paths so they resolve from the run dir."""
    for chk in payload.get("smoke", {}).get("checks", []):
        if chk.get("screenshot"):
            chk["screenshot"] = f"{prefix}/{chk['screenshot']}"
    exploration = payload.get("exploration", {})
    for action in exploration.get("actions", []):
        if action.get("screenshot"):
            action["screenshot"] = f"{prefix}/{action['screenshot']}"
    for finding in exploration.get("findings", []):
        if finding.get("evidence"):
            finding["evidence"] = [f"{prefix}/{p}" for p in finding["evidence"]]


def load_device(run_dir, row):
    dirname = device_dir_name(row)
    dev_dir = os.path.join(run_dir, dirname)

    override = None
    status_file = os.path.join(dev_dir, "status")
    if os.path.exists(status_file):
        with open(status_file, encoding="utf-8") as f:
            override = f.read().strip()

    entry = {
        "platform": row["platform"],
        "id": row["id"],
        "name": row.get("name", ""),
        "model": row.get("model", ""),
        "os_version": row.get("os_version", ""),
        "bundle_id": row.get("bundle_id", ""),
    }

    result_path = os.path.join(dev_dir, "result.json")
    result = None
    if os.path.exists(result_path):
        try:
            with open(result_path, encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, json.JSONDecodeError):
            result = None

    if result is not None and not override:
        entry["worker_status"] = "ok"
        rewrite_paths(result, dirname)
        entry["smoke"] = result.get("smoke", dict(EMPTY_SMOKE))
        entry["exploration"] = result.get("exploration", {})
    else:
        kind = (override or "error").split(":", 1)[0].strip()
        reason = override or "missing or unreadable result.json"
        entry["worker_status"] = kind if kind in ("blocked", "error") else "error"
        # Keep partial smoke/exploration if the worker wrote something before
        # being killed; otherwise synthesize empty, schema-valid payloads.
        if result is not None:
            rewrite_paths(result, dirname)
        smoke = (result or {}).get("smoke") or dict(EMPTY_SMOKE)
        exploration = (result or {}).get("exploration") or {}
        exploration.setdefault("actions", [])
        exploration.setdefault("findings", [])
        exploration["status"] = "blocked" if entry["worker_status"] == "blocked" else "error"
        exploration["blocked_reason"] = reason
        entry["smoke"] = smoke
        entry["exploration"] = exploration
    return entry


def summarize(devices):
    def count_findings(ftype):
        return sum(
            1
            for d in devices
            for f in d.get("exploration", {}).get("findings", [])
            if f.get("type") == ftype
        )

    summary = {
        "devices_total": len(devices),
        "smoke_passed": sum(1 for d in devices if d["smoke"]["status"] == "passed"),
        "smoke_failed": sum(1 for d in devices if d["smoke"]["status"] == "failed"),
        "bugs": count_findings("bug"),
        "blocked": count_findings("blocked"),
        "questions": count_findings("question"),
    }
    ok = [d for d in devices if d["worker_status"] == "ok"]
    if not devices or not ok:
        verdict = "infra"
    elif summary["bugs"] or summary["smoke_failed"] or len(ok) < len(devices):
        verdict = "failed"
    else:
        verdict = "passed"
    summary["verdict"] = verdict
    return summary


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: aggregate.py <run-dir>")
    run_dir = sys.argv[1].rstrip("/")

    try:
        with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as f:
            run = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"aggregate.py: cannot read run.json: {exc}")

    devices = [load_device(run_dir, row) for row in run.get("devices", [])]

    report = {
        "run_id": run["run_id"],
        "feature_id": run["feature_id"],
        "started_at": run["started_at"],
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "squad": {"name": run["squad"]["name"]},
        "summary": summarize(devices),
        "devices": devices,
    }
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
