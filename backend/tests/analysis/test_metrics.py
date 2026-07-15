from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_copilot.analysis.metrics import (
    MalformedMetrics,
    detect_anomalies,
    detect_anomalies_from_file,
    parse_runs,
)


def baseline_run(index: int) -> dict[str, object]:
    return {
        "run_id": f"run-{index}",
        "status": "success",
        "row_count": 1000,
        "duration_seconds": 60,
        "columns": {
            "email": {"null_ratio": 0.01, "distinct_count": 900},
        },
    }


BASELINE = [baseline_run(index) for index in range(5)]


def test_detects_each_anomaly_kind() -> None:
    target = {
        "run_id": "run-9",
        "status": "failed",
        "row_count": 300,
        "duration_seconds": 200,
        "columns": {"email": {"null_ratio": 0.4, "distinct_count": 100}},
    }

    anomalies = detect_anomalies([*BASELINE, target])

    by_metric = {
        (anomaly.metric, anomaly.column): anomaly for anomaly in anomalies
    }
    rows = by_metric[("row_count", None)]
    assert rows.baseline == 1000 and rows.ratio == 0.3
    assert by_metric[("duration", None)].ratio == pytest.approx(200 / 60, rel=1e-3)
    nulls = by_metric[("null_ratio", "email")]
    assert nulls.observed == 0.4 and nulls.threshold == 0.2
    assert by_metric[("distinct_count", "email")].ratio == pytest.approx(
        100 / 900, abs=1e-4
    )


def test_zero_rows_and_surge() -> None:
    zero = detect_anomalies(
        [*BASELINE, {"run_id": "z", "status": "failed", "row_count": 0}]
    )
    surge = detect_anomalies(
        [*BASELINE, {"run_id": "s", "status": "success", "row_count": 5000}]
    )

    assert any(
        anomaly.metric == "zero_rows" and anomaly.severity == "error"
        for anomaly in zero
    )
    assert any(
        anomaly.metric == "row_count" and anomaly.threshold == 2.0
        for anomaly in surge
    )


def test_no_baseline_means_no_ratio_anomalies() -> None:
    anomalies = detect_anomalies(
        [{"run_id": "only", "status": "failed", "row_count": 5}]
    )

    assert [anomaly.metric for anomaly in anomalies] == []


def test_target_selection_and_failed_runs_excluded_from_baseline() -> None:
    failed_middle = {
        "run_id": "bad",
        "status": "failed",
        "row_count": 1,
    }
    target = {"run_id": "target", "status": "failed", "row_count": 400}

    anomalies = detect_anomalies(
        [*BASELINE, failed_middle, target], target_run_id="target"
    )

    rows = next(anomaly for anomaly in anomalies if anomaly.metric == "row_count")
    # Baseline is the median of successful runs only, so the failed run's
    # row_count=1 never drags it down.
    assert rows.baseline == 1000

    with pytest.raises(MalformedMetrics, match="not found"):
        detect_anomalies(BASELINE, target_run_id="missing")


def test_parse_runs_validates_shape(tmp_path: Path) -> None:
    good = tmp_path / "runs.json"
    good.write_text(json.dumps({"runs": BASELINE}), encoding="utf-8")
    assert len(parse_runs(good)) == 5
    assert detect_anomalies_from_file(good) == []

    broken = tmp_path / "broken.json"
    broken.write_text("[{}]", encoding="utf-8")
    with pytest.raises(MalformedMetrics, match="run_id"):
        parse_runs(broken)
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(MalformedMetrics, match="non-empty"):
        parse_runs(empty)
