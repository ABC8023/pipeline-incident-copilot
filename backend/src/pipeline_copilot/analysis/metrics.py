from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from pipeline_copilot.domain.evidence import MetricAnomaly

BASELINE_RUNS = 7
ROW_RATIO_LOW = 0.5
ROW_RATIO_HIGH = 2.0
NULL_RATIO_JUMP = 0.2
DISTINCT_COLLAPSE_RATIO = 0.5
DURATION_SURGE_RATIO = 2.0


class MalformedMetrics(ValueError):
    """Raised when runs.json is not in a supported shape."""


Run = dict[str, Any]


def parse_runs(path: Path) -> list[Run]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise MalformedMetrics(f"metrics are not valid JSON: {error}") from error
    if isinstance(raw, dict):
        raw = raw.get("runs")
    if not isinstance(raw, list) or not raw:
        raise MalformedMetrics("metrics must contain a non-empty run list")
    runs: list[Run] = []
    for record in raw:
        if not isinstance(record, dict) or "run_id" not in record:
            raise MalformedMetrics("every run needs at least a run_id")
        runs.append(record)
    return runs


def _anomaly_id(metric: str, column: str | None) -> str:
    material = f"{metric}|{column or ''}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _target_and_baseline(
    runs: list[Run], target_run_id: str | None
) -> tuple[Run, list[Run]]:
    if target_run_id is None:
        target = runs[-1]
    else:
        matches = [run for run in runs if run.get("run_id") == target_run_id]
        if not matches:
            raise MalformedMetrics(f"run {target_run_id!r} not found")
        target = matches[0]
    target_index = runs.index(target)
    baseline = [
        run
        for run in runs[:target_index]
        if str(run.get("status", "")).lower() in {"success", "succeeded"}
    ][-BASELINE_RUNS:]
    return target, baseline


def detect_anomalies(
    runs: list[Run], target_run_id: str | None = None
) -> list[MetricAnomaly]:
    target, baseline = _target_and_baseline(runs, target_run_id)
    anomalies: list[MetricAnomaly] = []

    observed_rows = target.get("row_count")
    if observed_rows is not None:
        observed = float(observed_rows)
        if observed == 0:
            anomalies.append(
                MetricAnomaly(
                    id=_anomaly_id("zero_rows", None),
                    metric="zero_rows",
                    column=None,
                    observed=0.0,
                    baseline=None,
                    ratio=None,
                    threshold=0.0,
                    severity="error",
                )
            )
        baseline_rows = _median(
            [
                float(run["row_count"])
                for run in baseline
                if run.get("row_count") is not None
            ]
        )
        if baseline_rows and observed > 0:
            ratio = observed / baseline_rows
            if ratio <= ROW_RATIO_LOW or ratio >= ROW_RATIO_HIGH:
                anomalies.append(
                    MetricAnomaly(
                        id=_anomaly_id("row_count", None),
                        metric="row_count",
                        column=None,
                        observed=observed,
                        baseline=baseline_rows,
                        ratio=round(ratio, 4),
                        threshold=(
                            ROW_RATIO_LOW if ratio <= ROW_RATIO_LOW else ROW_RATIO_HIGH
                        ),
                        severity="warning",
                    )
                )

    duration = target.get("duration_seconds")
    baseline_duration = _median(
        [
            float(run["duration_seconds"])
            for run in baseline
            if run.get("duration_seconds") is not None
        ]
    )
    if duration is not None and baseline_duration:
        ratio = float(duration) / baseline_duration
        if ratio >= DURATION_SURGE_RATIO:
            anomalies.append(
                MetricAnomaly(
                    id=_anomaly_id("duration", None),
                    metric="duration",
                    column=None,
                    observed=float(duration),
                    baseline=baseline_duration,
                    ratio=round(ratio, 4),
                    threshold=DURATION_SURGE_RATIO,
                    severity="warning",
                )
            )

    target_columns = target.get("columns") or {}
    for column in sorted(target_columns):
        stats = target_columns[column] or {}
        null_ratio = stats.get("null_ratio")
        baseline_null = _median(
            [
                float((run.get("columns") or {}).get(column, {}).get("null_ratio"))
                for run in baseline
                if (run.get("columns") or {}).get(column, {}).get("null_ratio")
                is not None
            ]
        )
        if null_ratio is not None and baseline_null is not None:
            jump = float(null_ratio) - baseline_null
            if jump >= NULL_RATIO_JUMP:
                anomalies.append(
                    MetricAnomaly(
                        id=_anomaly_id("null_ratio", column),
                        metric="null_ratio",
                        column=column,
                        observed=float(null_ratio),
                        baseline=baseline_null,
                        ratio=round(jump, 4),
                        threshold=NULL_RATIO_JUMP,
                        severity="warning",
                    )
                )
        distinct = stats.get("distinct_count")
        baseline_distinct = _median(
            [
                float(
                    (run.get("columns") or {}).get(column, {}).get("distinct_count")
                )
                for run in baseline
                if (run.get("columns") or {}).get(column, {}).get("distinct_count")
                is not None
            ]
        )
        if distinct is not None and baseline_distinct:
            ratio = float(distinct) / baseline_distinct
            if ratio <= DISTINCT_COLLAPSE_RATIO:
                anomalies.append(
                    MetricAnomaly(
                        id=_anomaly_id("distinct_count", column),
                        metric="distinct_count",
                        column=column,
                        observed=float(distinct),
                        baseline=baseline_distinct,
                        ratio=round(ratio, 4),
                        threshold=DISTINCT_COLLAPSE_RATIO,
                        severity="warning",
                    )
                )
    return anomalies


def detect_anomalies_from_file(
    path: Path, target_run_id: str | None = None
) -> list[MetricAnomaly]:
    return detect_anomalies(parse_runs(path), target_run_id)
