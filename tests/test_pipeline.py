#!/usr/bin/env python3
"""End-to-end pipeline test: aggregate -> validate -> render.

Builds a synthetic run dir with one healthy worker and one timebox-killed
worker, then asserts the report validates and renders.
Run: python3 tests/test_pipeline.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")

RESULT = {
    "device": {
        "platform": "ios",
        "id": "ABC-123",
        "model": "iPhone 17",
        "os_version": "iOS 26.4",
        "bundle_id": "com.example.app",
    },
    "smoke": {
        "status": "passed",
        "checks": [
            {"goal": "App launches", "verdict": "pass", "screenshot": "screenshots/smoke-01.png"}
        ],
    },
    "exploration": {
        "status": "completed",
        "actions": [
            {
                "index": 1,
                "kind": "tap",
                "target": "Přihlásit se",
                "observation": "Login opened",
                "screenshot": "screenshots/step-001.png",
            }
        ],
        "findings": [
            {
                "type": "bug",
                "severity": "high",
                "title": "Magic link stays in browser",
                "evidence": ["screenshots/step-001.png"],
                "repro_steps": ["Launch", "Open login", "Tap link"],
                "expected": "Returns to app",
                "actual": "Browser stays",
            }
        ],
    },
}


def script(name, *args):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, name), *args],
        capture_output=True,
        text=True,
    )


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = os.path.join(self.tmp.name, "run")
        ok_dir = os.path.join(self.run_dir, "ios-ABC-123")
        os.makedirs(os.path.join(ok_dir, "screenshots"))
        os.makedirs(os.path.join(self.run_dir, "ios-DEF-456"))
        for name in ("smoke-01.png", "step-001.png"):
            open(os.path.join(ok_dir, "screenshots", name), "w").close()
        with open(os.path.join(ok_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(RESULT, f)
        with open(os.path.join(self.run_dir, "ios-DEF-456", "status"), "w") as f:
            f.write("blocked: timeout\n")
        with open(os.path.join(self.run_dir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "run_id": "login-magic-link-20260612-170000",
                    "feature_id": "login-magic-link",
                    "squad": {"name": "pilot-login-magic-link-20260612-170000"},
                    "started_at": "2026-06-12T17:00:00Z",
                    "devices": [
                        dict(RESULT["device"], name="simsquad-p-ios-0"),
                        {
                            "platform": "ios",
                            "id": "DEF-456",
                            "name": "simsquad-p-ios-1",
                            "model": "iPhone 17 Pro",
                            "os_version": "iOS 26.4",
                            "bundle_id": "com.example.app",
                        },
                    ],
                },
                f,
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_aggregate_validate_render(self):
        agg = script("aggregate.py", self.run_dir)
        self.assertEqual(agg.returncode, 0, agg.stderr)
        report = json.loads(agg.stdout)

        self.assertEqual(report["summary"]["verdict"], "failed")
        self.assertEqual(report["summary"]["devices_total"], 2)
        self.assertEqual(report["summary"]["bugs"], 1)
        by_id = {d["id"]: d for d in report["devices"]}
        self.assertEqual(by_id["ABC-123"]["worker_status"], "ok")
        self.assertEqual(by_id["DEF-456"]["worker_status"], "blocked")
        self.assertEqual(by_id["DEF-456"]["exploration"]["status"], "blocked")
        # Evidence paths are rewritten relative to the run dir.
        evidence = by_id["ABC-123"]["exploration"]["findings"][0]["evidence"][0]
        self.assertEqual(evidence, "ios-ABC-123/screenshots/step-001.png")

        report_path = os.path.join(self.run_dir, "report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(agg.stdout)
        val = script("validate.py", "report", report_path)
        self.assertEqual(val.returncode, 0, val.stderr)

        ren = script("render.py", report_path)
        self.assertEqual(ren.returncode, 0, ren.stderr)
        for fragment in (
            "❌ FAILED",
            "[high] Magic link stays in browser",
            "blocked: timeout",
            "![evidence](ios-ABC-123/screenshots/step-001.png)",
        ):
            self.assertIn(fragment, ren.stdout)

    def test_all_workers_dead_is_infra(self):
        os.remove(os.path.join(self.run_dir, "ios-ABC-123", "result.json"))
        with open(os.path.join(self.run_dir, "ios-ABC-123", "status"), "w") as f:
            f.write("error: invalid result\n")
        agg = script("aggregate.py", self.run_dir)
        self.assertEqual(agg.returncode, 0, agg.stderr)
        self.assertEqual(json.loads(agg.stdout)["summary"]["verdict"], "infra")


if __name__ == "__main__":
    unittest.main()
