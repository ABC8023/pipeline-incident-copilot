from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_copilot.analysis.log_parser import parse_log
from pipeline_copilot.analysis.signatures import CATALOG, scan_logs


def write_log(tmp_path: Path, name: str, *lines: str) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_parser_handles_text_json_and_garbage(tmp_path: Path) -> None:
    path = write_log(
        tmp_path,
        "mixed.log",
        "2026-07-15T01:02:03Z ERROR boom happened",
        json.dumps(
            {"timestamp": "2026-07-15T01:02:04Z", "level": "warn", "msg": "slow"}
        ),
        "\x00\x01 unstructured garbage without level",
        "",
        "2026-07-15 01:02:05 INFO done",
    )

    lines = list(parse_log(path))

    assert [line.level for line in lines] == ["error", "warning", "unknown", "info"]
    assert lines[0].timestamp == "2026-07-15T01:02:03Z"
    assert lines[1].message == "slow"
    assert lines[2].timestamp is None
    assert lines[3].line_number == 5


@pytest.mark.parametrize(
    ("rule_id", "line"),
    [
        ("resources.out_of_memory", "FATAL java.lang.OutOfMemoryError: heap"),
        ("resources.out_of_memory", "task killed: exit code 137"),
        ("resources.disk_full", "ERROR No space left on device"),
        ("access.permission_denied", "ERROR Permission denied for schema sales"),
        ("connectivity.connection_failed", "ERROR Connection refused by host db"),
        ("connectivity.retry_exhausted", "ERROR giving up after 5 attempts"),
        ("sql.syntax_error", 'ERROR syntax error at or near "FORM"'),
        ("schema.missing_column", 'ERROR column "customer_id" does not exist'),
        ("schema.missing_table", 'ERROR relation "orders_raw" does not exist'),
        ("schema.cast_failure", "ERROR cannot cast type text to integer"),
        ("cascade.upstream_failure", "WARN upstream task extract_orders failed"),
    ],
)
def test_each_signature_fires(tmp_path: Path, rule_id: str, line: str) -> None:
    path = write_log(tmp_path, "one.log", f"2026-07-15T00:00:01Z {line}")

    hits = scan_logs([path])

    assert any(hit.rule_id == rule_id for hit in hits)


def test_catalog_rule_ids_are_unique_and_versioned() -> None:
    ids = [rule.rule_id for rule in CATALOG]
    assert len(ids) == len(set(ids))
    assert all(rule.rule_version >= 1 for rule in CATALOG)


def test_root_event_selection_prefers_non_cascade(tmp_path: Path) -> None:
    path = write_log(
        tmp_path,
        "run.log",
        "2026-07-15T00:00:01Z WARN upstream task extract failed",
        '2026-07-15T00:00:02Z ERROR column "customer_id" does not exist',
        "2026-07-15T00:00:03Z ERROR Connection refused during cleanup",
    )

    hits = scan_logs([path])

    roots = [hit for hit in hits if hit.is_root_candidate]
    assert len(roots) == 1
    assert roots[0].rule_id == "schema.missing_column"
    assert roots[0].matched == {"column": "customer_id"}
    cascades = [hit for hit in hits if hit.cascaded_from == roots[0].id]
    assert {hit.rule_id for hit in cascades} == {
        "cascade.upstream_failure",
        "connectivity.connection_failed",
    }


def test_scan_is_deterministic_across_files(tmp_path: Path) -> None:
    first = write_log(tmp_path, "b.log", "ERROR Connection refused")
    second = write_log(tmp_path, "a.log", "ERROR No space left on device")

    once = scan_logs([first, second])
    twice = scan_logs([second, first])

    assert [hit.model_dump() for hit in once] == [
        hit.model_dump() for hit in twice
    ]
    assert once[0].file == "a.log"
