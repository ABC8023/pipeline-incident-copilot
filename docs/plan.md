# Pipeline Incident Copilot Implementation Plan

**Goal:** Diagnose failed pipelines from local logs, schema snapshots, and
run metrics; emit ranked typed diagnoses with recommended fixes, via CLI and
an authenticated loopback API.

**Spec:** `docs/superpowers/specs/2026-07-15-pipeline-incident-copilot-design.md`

**Tech:** Python 3.12–3.14, FastAPI, Pydantic v2, uvicorn, pytest, ruff, mypy.

**Conventions:** identical to the Data Cleanup Workbench — tests first per
task, typed errors, immutable staging, durable JSON persistence, ruff + mypy
clean, one commit per task, report in `.superpowers/sdd/`.

## File structure

```text
pyproject.toml
backend/src/pipeline_copilot/
  core/config.py            AppConfig (workspace, limits, origin)
  core/security.py          token, loopback validation
  domain/incident.py        IncidentManifest, StagedFile
  domain/evidence.py        LogSignatureHit, SchemaDrift, MetricAnomaly
  domain/diagnosis.py       Diagnosis, RecommendedFix, IncidentReport
  storage/incident_repository.py   immutable staging + durable persistence
  analysis/log_parser.py    tolerant line parser
  analysis/signatures.py    versioned signature catalog + scanner
  analysis/schema_drift.py  snapshot diff engine
  analysis/metrics.py       baseline + anomaly detectors
  diagnosis/catalog.py      cause rules + fix catalog
  diagnosis/engine.py       evidence correlation + ranking
  reporting/report.py       canonical JSON + Markdown rendering
  api/app.py                app factory (token, origin middleware)
  api/routes/…              health, incidents, analysis
  cli.py                    analyze + serve entry points
backend/tests/…             mirrors the packages
```

## Task 1: Project scaffold and authenticated API foundation

- `pyproject.toml` (fastapi, uvicorn, pydantic, httpx; dev: pytest,
  pytest-asyncio, ruff, mypy), `[project.scripts] pipeline-copilot`.
- `core/config.py` (frozen AppConfig: workspace, max_bundle_bytes=512 MiB,
  max_log_bytes=64 MiB, allowed_origin), `core/security.py` (token +
  loopback validation, ported), `api/app.py` with `GET /api/health`,
  token dependency, origin middleware.
- Tests: health auth, origin rejection, loopback validation.
- Commit: `feat: add authenticated copilot api foundation`.

## Task 2: Incident staging

- `domain/incident.py`: `StagedFile(kind, filename, size_bytes, sha256)`,
  `IncidentManifest(id, pipeline, files, state)`.
- `storage/incident_repository.py`: create incident; stage streamed file
  bytes immutably (size caps, sanitized filenames, kind whitelist:
  `log | schema_before | schema_after | metrics | manifest`); durable
  incident.json publication (tmp + fsync + replace); strict incident-id
  validation.
- Routes: `POST /api/incidents`, `POST /api/incidents/{id}/files`,
  `GET /api/incidents/{id}`.
- Tests: staging immutability + fingerprints, caps, traversal, API flow.
- Commit: `feat: stage immutable incident bundles`.

## Task 3: Log parsing and signature catalog

- `analysis/log_parser.py`: stream lines; parse JSON-lines and common text
  formats (ISO timestamp, level token); tolerant fallback to
  `level=unknown` raw lines; bounded line length.
- `analysis/signatures.py`: `SignatureRule(rule_id, rule_version, pattern,
  category, severity, is_cascade)` catalog (OOM, disk full, permission,
  connection refused/reset/timeout, retry exhaustion, SQL syntax, missing
  column/table, cast failure, upstream/dependency failure); scanner emits
  `LogSignatureHit(rule_id, line_number, file, timestamp, excerpt, matched:
  dict)` with excerpts capped and root-event selection (earliest non-cascade
  error; cascades attach `cascaded_from`).
- Tests: parser formats + fallbacks; each rule fires on a fixture line;
  root vs cascade selection; determinism.
- Commit: `feat: extract typed log evidence`.

## Task 4: Schema drift engine

- `domain/evidence.py` gains `SchemaDrift(kind: added|removed|renamed|
  type_changed|nullability_changed, table, column, before, after, severity)`.
- `analysis/schema_drift.py`: parse snapshots (flat or per-table), diff,
  rename detection (equal type + normalized-name similarity ≥ 0.8),
  severity mapping per spec.
- Tests: every drift kind, rename vs add/remove disambiguation, nested
  tables, malformed snapshot → typed error.
- Commit: `feat: detect schema drift`.

## Task 5: Metric anomaly engine

- `analysis/metrics.py`: parse `runs.json`; baseline = median of ≤7 prior
  successful runs; detectors: row-count ratio (default 0.5×/2×), null-ratio
  jump (Δ ≥ 0.2), distinct-count collapse (≤0.5×), duration surge (≥2×),
  zero rows. Emit `MetricAnomaly(metric, column?, observed, baseline,
  ratio, threshold, severity)`.
- Tests: each detector, no-baseline behavior (skip, not crash), determinism.
- Commit: `feat: detect run metric anomalies`.

## Task 6: Diagnosis engine and fix catalog

- `domain/diagnosis.py`: `RecommendedFix(fix_id, title, steps[])`,
  `Diagnosis(...)` per spec; `diagnosis/catalog.py`: the nine cause rules
  with fixes; `diagnosis/engine.py`: correlation, documented confidence
  weights (0.5/0.3/0.2, cap 0.95), affected tables/columns extraction,
  deterministic ordering, `unknown.needs_human` fallback.
- Tests: table-driven — each cause fires on a synthetic evidence set;
  confidence math; evidence refs always resolve; ordering stability.
- Commit: `feat: correlate evidence into ranked diagnoses`.

## Task 7: Report, analyze pipeline, API + persistence

- `reporting/report.py`: `IncidentReport` canonical JSON + Markdown.
- Orchestrator (`diagnosis/engine.py` or `analysis/run.py`): stage →
  parse all evidence → diagnose → report.
- Routes: `POST /api/incidents/{id}/analyze` (runs synchronously, persists
  `report.json` durably), `GET /api/incidents/{id}/report`.
- Tests: end-to-end API flow on a dirty fixture bundle; golden report JSON;
  graceful empty-bundle handling.
- Commit: `feat: produce persisted incident reports`.

## Task 8: CLI, packaging metadata, CI

- `cli.py`: `analyze <dir>` (offline, exit codes 0/1/3, `--format
  json|markdown`, `--output`), `serve` (loopback launcher with random
  token, mirrors workbench CLI).
- `.github/workflows/ci.yml`: 3-OS matrix — pytest, ruff, mypy.
- README with usage.
- Tests: CLI analyze on fixture bundle (subprocess-free, call main),
  exit codes, serve loopback validation.
- Commit: `feat: add copilot cli and ci`.

## Final verification

- Full pytest suite, `ruff check backend`, `mypy backend/src` clean.
- `pipeline-copilot analyze` on the dirty fixture bundle produces the
  golden report; re-running is byte-identical.
- No outbound network calls (grep for http clients in runtime paths).
