#!/usr/bin/env python3
"""Regression tests for scripts/validate.py. Run: python3 tests/test_validate.py"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALIDATE = os.path.join(ROOT, "scripts", "validate.py")

GOOD_RESULT = {
    "device": {
        "platform": "ios",
        "id": "ABC-123",
        "name": "simsquad-x-ios-0",
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
                "target": "Log in",
                "observation": "Login screen opened",
                "screenshot": "screenshots/step-001.png",
            }
        ],
        "findings": [
            {
                "type": "bug",
                "severity": "high",
                "title": "Magic link does not return to app",
                "evidence": ["screenshots/step-001.png"],
            },
            {"type": "note", "title": "Animation feels slow"},
        ],
    },
}


def run(kind, path):
    proc = subprocess.run(
        [sys.executable, VALIDATE, kind, path], capture_output=True, text=True
    )
    return proc.returncode, proc.stderr


class ValidateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        shots = os.path.join(self.dir.name, "screenshots")
        os.makedirs(shots)
        for name in ("smoke-01.png", "step-001.png"):
            open(os.path.join(shots, name), "w").close()

    def tearDown(self):
        self.dir.cleanup()

    def write(self, name, doc):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return path

    def test_example_feature_context_valid(self):
        code, err = run(
            "feature-context", os.path.join(ROOT, "examples", "feature-context.example.json")
        )
        self.assertEqual(code, 0, err)

    def test_bad_feature_context(self):
        path = self.write(
            "ctx.json",
            {"feature_id": "Login!", "title": "", "smoke": [], "explore": {"timebox_minutes": 90}},
        )
        code, err = run("feature-context", path)
        self.assertEqual(code, 1)
        for fragment in ("pattern", "minLength", "minItems", "charter", "maximum"):
            self.assertIn(fragment, err)

    def test_good_result_valid(self):
        code, err = run("result", self.write("result.json", GOOD_RESULT))
        self.assertEqual(code, 0, err)

    def test_result_structural_errors(self):
        doc = copy.deepcopy(GOOD_RESULT)
        doc["smoke"]["status"] = "maybe"
        doc["extra_key"] = 1
        code, err = run("result", self.write("result.json", doc))
        self.assertEqual(code, 1)
        self.assertIn("not one of", err)
        self.assertIn("unexpected key", err)

    def test_result_semantic_errors(self):
        doc = copy.deepcopy(GOOD_RESULT)
        doc["exploration"]["findings"][0].pop("severity")
        doc["exploration"]["findings"][0]["evidence"] = []
        doc["exploration"]["actions"][0]["screenshot"] = "screenshots/ghost.png"
        code, err = run("result", self.write("result.json", doc))
        self.assertEqual(code, 1)
        self.assertIn("requires severity", err)
        self.assertIn("non-empty evidence", err)
        self.assertIn("does not exist", err)

    def _report(self):
        dev = dict(GOOD_RESULT["device"])
        dev["worker_status"] = "ok"
        dev["smoke"] = copy.deepcopy(GOOD_RESULT["smoke"])
        dev["exploration"] = copy.deepcopy(GOOD_RESULT["exploration"])
        return {
            "run_id": "login-magic-link-20260612-160000",
            "feature_id": "login-magic-link",
            "started_at": "2026-06-12T16:00:00Z",
            "finished_at": "2026-06-12T16:09:00Z",
            "squad": {"name": "pilot-login-magic-link-20260612-160000"},
            "summary": {
                "verdict": "failed",
                "devices_total": 1,
                "smoke_passed": 1,
                "smoke_failed": 0,
                "bugs": 1,
                "blocked": 0,
                "questions": 0,
            },
            "devices": [dev],
        }

    def test_good_report_valid(self):
        code, err = run("report", self.write("report.json", self._report()))
        self.assertEqual(code, 0, err)

    def test_report_count_mismatch(self):
        doc = self._report()
        doc["summary"]["bugs"] = 5
        code, err = run("report", self.write("report.json", doc))
        self.assertEqual(code, 1)
        self.assertIn("devices say 1", err)


if __name__ == "__main__":
    unittest.main()
