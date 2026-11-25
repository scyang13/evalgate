"""Pluggable execution targets for evalgate (AI gateway, Gemini, CLI, HTTP, fixture)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TargetResult:
    """Normalized response from any eval target."""

    output: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


def _build_prompt(case: dict[str, Any]) -> str:
    prompt = str(case.get("prompt", "")).strip()
    case_input = case.get("input")
    if case_input:
        return f"{prompt}\n\nInput:\n{case_input}"
    return prompt


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_s: float,
) -> tuple[dict[str, Any], int]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    latency_ms = int((time.perf_counter() - start) * 1000)
    return json.loads(raw), latency_ms


def run_fixture_target(case: dict[str, Any]) -> TargetResult:
    """Offline target using pre-recorded candidate_output from the suite file."""
    return TargetResult(
        output=str(case.get("candidate_output", "")),
        latency_ms=int(case.get("latency_ms", 0)),
        input_tokens=case.get("input_tokens"),
        output_tokens=case.get("output_tokens"),
    )


def run_cmd_target(
    case: dict[str, Any], cmd: str, timeout_s: float = 60.0
) -> TargetResult:
    """Pipe the prompt to any local CLI/script and capture stdout."""
    full_prompt = _build_prompt(case)
    argv = shlex.split(cmd)
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return TargetResult(
            output="",
            latency_ms=int((time.perf_counter() - start) * 1000),
            error=f"command timed out after {timeout_s}s",
        )
    latency_ms = int((time.perf_counter() - start) * 1000)
    if proc.returncode != 0:
        err = proc.stderr.strip() or f"exit code {proc.returncode}"
        return TargetResult(output=proc.stdout.strip(), latency_ms=latency_ms, error=err)
    return TargetResult(output=proc.stdout.strip(), latency_ms=latency_ms)


def run_gateway_target(
    case: dict[str, Any],
    model: str,
    base_url: str | None = None,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout_s: float = 60.0,
) -> TargetResult:
    """Call an OpenAI-compatible AI gateway (or OpenAI/OpenRouter/vLLM/LiteLLM)."""
    resolved_base = (
        base_url
        or os.environ.get("EVALGATE_GATEWAY_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    resolved_key = (
        api_key
        or os.environ.get("EVALGATE_API_KEY")
        or os.environ.get("AI_GATEWAY_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not resolved_key:
        return TargetResult(
            output="",
            latency_ms=0,
            error="missing API key (set EVALGATE_API_KEY, AI_GATEWAY_API_KEY, or OPENAI_API_KEY)",
        )

    url = (
        resolved_base
        if resolved_base.endswith("/chat/completions")
        else f"{resolved_base}/chat/completions"
    )
    headers = {"Authorization": f"Bearer {resolved_key}", **(extra_headers or {})}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _build_prompt(case)}],
        "temperature": 0,
    }
    try:
        data, latency_ms = _http_post_json(url, payload, headers, timeout_s)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return TargetResult(output="", latency_ms=0, error=f"gateway error: {exc}")

    choices = data.get("choices") or []
    text = ""
    if choices:
        text = str(choices[0].get("message", {}).get("content", "")).strip()
    usage = data.get("usage") or {}
    return TargetResult(
        output=text,
        latency_ms=latency_ms,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
    )


def run_gemini_target(
    case: dict[str, Any],
    model: str = "gemini-2.5-flash",
    api_key: str | None = None,
    timeout_s: float = 60.0,
) -> TargetResult:
    """Call the Gemini REST API directly via GEMINI_API_KEY."""
    resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not resolved_key:
        return TargetResult(
            output="",
            latency_ms=0,
            error="missing GEMINI_API_KEY environment variable",
        )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={resolved_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": _build_prompt(case)}]}],
        "generationConfig": {"temperature": 0},
    }
    try:
        data, latency_ms = _http_post_json(url, payload, {}, timeout_s)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return TargetResult(output="", latency_ms=0, error=f"gemini error: {exc}")

    candidates = data.get("candidates") or []
    parts = (
        candidates[0].get("content", {}).get("parts", []) if candidates else []
    )
    text = "".join(str(p.get("text", "")) for p in parts).strip()
    usage = data.get("usageMetadata") or {}
    return TargetResult(
        output=text,
        latency_ms=latency_ms,
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
    )


def run_http_target(
    case: dict[str, Any],
    url: str,
    response_key: str = "output",
    extra_headers: dict[str, str] | None = None,
    timeout_s: float = 60.0,
) -> TargetResult:
    """POST prompt + input JSON to a custom HTTP agent endpoint."""
    payload = {
        "id": case.get("id"),
        "prompt": case.get("prompt", ""),
        "input": case.get("input", ""),
    }
    try:
        data, latency_ms = _http_post_json(
            url, payload, extra_headers or {}, timeout_s
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return TargetResult(output="", latency_ms=0, error=f"http error: {exc}")

    output = data.get(response_key)
    if output is None:
        return TargetResult(
            output="",
            latency_ms=latency_ms,
            error=f"response missing key '{response_key}'",
        )
    return TargetResult(
        output=output if isinstance(output, str) else json.dumps(output),
        latency_ms=latency_ms,
        input_tokens=data.get("input_tokens"),
        output_tokens=data.get("output_tokens"),
    )


def execute_target(
    target_type: str,
    case: dict[str, Any],
    *,
    model: str = "gemini-2.5-flash",
    cmd: str | None = None,
    base_url: str | None = None,
    url: str | None = None,
    response_key: str = "output",
    headers: dict[str, str] | None = None,
    timeout_s: float = 60.0,
) -> TargetResult:
    """Dispatch a test case to the configured target adapter."""
    if target_type == "fixture":
        return run_fixture_target(case)
    if target_type == "cmd":
        if not cmd:
            return TargetResult(output="", latency_ms=0, error="--cmd is required for target 'cmd'")
        return run_cmd_target(case, cmd=cmd, timeout_s=timeout_s)
    if target_type in ("gateway", "openai"):
        return run_gateway_target(
            case,
            model=model,
            base_url=base_url,
            extra_headers=headers,
            timeout_s=timeout_s,
        )
    if target_type == "gemini":
        return run_gemini_target(case, model=model, timeout_s=timeout_s)
    if target_type == "http":
        if not url:
            return TargetResult(output="", latency_ms=0, error="--url is required for target 'http'")
        return run_http_target(
            case,
            url=url,
            response_key=response_key,
            extra_headers=headers,
            timeout_s=timeout_s,
        )
    return TargetResult(output="", latency_ms=0, error=f"unknown target: {target_type}")
