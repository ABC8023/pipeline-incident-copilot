# Pipeline Incident Copilot — Design Specification

**Date:** 2026-07-15
**Status:** Approved for implementation
**Repository:** `work/pipeline-incident-copilot`

## Problem

When a data pipeline run fails, an engineer must correlate three evidence
sources by hand: run logs (what crashed, and was it the root event or a
cascade), schema snapshots (did an upstream contract change), and run metrics
(did data volume or quality shift even when nothing crashed). The copilot
automates that triage: it ingests an incident bundle, extracts typed evidence
from each source, correlates the evidence into ranked probable causes, and
recommends concrete fixes — deterministically, offline, with every conclusion
traceable to the evidence that produced it.

## Goals

1. Diagnose a failed (or silently degraded) pipeline run from local artifacts
   only: log files, schema snapshots, and run-metric records.
2. Produce ranked, typed diagnoses with confidence scores, the exact evidence
   behind each, and recommended fixes from a versioned catalog.
3. Be usable both as a CLI (in CI or on-call terminals) and as an
   authenticated local HTTP API.
4. Deterministic output: identical bundles produce identical reports.

## Non-goals (v1)

- No browser UI (CLI + API only).
- No live connections to schedulers, warehouses, or log services; evidence
  arrives as files.
- No automatic remediation; the copilot recommends, humans act.
- No LLM/AI calls; the diagnosis engine is a deterministic rule system.

## Inputs: the incident bundle

An incident is a directory (or upload set) containing any subset of:

| File | Format | Content |
| --- | --- | --- |
| `logs/*.log`, `logs/*.txt`, `logs/*.jsonl` | text or JSON lines | run logs, any mix of structured and unstructured lines |
| `schema/before.json`, `schema/after.json` | JSON | column name → type/nullable snapshots (optionally nested per table) |
| `metrics/runs.json` | JSON | per-run records: run id, started_at, status, row counts and per-column metrics |
| `manifest.json` | JSON | optional: pipeline name, failed run id, timezone |

Every input is staged immutably (byte-for-byte copy, SHA-256 fingerprint)
before analysis, mirroring the Data Cleanup Workbench staging contract.
Bundles are capped at 512 MB total and 64 MB per log file.

## Analysis engines

### 1. Log analysis (`analysis/logs.py`)

- Tolerant line parser: extracts timestamp (several formats), level, and
  message from structured (JSON) and unstructured lines; unparseable lines
  keep raw text with `level=unknown`.
- **Signature catalog**: versioned, typed rules (regex + metadata) that map
  error text to `LogSignature` hits — out-of-memory, disk-full, permission
  denied, connection refused/reset/timeout, SQL syntax error, missing
  column/table, type-cast failure, dependency/task failure, retry exhaustion,
  OOM-killed exit codes.
- **Root-event selection**: the earliest ERROR/FATAL hit whose signature is
  not a known *cascade* signature (e.g. "upstream task failed") is the root
  candidate; later errors within the cascade window attach to it as
  `cascaded_from` evidence.

### 2. Schema drift (`analysis/schema_drift.py`)

- Diff `before` vs `after` snapshots per table: added, removed, renamed
  (same type + high name similarity), type-changed, nullability-changed
  columns.
- Each drift is a typed `SchemaDrift` finding with severity: removals and
  type narrowing are `error`, additions and widening `info`, renames
  `warning`.

### 3. Metric anomalies (`analysis/metrics.py`)

- Compare the failed/target run against a baseline (median of up to the N=7
  preceding successful runs).
- Detectors: row-count collapse or surge (|Δ| beyond ratio threshold),
  null-ratio jump per column, distinct-count collapse per column, run-duration
  surge, and zero-rows-processed.
- Each anomaly is a typed `MetricAnomaly` with observed value, baseline,
  ratio, and threshold.

## Diagnosis engine (`diagnosis/engine.py`)

A versioned rule table correlates evidence kinds into `Diagnosis` records:

```
Diagnosis(id, cause_id, cause_version, title, confidence, severity,
          evidence_refs[], recommended_fixes[], affected: {tables, columns})
```

Correlation rules (initial catalog, each with documented confidence math):

| cause_id | fires when | example fix |
| --- | --- | --- |
| `schema.upstream_contract_change` | missing-column log signature AND matching removed/renamed drift | restore or alias the column; pin upstream contract |
| `schema.type_mismatch` | cast-failure signature AND type-changed drift on the same column | add explicit cast; coordinate type migration |
| `resources.out_of_memory` | OOM signature (optionally + row-count surge) | raise memory limit; partition input |
| `resources.disk_full` | disk-full signature | clear temp storage; expand volume |
| `connectivity.transient` | connection signature AND retry exhaustion | retry with backoff; check endpoint health |
| `access.permission_revoked` | permission-denied signature | restore grant / rotate credential |
| `data.volume_anomaly` | row-count anomaly without a crash signature | validate upstream extract; add volume guard |
| `data.quality_regression` | null-ratio or distinct-count anomaly | quarantine + upstream fix; add quality gate |
| `unknown.needs_human` | evidence exists but no rule matched | triage checklist (always emitted last, confidence 0.1) |

Confidence combines evidence weights (log signature 0.5, corroborating drift
or metric 0.3, cascade consistency 0.2) and is capped at 0.95 — the copilot
never claims certainty. Diagnoses sort by confidence desc, severity desc,
cause_id. Every diagnosis lists the evidence ids it used; evidence never
appears from thin air.

## Interfaces

### CLI

```
pipeline-copilot analyze <bundle-dir> [--format json|markdown] [--output PATH]
pipeline-copilot serve [--host 127.0.0.1] [--port N]
```

`analyze` runs offline and prints the report; exit code 0 (diagnoses found),
3 (no evidence at all), 1 (invalid bundle).

### HTTP API (loopback + `X-Session-Token`, same contract as the workbench)

- `POST /api/incidents` — multipart-free: tar/zip is out of scope; the client
  uploads files one at a time to `POST /api/incidents/{id}/files?kind=log|schema_before|schema_after|metrics&filename=...`
  after `POST /api/incidents` creates the incident.
- `POST /api/incidents/{id}/analyze` — runs analysis, persists
  `report.json` durably, returns the report.
- `GET /api/incidents/{id}/report` — returns the persisted report.
- `GET /api/health`.

### Report

`IncidentReport(incident_id, bundle_fingerprint, generated_by, evidence:
{log_signatures[], schema_drifts[], metric_anomalies[]}, diagnoses[],
summary)` serialized as canonical sorted JSON plus a Markdown rendering.

## Constraints

- Python 3.12–3.14, FastAPI, Pydantic v2, pytest; exact lockfile committed.
- Loopback only; random session token; no outbound network calls at runtime.
- Deterministic: no wall-clock in analysis output (timestamps come from
  evidence, not from the copilot).
- Log files stream line-by-line; nothing loads whole files above 64 MB.
- All parsing failures degrade gracefully into typed `unparsed` evidence —
  a malformed bundle still yields a report explaining what was unusable.
- Tests-first per task; ruff + mypy clean on production source.
