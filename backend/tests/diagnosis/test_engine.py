from __future__ import annotations

import pytest

from pipeline_copilot.diagnosis.engine import diagnose
from pipeline_copilot.domain.diagnosis import EvidenceBundle
from pipeline_copilot.domain.evidence import (
    LogSignatureHit,
    MetricAnomaly,
    SchemaDrift,
)


def hit(rule_id: str, *, root: bool = False, matched: dict[str, str] | None = None):
    return LogSignatureHit(
        id=f"hit-{rule_id}",
        rule_id=rule_id,
        rule_version=1,
        category=rule_id.split(".")[0],
        severity="error",
        file="run.log",
        line_number=1,
        timestamp=None,
        excerpt="…",
        matched=matched or {},
        is_cascade=rule_id.startswith("cascade."),
        is_root_candidate=root,
    )


def drift(kind: str, column: str) -> SchemaDrift:
    return SchemaDrift(
        id=f"drift-{kind}-{column}",
        kind=kind,  # type: ignore[arg-type]
        table="orders",
        column=column,
        before={"type": "varchar"},
        after=None if kind == "removed" else {"type": "int64"},
        severity="error",
    )


def anomaly(metric: str, column: str | None = None) -> MetricAnomaly:
    return MetricAnomaly(
        id=f"anomaly-{metric}-{column or 'run'}",
        metric=metric,
        column=column,
        observed=1.0,
        baseline=10.0,
        ratio=2.5 if metric == "row_count" else 0.3,
        threshold=2.0,
        severity="warning",
    )


def test_contract_change_correlates_log_and_drift() -> None:
    bundle = EvidenceBundle(
        log_signatures=[
            hit(
                "schema.missing_column",
                root=True,
                matched={"column": "customer_id"},
            )
        ],
        schema_drifts=[drift("removed", "customer_id")],
    )

    diagnoses = diagnose(bundle)

    top = diagnoses[0]
    assert top.cause_id == "schema.upstream_contract_change"
    assert top.confidence == 0.95
    assert set(top.evidence_refs) == {
        "hit-schema.missing_column",
        "drift-removed-customer_id",
    }
    assert top.affected["columns"] == ["customer_id"]
    assert top.recommended_fixes[0].fix_id == "restore_or_alias_column"


@pytest.mark.parametrize(
    ("rule_id", "cause_id"),
    [
        ("resources.out_of_memory", "resources.out_of_memory"),
        ("resources.disk_full", "resources.disk_full"),
        ("connectivity.connection_failed", "connectivity.transient"),
        ("access.permission_denied", "access.permission_revoked"),
        ("schema.cast_failure", "schema.type_mismatch"),
    ],
)
def test_single_signature_causes(rule_id: str, cause_id: str) -> None:
    diagnoses = diagnose(EvidenceBundle(log_signatures=[hit(rule_id, root=True)]))

    assert diagnoses[0].cause_id == cause_id
    # Signature (0.5) + root consistency (0.2), no corroboration.
    assert diagnoses[0].confidence == 0.7
    assert diagnoses[0].recommended_fixes


def test_corroboration_raises_confidence() -> None:
    lone = diagnose(
        EvidenceBundle(
            log_signatures=[hit("connectivity.connection_failed", root=True)]
        )
    )[0]
    corroborated = diagnose(
        EvidenceBundle(
            log_signatures=[
                hit("connectivity.connection_failed", root=True),
                hit("connectivity.retry_exhausted"),
            ]
        )
    )[0]

    assert lone.confidence == 0.7
    assert corroborated.confidence == 0.95
    assert "hit-connectivity.retry_exhausted" in corroborated.evidence_refs


def test_silent_anomalies_diagnose_without_crash_signatures() -> None:
    volume = diagnose(
        EvidenceBundle(metric_anomalies=[anomaly("row_count")])
    )
    quality = diagnose(
        EvidenceBundle(
            metric_anomalies=[
                anomaly("null_ratio", "email"),
                anomaly("distinct_count", "email"),
            ]
        )
    )

    assert volume[0].cause_id == "data.volume_anomaly"
    assert quality[0].cause_id == "data.quality_regression"
    assert quality[0].confidence == 0.8
    assert quality[0].affected["columns"] == ["email"]


def test_volume_anomaly_defers_to_crash_signatures() -> None:
    bundle = EvidenceBundle(
        log_signatures=[hit("resources.out_of_memory", root=True)],
        metric_anomalies=[anomaly("row_count")],
    )

    diagnoses = diagnose(bundle)

    assert [item.cause_id for item in diagnoses] == ["resources.out_of_memory"]
    # The row-count surge corroborates the OOM instead of standing alone.
    assert diagnoses[0].confidence == 0.95


def test_fallback_and_empty_bundles() -> None:
    unmatched = EvidenceBundle(schema_drifts=[drift("added", "new_col")])

    fallback = diagnose(unmatched)
    empty = diagnose(EvidenceBundle())

    assert [item.cause_id for item in fallback] == ["unknown.needs_human"]
    assert fallback[0].confidence == 0.1
    assert fallback[0].evidence_refs == ["drift-added-new_col"]
    assert empty == []


def test_ordering_and_determinism() -> None:
    bundle = EvidenceBundle(
        log_signatures=[
            hit("schema.missing_column", root=True, matched={"column": "c"}),
            hit("connectivity.connection_failed"),
        ],
        schema_drifts=[drift("removed", "c")],
        metric_anomalies=[anomaly("null_ratio", "email")],
    )

    once = diagnose(bundle)
    twice = diagnose(bundle)

    assert [item.model_dump() for item in once] == [
        item.model_dump() for item in twice
    ]
    confidences = [item.confidence for item in once]
    assert confidences == sorted(confidences, reverse=True)
    all_evidence = {
        "hit-schema.missing_column",
        "hit-connectivity.connection_failed",
        "drift-removed-c",
        "anomaly-null_ratio-email",
    }
    for diagnosis in once:
        assert set(diagnosis.evidence_refs) <= all_evidence
