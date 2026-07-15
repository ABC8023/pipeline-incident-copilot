from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_copilot.analysis.schema_drift import (
    MalformedSnapshot,
    detect_drift,
    diff_snapshots,
    parse_snapshot,
)


def snapshot(tmp_path: Path, name: str, payload: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_detects_every_drift_kind(tmp_path: Path) -> None:
    before = snapshot(
        tmp_path,
        "before.json",
        {
            "customer_id": "varchar",
            "amount": {"type": "int32", "nullable": False},
            "signup_ts": "timestamp",
            "full_name": "varchar",
            "notes": {"type": "varchar", "nullable": False},
        },
    )
    after = snapshot(
        tmp_path,
        "after.json",
        {
            "customer_id": "varchar",
            "amount": {"type": "int64", "nullable": False},
            "signup_ts": "varchar",
            "fullname": "varchar",
            "notes": {"type": "varchar", "nullable": True},
            "loyalty_tier": "varchar",
        },
    )

    drifts = detect_drift(before, after)

    by_column = {(drift.column, drift.kind): drift for drift in drifts}
    assert by_column[("amount", "type_changed")].severity == "info"
    assert by_column[("signup_ts", "type_changed")].severity == "error"
    renamed = by_column[("full_name", "renamed")]
    assert renamed.severity == "warning"
    assert renamed.after is not None
    assert renamed.after["renamed_to"] == "fullname"
    assert by_column[("notes", "nullability_changed")].severity == "warning"
    assert by_column[("loyalty_tier", "added")].severity == "info"
    assert ("full_name", "removed") not in by_column


def test_removed_column_without_rename_candidate_is_error(
    tmp_path: Path,
) -> None:
    before = snapshot(tmp_path, "b.json", {"legacy_flag": "boolean"})
    after = snapshot(tmp_path, "a.json", {"other": "varchar"})

    drifts = detect_drift(before, after)

    kinds = {(drift.column, drift.kind) for drift in drifts}
    assert ("legacy_flag", "removed") in kinds
    assert ("other", "added") in kinds
    removed = next(drift for drift in drifts if drift.kind == "removed")
    assert removed.severity == "error"


def test_nested_table_snapshots(tmp_path: Path) -> None:
    before = snapshot(
        tmp_path,
        "b.json",
        {"orders": {"id": "int64"}, "customers": {"id": "int64"}},
    )
    after = snapshot(
        tmp_path,
        "a.json",
        {"orders": {"id": "int64"}, "customers": {"id": "varchar"}},
    )

    drifts = detect_drift(before, after)

    assert len(drifts) == 1
    assert (drifts[0].table, drifts[0].column) == ("customers", "id")
    assert drifts[0].kind == "type_changed"


def test_malformed_snapshots_raise_typed_errors(tmp_path: Path) -> None:
    not_json = tmp_path / "broken.json"
    not_json.write_text("{nope", encoding="utf-8")
    empty = snapshot(tmp_path, "empty.json", {})
    bad_column = snapshot(tmp_path, "bad.json", {"col": 42})

    with pytest.raises(MalformedSnapshot, match="valid JSON"):
        parse_snapshot(not_json)
    with pytest.raises(MalformedSnapshot, match="non-empty"):
        parse_snapshot(empty)
    with pytest.raises(MalformedSnapshot, match="unsupported shape"):
        parse_snapshot(bad_column)


def test_diff_is_deterministic() -> None:
    before = {"default": {"a": {"type": "int64", "nullable": True}}}
    after = {"default": {"b": {"type": "varchar", "nullable": True}}}

    once = diff_snapshots(before, after)
    twice = diff_snapshots(before, after)

    assert [drift.model_dump() for drift in once] == [
        drift.model_dump() for drift in twice
    ]
