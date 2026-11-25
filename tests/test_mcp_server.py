"""Unit tests for evalgate.mcp_server read-only tools."""

import json
import tempfile
import unittest
from pathlib import Path

from evalgate.mcp_server import (
    get_failing_cases,
    get_flaky_cases,
    get_regression_diff,
    list_eval_runs,
)


class TestMcpServerTools(unittest.TestCase):
    def _seed_history(self, root: Path) -> None:
        hist = root / "history"
        hist.mkdir(parents=True, exist_ok=True)
        runs = [
            {
                "run_id": "run-1",
                "suite_name": "core",
                "target": "gateway",
                "model": "gemini-2.5-flash",
                "passed": 2,
                "total": 2,
                "pass_rate": 1.0,
                "p50_latency_ms": 200,
                "regressions": [],
                "cases": [
                    {"id": "c-stable", "passed": True, "reason": "ok"},
                    {"id": "c-flaky", "passed": True, "reason": "ok"},
                ],
            },
            {
                "run_id": "run-2",
                "suite_name": "core",
                "target": "gateway",
                "model": "gemini-2.5-flash",
                "baseline_run_id": "run-1",
                "passed": 1,
                "total": 2,
                "pass_rate": 0.5,
                "pass_rate_delta": -0.5,
                "p50_latency_ms": 250,
                "p50_latency_delta_ms": 50,
                "regressions": ["c-flaky"],
                "improvements": [],
                "cases": [
                    {"id": "c-stable", "passed": True, "reason": "ok"},
                    {"id": "c-flaky", "passed": False, "reason": "invalid JSON"},
                ],
            },
            {
                "run_id": "run-3",
                "suite_name": "core",
                "target": "gateway",
                "model": "gemini-2.5-flash",
                "baseline_run_id": "run-1",
                "passed": 2,
                "total": 2,
                "pass_rate": 1.0,
                "pass_rate_delta": 0.0,
                "p50_latency_ms": 210,
                "p50_latency_delta_ms": 10,
                "regressions": [],
                "improvements": [],
                "cases": [
                    {"id": "c-stable", "passed": True, "reason": "ok"},
                    {"id": "c-flaky", "passed": True, "reason": "ok"},
                ],
            },
        ]
        for r in runs:
            (hist / f"{r['run_id']}.json").write_text(json.dumps(r), encoding="utf-8")

    def test_mcp_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_history(root)

            runs = list_eval_runs(reports_dir=root)
            self.assertEqual(len(runs), 3)
            self.assertEqual(runs[-1]["run_id"], "run-3")

            diff = get_regression_diff("run-2", reports_dir=root)
            self.assertEqual(diff["regressions"], ["c-flaky"])
            self.assertEqual(diff["pass_rate_delta"], -0.5)

            failing = get_failing_cases("run-2", reports_dir=root)
            self.assertEqual(len(failing), 1)
            self.assertEqual(failing[0]["id"], "c-flaky")

            flaky = get_flaky_cases(window=10, reports_dir=root)
            self.assertEqual(len(flaky), 1)
            self.assertEqual(flaky[0]["id"], "c-flaky")
            self.assertEqual(flaky[0]["flips"], 2)


if __name__ == "__main__":
    unittest.main()
