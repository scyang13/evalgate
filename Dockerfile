FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY cases ./cases
COPY evalgate ./evalgate

RUN pip install --no-cache-dir ".[mcp]" && \
    python3 -m evalgate run --target fixture --suite cases/suite.json --baseline cases/baseline.json --out reports/latest.md || true

ENV PORT=8080
EXPOSE 8080
CMD ["python3", "-m", "evalgate.mcp_server"]
