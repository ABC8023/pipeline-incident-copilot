from __future__ import annotations

import hashlib

from pipeline_copilot.diagnosis.catalog import FIXES, TITLES
from pipeline_copilot.domain.diagnosis import Diagnosis, EvidenceBundle
from pipeline_copilot.domain.evidence import LogSignatureHit, Severity

SIGNATURE_WEIGHT = 0.5
CORROBORATION_WEIGHT = 0.3
ROOT_CONSISTENCY_WEIGHT = 0.2
CONFIDENCE_CAP = 0.95
FALLBACK_CONFIDENCE = 0.1


def _diagnosis_id(cause_id: str, evidence_refs: list[str]) -> str:
    material = "|".join([cause_id, *sorted(evidence_refs)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _build(
    cause_id: str,
    severity: Severity,
    confidence: float,
    evidence_refs: list[str],
    affected: dict[str, list[str]] | None = None,
) -> Diagnosis:
    refs = sorted(set(evidence_refs))
    return Diagnosis(
        id=_diagnosis_id(cause_id, refs),
        cause_id=cause_id,
        cause_version=1,
        title=TITLES[cause_id],
        confidence=round(min(confidence, CONFIDENCE_CAP), 4),
        severity=severity,
        evidence_refs=refs,
        recommended_fixes=FIXES[cause_id],
        affected=affected or {"tables": [], "columns": []},
    )


def _confidence(hit: LogSignatureHit, corroborated: bool) -> float:
    confidence = SIGNATURE_WEIGHT
    if corroborated:
        confidence += CORROBORATION_WEIGHT
    if hit.is_root_candidate:
        confidence += ROOT_CONSISTENCY_WEIGHT
    return confidence


def _hits(bundle: EvidenceBundle, rule_id: str) -> list[LogSignatureHit]:
    return [hit for hit in bundle.log_signatures if hit.rule_id == rule_id]


def diagnose(bundle: EvidenceBundle) -> list[Diagnosis]:
    diagnoses: list[Diagnosis] = []

    missing = _hits(bundle, "schema.missing_column") + _hits(
        bundle, "schema.missing_table"
    )
    if missing:
        hit = missing[0]
        named = set(hit.matched.values())
        drifts = [
            drift
            for drift in bundle.schema_drifts
            if drift.kind in {"removed", "renamed"}
            and (not named or drift.column in named or drift.table in named)
        ]
        refs = [item.id for item in missing] + [drift.id for drift in drifts]
        diagnoses.append(
            _build(
                "schema.upstream_contract_change",
                "error",
                _confidence(hit, bool(drifts)),
                refs,
                {
                    "tables": sorted({drift.table for drift in drifts}),
                    "columns": sorted(
                        {drift.column for drift in drifts} | named
                    ),
                },
            )
        )

    casts = _hits(bundle, "schema.cast_failure")
    if casts:
        hit = casts[0]
        drifts = [
            drift
            for drift in bundle.schema_drifts
            if drift.kind == "type_changed"
        ]
        diagnoses.append(
            _build(
                "schema.type_mismatch",
                "error",
                _confidence(hit, bool(drifts)),
                [item.id for item in casts] + [drift.id for drift in drifts],
                {
                    "tables": sorted({drift.table for drift in drifts}),
                    "columns": sorted({drift.column for drift in drifts}),
                },
            )
        )

    oom = _hits(bundle, "resources.out_of_memory")
    if oom:
        surges = [
            anomaly
            for anomaly in bundle.metric_anomalies
            if anomaly.metric == "row_count"
            and anomaly.ratio is not None
            and anomaly.ratio >= 1
        ]
        diagnoses.append(
            _build(
                "resources.out_of_memory",
                "error",
                _confidence(oom[0], bool(surges)),
                [item.id for item in oom] + [item.id for item in surges],
            )
        )

    disk = _hits(bundle, "resources.disk_full")
    if disk:
        diagnoses.append(
            _build(
                "resources.disk_full",
                "error",
                _confidence(disk[0], False),
                [item.id for item in disk],
            )
        )

    connection = _hits(bundle, "connectivity.connection_failed")
    if connection:
        retries = _hits(bundle, "connectivity.retry_exhausted")
        diagnoses.append(
            _build(
                "connectivity.transient",
                "error",
                _confidence(connection[0], bool(retries)),
                [item.id for item in connection + retries],
            )
        )

    denied = _hits(bundle, "access.permission_denied")
    if denied:
        diagnoses.append(
            _build(
                "access.permission_revoked",
                "error",
                _confidence(denied[0], False),
                [item.id for item in denied],
            )
        )

    crash_signatures = [
        hit for hit in bundle.log_signatures if not hit.is_cascade
    ]
    volume = [
        anomaly
        for anomaly in bundle.metric_anomalies
        if anomaly.metric in {"row_count", "zero_rows"}
    ]
    if volume and not crash_signatures:
        diagnoses.append(
            _build(
                "data.volume_anomaly",
                "warning",
                SIGNATURE_WEIGHT + CORROBORATION_WEIGHT
                if len(volume) > 1
                else SIGNATURE_WEIGHT,
                [item.id for item in volume],
            )
        )

    quality = [
        anomaly
        for anomaly in bundle.metric_anomalies
        if anomaly.metric in {"null_ratio", "distinct_count"}
    ]
    if quality:
        diagnoses.append(
            _build(
                "data.quality_regression",
                "warning",
                SIGNATURE_WEIGHT + CORROBORATION_WEIGHT
                if len(quality) > 1
                else SIGNATURE_WEIGHT,
                [item.id for item in quality],
                {
                    "tables": [],
                    "columns": sorted(
                        {
                            anomaly.column
                            for anomaly in quality
                            if anomaly.column is not None
                        }
                    ),
                },
            )
        )

    if not diagnoses and not bundle.is_empty():
        all_refs = (
            [hit.id for hit in bundle.log_signatures]
            + [drift.id for drift in bundle.schema_drifts]
            + [anomaly.id for anomaly in bundle.metric_anomalies]
        )
        diagnoses.append(
            _build(
                "unknown.needs_human", "warning", FALLBACK_CONFIDENCE, all_refs
            )
        )

    return sorted(
        diagnoses,
        key=lambda diagnosis: (
            -diagnosis.confidence,
            {"error": 0, "warning": 1, "info": 2}[diagnosis.severity],
            diagnosis.cause_id,
        ),
    )
