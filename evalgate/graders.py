"""Deterministic, budget, and custom-command graders for evalgate."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from typing import Any

from evalgate.targets import TargetResult


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def grade_result(case: dict[str, Any], result: TargetResult) -> tuple[bool, list[str]]:
    """Evaluate all configured assertions for a single case.

    Returns (passed, failure_reasons).
    """
    reasons: list[str] = []
    if result.error:
        return False, [f"target error: {result.error}"]

    output = result.output.strip()

    # 1. Structured JSON checks
    if case.get("json_valid") or "expected_keys" in case or "expected_json" in case:
        raw_json = output if case.get("strict_json", True) else _strip_json_fence(output)
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            reasons.append(f"invalid JSON: {exc.msg}")
            parsed = None

        if parsed is not None:
            if "expected_keys" in case:
                if not isinstance(parsed, dict):
                    reasons.append("expected JSON object for expected_keys")
                else:
                    missing = [k for k in case["expected_keys"] if k not in parsed]
                    if missing:
                        reasons.append(f"missing keys: {missing}")
            if "expected_json" in case and parsed != case["expected_json"]:
                reasons.append(f"expected {case['expected_json']!r}, got {parsed!r}")

    # 2. Required / forbidden tokens
    for token in case.get("required_tokens", []):
        if token not in output:
            reasons.append(f"missing required token: {token}")

    upper_out = output.upper()
    for token in case.get("forbidden_tokens", []):
        if token.upper() in upper_out:
            reasons.append(f"found forbidden token: {token}")

    # 3. Forbidden CSV cell prefixes (formula injection check)
    if "forbidden_prefixes" in case:
        cells = [c.strip() for c in output.split(",")]
        for cell in cells:
            if any(cell.startswith(p) for p in case["forbidden_prefixes"]):
                reasons.append(f"unescaped formula cell: {cell}")

    # 4. Regex checks
    for pattern in case.get("regex_match", []):
        if not re.search(pattern, output):
            reasons.append(f"did not match regex: {pattern}")

    for pattern in case.get("regex_not_match", []):
        if re.search(pattern, output):
            reasons.append(f"matched forbidden regex: {pattern}")

    # 5. Latency & token budgets
    max_latency = case.get("max_latency_ms")
    if max_latency is not None and result.latency_ms > int(max_latency):
        reasons.append(f"latency {result.latency_ms}ms > budget {max_latency}ms")

    max_tokens = case.get("max_output_tokens")
    if (
        max_tokens is not None
        and result.output_tokens is not None
        and result.output_tokens > int(max_tokens)
    ):
        reasons.append(f"output tokens {result.output_tokens} > budget {max_tokens}")

    # 6. Custom external grader command
    grader_cmd = case.get("grader_cmd")
    if grader_cmd:
        env = {**os.environ, "EVALGATE_CASE_ID": str(case.get("id", ""))}
        try:
            proc = subprocess.run(
                shlex.split(grader_cmd),
                input=output,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                check=False,
            )
            if proc.returncode != 0:
                msg = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
                reasons.append(f"grader_cmd failed: {msg}")
        except subprocess.TimeoutExpired:
            reasons.append("grader_cmd timed out after 30s")

    return len(reasons) == 0, reasons
