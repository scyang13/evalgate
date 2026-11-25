"""Read-only Streamable HTTP MCP server exposing evalgate run history."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(os.environ.get("EVALGATE_REPORTS_DIR", "reports"))


def load_runs(reports_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load run JSON files from reports/history (plus latest.json fallback)."""
    root = reports_dir or REPORTS_DIR
    history_dir = root / "history"
    paths: list[Path] = []
    if history_dir.exists():
        paths.extend(sorted(history_dir.glob("*.json")))
    latest = root / "latest.json"
    if latest.exists() and not paths:
        paths.append(latest)

    runs: list[dict[str, Any]] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_file"] = path.name
            runs.append(data)
        except json.JSONDecodeError:
            continue
    return runs


def list_eval_runs(reports_dir: Path | None = None) -> list[dict[str, Any]]:
    """List available eval runs with pass rate, latency, and regressions."""
    return [
        {
            "run_id": r.get("run_id"),
            "suite_name": r.get("suite_name"),
            "target": r.get("target"),
            "model": r.get("model"),
            "passed": r.get("passed"),
            "total": r.get("total"),
            "pass_rate": r.get("pass_rate"),
            "p50_latency_ms": r.get("p50_latency_ms"),
            "regressions": r.get("regressions", []),
        }
        for r in load_runs(reports_dir)
    ]


def _find_run(run_id: str, reports_dir: Path | None = None) -> dict[str, Any] | None:
    runs = load_runs(reports_dir)
    if not runs:
        return None
    if run_id == "latest":
        return runs[-1]
    return next((r for r in runs if r.get("run_id") == run_id), None)


def get_regression_diff(
    run_id: str = "latest", reports_dir: Path | None = None
) -> dict[str, Any]:
    """Compare an eval run against its baseline and return deltas."""
    target = _find_run(run_id, reports_dir)
    if not target:
        return {"error": f"run_id '{run_id}' not found"}
    return {
        "run_id": target.get("run_id"),
        "baseline_run_id": target.get("baseline_run_id"),
        "pass_rate": target.get("pass_rate"),
        "pass_rate_delta": target.get("pass_rate_delta"),
        "p50_latency_ms": target.get("p50_latency_ms"),
        "p50_latency_delta_ms": target.get("p50_latency_delta_ms"),
        "regressions": target.get("regressions", []),
        "improvements": target.get("improvements", []),
    }


def get_failing_cases(
    run_id: str = "latest", reports_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Return failing or regressed cases with failure reasons and outputs."""
    target = _find_run(run_id, reports_dir)
    if not target:
        return [{"error": f"run_id '{run_id}' not found"}]
    return [c for c in target.get("cases", []) if not c.get("passed")]


def get_flaky_cases(
    window: int = 10, reports_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Find cases that flip between pass and fail across the last N runs."""
    runs = load_runs(reports_dir)[-max(1, window) :]
    history: dict[str, list[bool]] = defaultdict(list)
    for run in runs:
        for case in run.get("cases", []):
            history[case["id"]].append(bool(case.get("passed")))

    flaky: list[dict[str, Any]] = []
    for case_id, outcomes in sorted(history.items()):
        flips = sum(1 for a, b in zip(outcomes, outcomes[1:]) if a != b)
        if flips > 0:
            flaky.append(
                {
                    "id": case_id,
                    "runs_checked": len(outcomes),
                    "passes": sum(outcomes),
                    "fails": len(outcomes) - sum(outcomes),
                    "flips": flips,
                }
            )
    return sorted(flaky, key=lambda item: item["flips"], reverse=True)


def create_mcp_server() -> Any:
    """Create and configure the FastMCP server (requires optional [mcp] extra)."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("evalgate")

    @mcp.tool(name="list_eval_runs")
    def _list_eval_runs() -> list[dict[str, Any]]:
        """List available eval runs with pass rate, latency, and regressions."""
        return list_eval_runs()

    @mcp.tool(name="get_regression_diff")
    def _get_regression_diff(run_id: str = "latest") -> dict[str, Any]:
        """Compare an eval run against its baseline and return deltas."""
        return get_regression_diff(run_id=run_id)

    @mcp.tool(name="get_failing_cases")
    def _get_failing_cases(run_id: str = "latest") -> list[dict[str, Any]]:
        """Return failing or regressed cases with failure reasons and outputs."""
        return get_failing_cases(run_id=run_id)

    @mcp.tool(name="get_flaky_cases")
    def _get_flaky_cases(window: int = 10) -> list[dict[str, Any]]:
        """Find cases that flip between pass and fail across the last N runs."""
        return get_flaky_cases(window=window)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Start evalgate MCP server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args()

    mcp = create_mcp_server()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
