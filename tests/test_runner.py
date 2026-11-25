"""Unit tests for evalgate.runner CLI and regression diffing."""

import json
import tempfile
import unittest
from pathlib import Path

from evalgate.runner import main


class TestRunner(unittest.TestCase):
    def test_run_detects_regressions_and_exits_nonzero(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            out_md = Path(tmp) / "latest.md"
            code = main(
                [
                    "run",
                    "--target",
                    "fixture",
                    "--suite",
                    str(repo_root / "cases/suite.json"),
                    "--baseline",
                    str(repo_root / "cases/baseline.json"),
                    "--out",
                    str(out_md),
                    "--fail-on-regression",
                ]
            )
            self.assertEqual(code, 1)
            self.assertTrue(out_md.exists())
            data = json.loads(out_md.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(data["passed"], 4)
            self.assertEqual(data["total"], 6)
            self.assertEqual(
                sorted(data["regressions"]),
                ["pii-email-redact", "retry-backoff-cap"],
            )
            history_files = list((Path(tmp) / "history").glob("*.json"))
            self.assertEqual(len(history_files), 1)

    def test_baseline_promotion_and_clean_pass(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            out_md = Path(tmp) / "latest.md"
            new_base = Path(tmp) / "baseline.json"

            # First run without --fail-on-regression succeeds and writes latest.json
            code = main(
                [
                    "run",
                    "--target",
                    "fixture",
                    "--suite",
                    str(repo_root / "cases/suite.json"),
                    "--baseline",
                    str(repo_root / "cases/baseline.json"),
                    "--out",
                    str(out_md),
                ]
            )
            self.assertEqual(code, 0)

            # Promote to new baseline
            code = main(
                [
                    "baseline",
                    "--from",
                    str(out_md.with_suffix(".json")),
                    "--out",
                    str(new_base),
                ]
            )
            self.assertEqual(code, 0)

            # Re-running against the new baseline has 0 regressions -> exit 0
            code = main(
                [
                    "run",
                    "--target",
                    "fixture",
                    "--suite",
                    str(repo_root / "cases/suite.json"),
                    "--baseline",
                    str(new_base),
                    "--out",
                    str(out_md),
                    "--fail-on-regression",
                ]
            )
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
