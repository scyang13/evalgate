# evalgate

A zero-dependency CI/CD regression gate for prompts, models, and AI agents — plus a read-only Streamable HTTP MCP server for querying run history.

Point `evalgate` at an AI gateway, Gemini API, local CLI, or HTTP endpoint. It runs your test cases in parallel, grades outputs against deterministic assertions and latency/token budgets, diffs against `baseline.json`, and exits `1` on regressions so it drops straight into GitHub Actions.

## Quick start

No pip dependencies required for the core CLI (Python 3.10+ stdlib only):

```bash
# Offline smoke test using bundled fixtures
python3 -m evalgate run \
  --target fixture \
  --suite cases/suite.json \
  --baseline cases/baseline.json \
  --out reports/latest.md \
  --fail-on-regression
```

### Run against an AI gateway (OpenAI-compatible)

Works with any AI gateway (LiteLLM, OpenRouter, Cloudflare AI Gateway, vLLM, or OpenAI directly):

```bash
export EVALGATE_GATEWAY_URL="https://your-ai-gateway.example.com/v1"
export EVALGATE_API_KEY="your-key"

python3 -m evalgate run \
  --target gateway \
  --model gemini-2.5-flash \
  --suite cases/suite.json \
  --baseline cases/baseline.json \
  --out reports/latest.md \
  --fail-on-regression
```

### Other targets

```bash
# Direct Gemini REST API (uses GEMINI_API_KEY)
python3 -m evalgate run --target gemini --model gemini-2.5-flash --fail-on-regression

# Any local CLI / script (pipes prompt to stdin, grades stdout)
python3 -m evalgate run --target cmd --cmd "python3 my_agent.py" --fail-on-regression

# Any custom HTTP agent endpoint
python3 -m evalgate run --target http --url https://agent.example.com/run --response-key answer
```

### Promote a new baseline or diff two runs

```bash
python3 -m evalgate baseline --from reports/latest.json --out cases/baseline.json
python3 -m evalgate diff --baseline cases/baseline.json --candidate reports/latest.json
```

## Supported assertions

Each case in `cases/suite.json` supports any combination of:
- **JSON:** `json_valid`, `expected_keys`, `expected_json`, `strict_json`
- **Text & safety:** `required_tokens`, `forbidden_tokens`, `forbidden_prefixes` (CSV formula injection), `regex_match`, `regex_not_match`
- **Budgets:** `max_latency_ms`, `max_output_tokens`
- **Custom grader:** `grader_cmd` (pipes output to any shell/Python command; exit `0` = pass)

## GitHub Actions

A ready-to-use workflow lives at `.github/workflows/eval.yml`. On every pull request it runs `evalgate`, posts/updates a sticky PR comment with the per-case regression table, and fails the check if any case regresses.

## Remote MCP server

Expose your eval history to any MCP-compatible agent over Streamable HTTP:

```bash
pip install -e ".[mcp]"
python3 -m evalgate.mcp_server --port 8080
```

Read-only tools exposed:
- `list_eval_runs` — recent runs with pass rate, p50 latency, and regression count
- `get_regression_diff` — pass-rate and latency deltas against baseline
- `get_failing_cases` — failing/regressed cases with exact reasons and outputs
- `get_flaky_cases` — cases that oscillate between pass and fail across the last `N` runs

Deploy to Cloud Run behind IAM/IAP for per-user access control:

```bash
gcloud run deploy evalgate-mcp \
  --source . \
  --region us-central1 \
  --no-allow-unauthenticated
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT
