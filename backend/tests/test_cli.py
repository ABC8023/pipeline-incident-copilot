from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_copilot.cli import (
    EXIT_INVALID_BUNDLE,
    EXIT_NO_EVIDENCE,
    EXIT_OK,
    analyze_directory,
    main,
)


def dirty_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "orders-incident"
    (bundle / "logs").mkdir(parents=True)
    (bundle / "schema").mkdir()
    (bundle / "logs" / "run.log").write_text(
        '2026-07-15T00:00:02Z ERROR column "customer_id" does not exist\n',
        encoding="utf-8",
    )
    (bundle / "schema" / "before.json").write_text(
        json.dumps({"customer_id": "varchar"}), encoding="utf-8"
    )
    (bundle / "schema" / "after.json").write_text(
        json.dumps({"customer_ref": "varchar"}), encoding="utf-8"
    )
    return bundle


def test_analyze_renders_markdown_and_json(tmp_path: Path) -> None:
    bundle = dirty_bundle(tmp_path)

    code, markdown = analyze_directory(bundle, "markdown")
    json_code, rendered = analyze_directory(bundle, "json")

    assert code == EXIT_OK and json_code == EXIT_OK
    assert "Upstream schema contract change" in markdown
    assert "Restore or alias the missing column" in markdown
    report = json.loads(rendered)
    assert report["diagnoses"][0]["cause_id"] == "schema.upstream_contract_change"
    assert report["diagnoses"][0]["confidence"] == 0.95

    repeat_code, repeat = analyze_directory(bundle, "json")
    assert (repeat_code, repeat) == (json_code, rendered)


def test_exit_codes(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    missing_code, message = analyze_directory(tmp_path / "nope", "json")
    empty_code, _ = analyze_directory(empty, "json")

    assert missing_code == EXIT_INVALID_BUNDLE
    assert "not found" in message
    assert empty_code == EXIT_NO_EVIDENCE


def test_main_writes_output_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = dirty_bundle(tmp_path)
    output = tmp_path / "report.json"

    code = main(
        ["analyze", str(bundle), "--format", "json", "--output", str(output)]
    )

    assert code == EXIT_OK
    assert json.loads(output.read_text(encoding="utf-8"))["diagnoses"]
    assert "schema.upstream_contract_change" in capsys.readouterr().out
