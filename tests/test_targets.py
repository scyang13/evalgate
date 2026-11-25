"""Unit tests for evalgate.targets."""

import sys
import unittest
from unittest import mock

from evalgate.targets import execute_target


class TestTargets(unittest.TestCase):
    def test_fixture_target(self) -> None:
        res = execute_target(
            "fixture", {"candidate_output": "hello", "latency_ms": 123}
        )
        self.assertEqual(res.output, "hello")
        self.assertEqual(res.latency_ms, 123)
        self.assertIsNone(res.error)

    def test_cmd_target(self) -> None:
        cmd = f'{sys.executable} -c "import sys; print(sys.stdin.read().strip().upper())"'
        res = execute_target("cmd", {"prompt": "hi there"}, cmd=cmd)
        self.assertIsNone(res.error)
        self.assertEqual(res.output, "HI THERE")
        self.assertGreaterEqual(res.latency_ms, 0)

    def test_gateway_target_mocked(self) -> None:
        fake_resp = {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        }
        with (
            mock.patch.dict(
                "os.environ",
                {"EVALGATE_API_KEY": "test-key", "EVALGATE_GATEWAY_URL": "https://gw.local/v1"},
            ),
            mock.patch("evalgate.targets._http_post_json", return_value=(fake_resp, 88)) as post,
        ):
            res = execute_target("gateway", {"prompt": "test"}, model="gemini-2.5-flash")
        self.assertIsNone(res.error)
        self.assertEqual(res.output, '{"ok": true}')
        self.assertEqual(res.latency_ms, 88)
        self.assertEqual(res.input_tokens, 12)
        self.assertEqual(res.output_tokens, 5)
        self.assertEqual(post.call_args[0][0], "https://gw.local/v1/chat/completions")

    def test_gemini_target_mocked(self) -> None:
        fake_resp = {
            "candidates": [{"content": {"parts": [{"text": "SELECT 1;"}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4},
        }
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test-gemini-key"}),
            mock.patch("evalgate.targets._http_post_json", return_value=(fake_resp, 75)),
        ):
            res = execute_target("gemini", {"prompt": "sql"}, model="gemini-2.5-flash")
        self.assertIsNone(res.error)
        self.assertEqual(res.output, "SELECT 1;")
        self.assertEqual(res.output_tokens, 4)

    def test_http_target_mocked(self) -> None:
        with mock.patch(
            "evalgate.targets._http_post_json",
            return_value=({"answer": "42"}, 33),
        ):
            res = execute_target(
                "http",
                {"id": "c1", "prompt": "meaning"},
                url="https://agent.local/run",
                response_key="answer",
            )
        self.assertIsNone(res.error)
        self.assertEqual(res.output, "42")


if __name__ == "__main__":
    unittest.main()
