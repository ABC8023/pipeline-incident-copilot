from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pipeline_copilot.analysis.metrics import (
    MalformedMetrics,
    detect_anomalies_from_file,
)
from pipeline_copilot.analysis.schema_drift import MalformedSnapshot, detect_drift
from pipeline_copilot.analysis.signatures import scan_logs
from pipeline_copilot.diagnosis.engine import diagnose
from pipeline_copilot.domain.diagnosis import Diagnosis, EvidenceBundle

APP_VERSION = "0.1.0"
GENERATED_BY = f"pipeline-incident-copilot/{APP_VERSION}"


class IncidentReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident_id: str
    pipeline: str
    bundle_fingerprint: str
    generated_by: str = GENERATED_BY
    evidence: EvidenceBundle
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    summary: str


def collect_evidence(
    log_paths: list[Path],
    schema_before: Path | None,
    schema_after: Path | None,
    metrics_path: Path | None,
    target_run_id: str | None = None,
) -> EvidenceBundle:
    unparsed: list[str] = []
    log_signatures = scan_logs(log_paths)
    schema_drifts = []
    if schema_before is not None and schema_after is not None:
        try:
            schema_drifts = detect_drift(schema_before, schema_after)
        except MalformedSnapshot as error:
            unparsed.append(f"schema: {error}")
    elif schema_before is not None or schema_after is not None:
        unparsed.append("schema: need both before and after snapshots to diff")
    metric_anomalies = []
    if metrics_path is not None:
        try:
            metric_anomalies = detect_anomalies_from_file(
                metrics_path, target_run_id
            )
        except MalformedMetrics as error:
            unparsed.append(f"metrics: {error}")
    return EvidenceBundle(
        log_signatures=log_signatures,
        schema_drifts=schema_drifts,
        metric_anomalies=metric_anomalies,
        unparsed=unparsed,
    )


def _summarize(evidence: EvidenceBundle, diagnoses: list[Diagnosis]) -> str:
    if evidence.is_empty() and not evidence.unparsed:
        return "No evidence found in the bundle; nothing to diagnose."
    counts = (
        f"{len(evidence.log_signatures)} log signature(s),"
        f" {len(evidence.schema_drifts)} schema drift(s),"
        f" {len(evidence.metric_anomalies)} metric anomaly(ies)"
    )
    if not diagnoses:
        return f"Evidence collected ({counts}) but no diagnosis produced."
    top = diagnoses[0]
    return (
        f"{len(diagnoses)} diagnosis(es) from {counts}."
        f" Most likely: {top.title}"
        f" ({round(top.confidence * 100)}% confidence)."
    )


def build_report(
    incident_id: str,
    pipeline: str,
    bundle_fingerprint: str,
    evidence: EvidenceBundle,
) -> IncidentReport:
    diagnoses = diagnose(evidence)
    return IncidentReport(
        incident_id=incident_id,
        pipeline=pipeline,
        bundle_fingerprint=bundle_fingerprint,
        evidence=evidence,
        diagnoses=diagnoses,
        summary=_summarize(evidence, diagnoses),
    )


def render_json(report: IncidentReport) -> str:
    return (
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n"
    )


def render_markdown(report: IncidentReport) -> str:
    lines = [
        f"# Incident report — {report.pipeline}",
        "",
        report.summary,
        "",
    ]
    for rank, diagnosis in enumerate(report.diagnoses, start=1):
        lines.append(
            f"## {rank}. {diagnosis.title}"
            f" ({round(diagnosis.confidence * 100)}%, {diagnosis.severity})"
        )
        if diagnosis.affected["columns"] or diagnosis.affected["tables"]:
            affected = ", ".join(
                [*diagnosis.affected["tables"], *diagnosis.affected["columns"]]
            )
            lines.append(f"Affected: {affected}")
        lines.append(f"Evidence: {', '.join(diagnosis.evidence_refs)}")
        for fix in diagnosis.recommended_fixes:
            lines.append(f"### Fix: {fix.title}")
            lines.extend(f"1. {step}" for step in fix.steps)
        lines.append("")
    if report.evidence.unparsed:
        lines.append("## Unusable inputs")
        lines.extend(f"- {note}" for note in report.evidence.unparsed)
        lines.append("")
    return "\n".join(lines)
