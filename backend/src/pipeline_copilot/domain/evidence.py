from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning", "error"]


class LogLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    file: str
    line_number: int = Field(ge=1)
    timestamp: str | None
    level: Literal["debug", "info", "warning", "error", "fatal", "unknown"]
    message: str


class LogSignatureHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    rule_id: str
    rule_version: int = Field(ge=1)
    category: str
    severity: Severity
    file: str
    line_number: int = Field(ge=1)
    timestamp: str | None
    excerpt: str
    matched: dict[str, str]
    is_cascade: bool
    is_root_candidate: bool = False
    cascaded_from: str | None = None


class SchemaDrift(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal[
        "added", "removed", "renamed", "type_changed", "nullability_changed"
    ]
    table: str
    column: str
    before: dict[str, object] | None
    after: dict[str, object] | None
    severity: Severity


class MetricAnomaly(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    metric: str
    column: str | None
    observed: float
    baseline: float | None
    ratio: float | None
    threshold: float
    severity: Severity
