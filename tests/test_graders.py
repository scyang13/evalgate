"""Unit tests for evalgate.graders."""

import sys
import unittest

from evalgate.graders import grade_result
from evalgate.targets import TargetResult


class TestGraders(unittest.TestCase):
    def test_json_keys_and_exact_match(self) -> None:
        case = {"json_valid": True, "expected_keys": ["a", "b"]}
        passed, reasons = grade_result(case, TargetResult('{"a": 1, "b": 2}', 50))
        self.assertTrue(passed)
        self.assertEqual(reasons, [])

        passed, reasons = grade_result(case, TargetResult('{"a": 1}', 50))
        self.assertFalse(passed)
        self.assertIn("missing keys: ['b']", reasons[0])

    def test_exact_json_mismatch(self) -> None:
        case = {"expected_json": [1, 2, 4, 5]}
        passed, reasons = grade_result(case, TargetResult("[1, 2, 4, 8]", 40))
        self.assertFalse(passed)
        self.assertIn("expected [1, 2, 4, 5]", reasons[0])

    def test_tokens_and_regex(self) -> None:
        case = {
            "required_tokens": ["[REDACTED]"],
            "forbidden_tokens": ["alice@example.com"],
            "regex_not_match": [r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"],
        }
        passed, _ = grade_result(
            case, TargetResult("auth failed for [REDACTED]", 20)
        )
        self.assertTrue(passed)

        passed, reasons = grade_result(
            case, TargetResult("auth failed for alice@example.com", 20)
        )
        self.assertFalse(passed)
        self.assertGreaterEqual(len(reasons), 2)

    def test_csv_formula_escape(self) -> None:
        case = {"forbidden_prefixes": ["=", "+", "-", "@"]}
        passed, _ = grade_result(case, TargetResult("'=SUM(A1),ok,'+cmd", 10))
        self.assertTrue(passed)

        passed, reasons = grade_result(case, TargetResult("=SUM(A1),ok", 10))
        self.assertFalse(passed)
        self.assertIn("unescaped formula cell", reasons[0])

    def test_latency_and_token_budgets(self) -> None:
        case = {"max_latency_ms": 100, "max_output_tokens": 50}
        passed, reasons = grade_result(
            case, TargetResult("ok", latency_ms=250, output_tokens=80)
        )
        self.assertFalse(passed)
        self.assertEqual(len(reasons), 2)

    def test_custom_grader_cmd(self) -> None:
        cmd_pass = f'{sys.executable} -c "import sys; sys.exit(0 if \\"ok\\" in sys.stdin.read() else 1)"'
        passed, _ = grade_result({"grader_cmd": cmd_pass}, TargetResult("all ok", 5))
        self.assertTrue(passed)

        passed, reasons = grade_result({"grader_cmd": cmd_pass}, TargetResult("nope", 5))
        self.assertFalse(passed)
        self.assertIn("grader_cmd failed", reasons[0])

    def test_target_error_short_circuits(self) -> None:
        passed, reasons = grade_result({}, TargetResult("", 0, error="boom"))
        self.assertFalse(passed)
        self.assertEqual(reasons, ["target error: boom"])


if __name__ == "__main__":
    unittest.main()
