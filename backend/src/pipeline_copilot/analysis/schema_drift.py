from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pipeline_copilot.domain.evidence import SchemaDrift, Severity

RENAME_SIMILARITY = 0.8
DEFAULT_TABLE = "default"
# Type changes that only widen capacity are informational.
_WIDENINGS = {
    ("int32", "int64"),
    ("int", "bigint"),
    ("integer", "bigint"),
    ("float", "double"),
    ("varchar", "text"),
}


class MalformedSnapshot(ValueError):
    """Raised when a schema snapshot is not in a supported shape."""


Column = dict[str, Any]
Table = dict[str, Column]


def _normalize_column(name: str, value: Any) -> Column:
    if isinstance(value, str):
        return {"type": value.lower(), "nullable": True}
    if isinstance(value, dict):
        raw_type = value.get("type")
        if not isinstance(raw_type, str):
            raise MalformedSnapshot(f"column {name!r} is missing a type")
        return {
            "type": raw_type.lower(),
            "nullable": bool(value.get("nullable", True)),
        }
    raise MalformedSnapshot(f"column {name!r} has an unsupported shape")


def parse_snapshot(path: Path) -> dict[str, Table]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise MalformedSnapshot(f"snapshot is not valid JSON: {error}") from error
    if not isinstance(raw, dict) or not raw:
        raise MalformedSnapshot("snapshot must be a non-empty JSON object")
    values = list(raw.values())
    # Nested shape maps table -> {column: spec}; flat shape maps
    # column -> type-string or {"type": ..., "nullable": ...} spec.
    if all(
        isinstance(value, dict) and "type" not in value for value in values
    ):
        tables = raw
    else:
        tables = {DEFAULT_TABLE: raw}
    normalized: dict[str, Table] = {}
    for table, columns in tables.items():
        if not isinstance(columns, dict):
            raise MalformedSnapshot(f"table {table!r} must map columns")
        normalized[str(table)] = {
            str(name): _normalize_column(str(name), value)
            for name, value in columns.items()
        }
    return normalized


def _drift_id(kind: str, table: str, column: str) -> str:
    material = f"{kind}|{table}|{column}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _type_change_severity(before: str, after: str) -> Severity:
    if (before, after) in _WIDENINGS:
        return "info"
    return "error"


def diff_snapshots(
    before: dict[str, Table], after: dict[str, Table]
) -> list[SchemaDrift]:
    drifts: list[SchemaDrift] = []
    for table in sorted(set(before) | set(after)):
        old = before.get(table, {})
        new = after.get(table, {})
        removed = sorted(set(old) - set(new))
        added = sorted(set(new) - set(old))
        renamed: set[str] = set()
        for gone in removed:
            candidates = [
                name
                for name in added
                if name not in renamed
                and new[name]["type"] == old[gone]["type"]
                and difflib.SequenceMatcher(
                    None, _normalized_name(gone), _normalized_name(name)
                ).ratio()
                >= RENAME_SIMILARITY
            ]
            if candidates:
                target = candidates[0]
                renamed.add(target)
                drifts.append(
                    SchemaDrift(
                        id=_drift_id("renamed", table, gone),
                        kind="renamed",
                        table=table,
                        column=gone,
                        before=old[gone],
                        after={**new[target], "renamed_to": target},
                        severity="warning",
                    )
                )
        for gone in removed:
            if any(
                drift.kind == "renamed"
                and drift.table == table
                and drift.column == gone
                for drift in drifts
            ):
                continue
            drifts.append(
                SchemaDrift(
                    id=_drift_id("removed", table, gone),
                    kind="removed",
                    table=table,
                    column=gone,
                    before=old[gone],
                    after=None,
                    severity="error",
                )
            )
        for arrived in added:
            if arrived in renamed:
                continue
            drifts.append(
                SchemaDrift(
                    id=_drift_id("added", table, arrived),
                    kind="added",
                    table=table,
                    column=arrived,
                    before=None,
                    after=new[arrived],
                    severity="info",
                )
            )
        for name in sorted(set(old) & set(new)):
            if old[name]["type"] != new[name]["type"]:
                drifts.append(
                    SchemaDrift(
                        id=_drift_id("type_changed", table, name),
                        kind="type_changed",
                        table=table,
                        column=name,
                        before=old[name],
                        after=new[name],
                        severity=_type_change_severity(
                            str(old[name]["type"]), str(new[name]["type"])
                        ),
                    )
                )
            elif old[name]["nullable"] != new[name]["nullable"]:
                drifts.append(
                    SchemaDrift(
                        id=_drift_id("nullability_changed", table, name),
                        kind="nullability_changed",
                        table=table,
                        column=name,
                        before=old[name],
                        after=new[name],
                        severity="warning",
                    )
                )
    return sorted(drifts, key=lambda drift: (drift.table, drift.column, drift.kind))


def detect_drift(before_path: Path, after_path: Path) -> list[SchemaDrift]:
    return diff_snapshots(parse_snapshot(before_path), parse_snapshot(after_path))
