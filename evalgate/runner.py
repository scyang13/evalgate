"""Parallel eval runner, baseline regression diff, and CLI entrypoint."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from evalgate.graders import grade_result
from evalgate.targets import execute_target


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def _parse_headers(raw_headers: list[str] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in raw_headers or []:
        if ":" not in item:
            raise ValueError(f"invalid header '{item}'; expected 'Key: Value'")
        key, val = item.split(":", 1)
        parsed[key.strip()] = val.strip()
    return parsed


def diff_runs(
    candidate: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    """Compare a candidate run summary against a baseline summary."""
    base_cases = baseline.get("cases", {})
    if isinstance(base_cases, list):
        base_map = {c["id"]: c for c in base_cases}
    else:
        base_map = base_cases

    regressions: list[str] = []
    improvements: list[str] = []
    for case in candidate.get("cases", []):
        prev = base_map.get(case["id"])
        if prev:
            if prev.get("passed") and not case.get("passed"):
                case["regressed"] = True
                regressions.append(case["id"])
            elif not prev.get("passed") and case.get("passed"):
                case["improved"] = True
                improvements.append(case["id"])

    return {
        "baseline_run_id": baseline.get("run_id"),
        "pass_rate_delta": round(
            float(candidate.get("pass_rate", 0.0))
            - float(baseline.get("pass_rate", 0.0)),
            4,
        ),
        "p50_latency_delta_ms": int(candidate.get("p50_latency_ms", 0))
        - int(baseline.get("p50_latency_ms", 0)),
        "regressions": regressions,
        "improvements": improvements,
    }


def run_suite(
    suite_path: Path,
    *,
    target_type: str = "fixture",
    model: str = "gemini-2.5-flash",
    cmd: str | None = None,
    base_url: str | None = None,
    url: str | None = None,
    response_key: str = "output",
    headers: dict[str, str] | None = None,
    baseline_path: Path | None = None,
    concurrency: int = 4,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Run all cases in suite_path concurrently and diff against baseline."""
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path and baseline_path.exists()
        else None
    )

    def _eval_one(case: dict[str, Any]) -> dict[str, Any]:
        target_res = execute_target(
            target_type,
            case,
            model=model,
            cmd=cmd,
            base_url=base_url,
            url=url,
            response_key=response_key,
            headers=headers,
            timeout_s=timeout_s,
        )
        passed, reasons = grade_result(case, target_res)
        return {
            "id": case["id"],
            "category": case.get("category", "general"),
            "passed": passed,
            "reason": "; ".join(reasons) if reasons else "ok",
            "latency_ms": target_res.latency_ms,
            "input_tokens": target_res.input_tokens,
            "output_tokens": target_res.output_tokens,
            "regressed": False,
            "improved": False,
            "output": target_res.output,
        }

    cases = suite.get("cases", [])
    max_workers = max(1, min(concurrency, len(cases) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_eval_one, cases))

    latencies = [r["latency_ms"] for r in results]
    passed_count = sum(1 for r in results if r["passed"])
    total = len(results)
    pass_rate = round(passed_count / total, 4) if total else 0.0

    now = dt.datetime.now(dt.timezone.utc)
    summary: dict[str, Any] = {
        "run_id": now.strftime("run-%Y%m%d-%H%M%S"),
        "timestamp_utc": now.isoformat(),
        "suite_name": suite.get("suite_name", suite_path.stem),
        "target": target_type,
        "model": model if target_type in ("gateway", "openai", "gemini") else None,
        "passed": passed_count,
        "total": total,
        "pass_rate": pass_rate,
        "p50_latency_ms": int(statistics.median(latencies)) if latencies else 0,
        "p95_latency_ms": _percentile(latencies, 95),
        "regressions": [],
        "improvements": [],
        "cases": results,
    }

    if baseline:
        summary.update(diff_runs(summary, baseline))

    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    """Render a GitHub-flavored Markdown regression table."""
    lines = [
        f"## Evalgate Report: `{summary['suite_name']}` (`{summary['run_id']}`)",
        "",
        f"- **Target:** `{summary.get('target', 'unknown')}`"
        + (f" (`{summary['model']}`)" if summary.get("model") else ""),
        f"- **Pass rate:** **{summary['passed']}/{summary['total']}** ({summary['pass_rate'] * 100:.1f}%)",
        f"- **Latency:** p50 `{summary['p50_latency_ms']} ms` | p95 `{summary.get('p95_latency_ms', 0)} ms`",
    ]
    if summary.get("baseline_run_id"):
        lines.append(
            f"- **Delta vs `{summary['baseline_run_id']}`:** "
            f"`{summary['pass_rate_delta'] * 100:+.1f}%` pass rate, "
            f"`{summary['p50_latency_delta_ms']:+d} ms` p50 latency"
        )
    regressions = summary.get("regressions") or []
    lines.append(
        f"- **Regressions:** {', '.join(f'`{r}`' for r in regressions) if regressions else 'none'}"
    )
    lines.extend(
        [
            "",
            "| Case | Category | Status | Latency (ms) | Notes |",
            "| :--- | :--- | :--- | ---: | :--- |",
        ]
    )
    for c in summary.get("cases", []):
        if c.get("regressed"):
            status = "REGRESSION"
        elif c.get("improved"):
            status = "FIXED"
        elif c.get("passed"):
            status = "PASS"
        else:
            status = "FAIL"
        notes = str(c.get("reason", "ok")).replace("|", "\\|")
        lines.append(
            f"| `{c['id']}` | {c.get('category', 'general')} | {status} | {c.get('latency_ms', 0)} | {notes} |"
        )
    return "\n".join(lines) + "\n"


