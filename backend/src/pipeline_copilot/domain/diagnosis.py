from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pipeline_copilot.domain.evidence import (
    LogSignatureHit,
    MetricAnomaly,
    SchemaDrift,
    Severity,
)


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    log_signatures: list[LogSignatureHit] = Field(default_factory=list)
    schema_drifts: list[SchemaDrift] = Field(default_factory=list)
    metric_anomalies: list[MetricAnomaly] = Field(default_factory=list)
    unparsed: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.log_signatures or self.schema_drifts or self.metric_anomalies
        )


class RecommendedFix(BaseModel):
    model_config = ConfigDict(frozen=True)

    fix_id: str
    title: str
    steps: list[str]


class Diagnosis(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    cause_id: str
    cause_version: int = Field(ge=1)
    title: str
    confidence: float = Field(ge=0.0, le=0.95)
    severity: Severity
    evidence_refs: list[str]
    recommended_fixes: list[RecommendedFix]
    affected: dict[str, list[str]]
