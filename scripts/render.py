#!/usr/bin/env python3
"""Render report.json into a reviewer-friendly report.md (stdlib only).

Usage: render.py <report.json>   # writes markdown to stdout
"""

import json
import sys

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
VERDICT_LABEL = {"passed": "✅ PASSED", "failed": "❌ FAILED", "infra": "⚠️ INFRA FAILURE"}


def finding_sort_key(item):
    finding = item[1]
    return (SEVERITY_ORDER.get(finding.get("severity"), 3), finding.get("title", ""))


def render(report):
    out = []
    summary = report["summary"]
    out.append(f"# {report['feature_id']} — {VERDICT_LABEL[summary['verdict']]}")
    out.append("")
    out.append(
        f"Run `{report['run_id']}` · squad `{report['squad']['name']}` · "
        f"{report['started_at']} → {report['finished_at']}"
    )
    out.append("")
    out.append(
        f"**{summary['devices_total']} device(s)** · "
        f"smoke {summary['smoke_passed']} passed / {summary['smoke_failed']} failed · "
        f"**{summary['bugs']} bug(s)** · {summary['blocked']} blocked · "
        f"{summary['questions']} question(s)"
    )

    bugs = []
    others = []
    for dev in report["devices"]:
        label = f"{dev.get('model') or dev['platform']} ({dev['id']})"
        for finding in dev.get("exploration", {}).get("findings", []):
            bucket = bugs if finding.get("type") == "bug" else others
            bucket.append((label, finding))

    if bugs:
        out.append("")
        out.append("## Bugs")
        for label, f in sorted(bugs, key=finding_sort_key):
            out.append("")
            out.append(f"### [{f.get('severity', '?')}] {f['title']}")
            out.append("")
            out.append(f"*Device: {label}*")
            if f.get("expected") or f.get("actual"):
                out.append("")
                out.append(f"- **Expected:** {f.get('expected', '—')}")
                out.append(f"- **Actual:** {f.get('actual', '—')}")
            if f.get("repro_steps"):
                out.append("")
                out.append("Repro:")
                for i, step in enumerate(f["repro_steps"], 1):
                    out.append(f"{i}. {step}")
            for shot in f.get("evidence", []):
                out.append("")
                out.append(f"![evidence]({shot})")

    out.append("")
    out.append("## Devices")
    for dev in report["devices"]:
        out.append("")
        out.append(
            f"### {dev.get('model') or dev['platform']} — {dev.get('os_version', '')} "
            f"(`{dev['id']}`)"
        )
        out.append("")
        out.append(
            f"Worker: {dev.get('worker_status', '?')} · smoke: {dev['smoke']['status']} · "
            f"exploration: {dev.get('exploration', {}).get('status', '?')}"
        )
        reason = dev.get("exploration", {}).get("blocked_reason")
        if reason:
            out.append(f"  — {reason}")
        checks = dev["smoke"].get("checks", [])
        if checks:
            out.append("")
            for chk in checks:
                mark = {"pass": "✓", "fail": "✗", "skip": "→"}[chk["verdict"]]
                line = f"- {mark} {chk['goal']}"
                if chk.get("observation"):
                    line += f" — {chk['observation']}"
                out.append(line)

    if others:
        out.append("")
        out.append("## Questions, blockers, notes")
        for label, f in others:
            out.append("")
            out.append(f"- **{f['type']}** · {f['title']} *({label})*")
            for shot in f.get("evidence", []):
                out.append(f"  - evidence: {shot}")

    out.append("")
    out.append("## Artifacts")
    out.append("")
    out.append("Per-device `log.md`, `result.json`, and `screenshots/` sit next to")
    out.append("this report in the run directory.")
    out.append("")
    return "\n".join(out)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: render.py <report.json>")
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"render.py: cannot read report: {exc}")
    print(render(report))


if __name__ == "__main__":
    main()