def _cmd_run(args: argparse.Namespace) -> int:
    headers = _parse_headers(args.header)
    summary = run_suite(
        args.suite,
        target_type=args.target,
        model=args.model,
        cmd=args.cmd,
        base_url=args.base_url,
        url=args.url,
        response_key=args.response_key,
        headers=headers,
        baseline_path=args.baseline,
        concurrency=args.concurrency,
        timeout_s=args.timeout,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    md = render_markdown(summary)
    args.out.write_text(md, encoding="utf-8")

    json_out = args.out.with_suffix(".json")
    json_payload = json.dumps(summary, indent=2) + "\n"
    json_out.write_text(json_payload, encoding="utf-8")

    history_dir = args.out.parent / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"{summary['run_id']}.json").write_text(
        json_payload, encoding="utf-8"
    )

    print(
        f"evalgate: {summary['passed']}/{summary['total']} passed "
        f"({summary['pass_rate'] * 100:.1f}%), "
        f"{len(summary['regressions'])} regressions, "
        f"p50={summary['p50_latency_ms']}ms"
    )
    print(f"Reports: {args.out} | {json_out}")

    if args.fail_on_regression and summary["regressions"]:
        print(
            f"FAIL: regressions detected: {', '.join(summary['regressions'])}",
            file=sys.stderr,
        )
        return 1
    if args.min_pass_rate is not None and summary["pass_rate"] < args.min_pass_rate:
        print(
            f"FAIL: pass rate {summary['pass_rate']:.2%} < min {args.min_pass_rate:.2%}",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    run_data = json.loads(args.source.read_text(encoding="utf-8"))
    baseline = {
        "run_id": run_data.get("run_id"),
        "suite_name": run_data.get("suite_name"),
        "pass_rate": run_data.get("pass_rate"),
        "passed": run_data.get("passed"),
        "total": run_data.get("total"),
        "p50_latency_ms": run_data.get("p50_latency_ms"),
        "cases": {
            c["id"]: {"passed": c["passed"], "latency_ms": c.get("latency_ms", 0)}
            for c in run_data.get("cases", [])
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print(f"Updated baseline {args.out} from {run_data.get('run_id')}")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    candidate.update(diff_runs(candidate, baseline))
    print(render_markdown(candidate))
    return 1 if (args.fail_on_regression and candidate.get("regressions")) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evalgate",
        description="CI/CD prompt & model regression gate.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_run = sub.add_parser("run", help="Run an eval suite and compare to baseline.")
    p_run.add_argument("--suite", type=Path, default=Path("cases/suite.json"))
    p_run.add_argument("--baseline", type=Path, default=Path("cases/baseline.json"))
    p_run.add_argument("--out", type=Path, default=Path("reports/latest.md"))
    p_run.add_argument(
        "--target",
        choices=["gateway", "openai", "gemini", "cmd", "http", "fixture"],
        default="fixture",
    )
    p_run.add_argument("--model", default="gemini-2.5-flash")
    p_run.add_argument("--base-url", help="AI gateway or OpenAI-compatible base URL")
    p_run.add_argument("--cmd", help="Shell command for --target cmd")
    p_run.add_argument("--url", help="Endpoint URL for --target http")
    p_run.add_argument("--response-key", default="output")
    p_run.add_argument("--header", action="append", help="Extra HTTP header 'Key: Value'")
    p_run.add_argument("--concurrency", type=int, default=4)
    p_run.add_argument("--timeout", type=float, default=60.0)
    p_run.add_argument("--fail-on-regression", action="store_true")
    p_run.add_argument("--min-pass-rate", type=float, default=None)
    p_run.set_defaults(func=_cmd_run)

    p_base = sub.add_parser("baseline", help="Promote a run JSON to baseline.json.")
    p_base.add_argument("--from", dest="source", type=Path, default=Path("reports/latest.json"))
    p_base.add_argument("--out", type=Path, default=Path("cases/baseline.json"))
    p_base.set_defaults(func=_cmd_baseline)

    p_diff = sub.add_parser("diff", help="Compare two run JSON files.")
    p_diff.add_argument("--baseline", type=Path, required=True)
    p_diff.add_argument("--candidate", type=Path, required=True)
    p_diff.add_argument("--fail-on-regression", action="store_true")
    p_diff.set_defaults(func=_cmd_diff)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
