# Pipeline Incident Copilot

Diagnose failed pipelines from local logs, schema drift, and run-metric
anomalies; get ranked, typed diagnoses with recommended fixes. Deterministic
and offline — no LLM calls, no network access, every conclusion traceable to
the evidence ids that produced it. See [docs/design.md](docs/design.md).

## Analyze a bundle (CLI)

```bash
pipeline-copilot analyze ./incident-dir            # Markdown report
pipeline-copilot analyze ./incident-dir --format json --output report.json
```

Bundle layout: `logs/*.log|*.txt|*.jsonl`, `schema/before.json`,
`schema/after.json`, `metrics/runs.json` — all optional; whatever exists is
used. Exit codes: 0 diagnosed, 1 invalid bundle, 3 no evidence.

## Run the local API

```bash
pipeline-copilot serve        # loopback only, prints a session token
```

Then: `POST /api/incidents`, `POST /api/incidents/{id}/files?kind=log&filename=…`
(body = raw bytes), `POST /api/incidents/{id}/analyze`,
`GET /api/incidents/{id}/report` — all with `X-Session-Token`.

## Develop

```bash
uv sync --extra dev
python -m pytest backend/tests -q
python -m ruff check backend
python -m mypy backend/src
```

## What it detects

- **Log signatures:** OOM, disk full, permission denied, connection
  failures, retry exhaustion, SQL syntax errors, missing columns/tables,
  cast failures — with root-event vs cascade separation.
- **Schema drift:** added/removed/renamed columns, type and nullability
  changes, severity-scored.
- **Metric anomalies:** row-count collapse/surge, zero rows, null-ratio
  jumps, distinct-count collapse, duration surges vs a median baseline of
  prior successful runs.
- **Diagnoses:** nine correlated causes (documented confidence weights,
  capped at 95%) each with a concrete fix checklist, plus an explicit
  needs-human fallback.
