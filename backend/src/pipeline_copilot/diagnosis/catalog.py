from __future__ import annotations

from pipeline_copilot.domain.diagnosis import RecommendedFix

FIXES: dict[str, list[RecommendedFix]] = {
    "schema.upstream_contract_change": [
        RecommendedFix(
            fix_id="restore_or_alias_column",
            title="Restore or alias the missing column",
            steps=[
                "Confirm with the upstream owner whether the column was"
                " removed or renamed.",
                "If renamed, add a select alias mapping the new name to the"
                " expected name.",
                "Pin the upstream contract with a schema test so the next"
                " change fails fast.",
            ],
        )
    ],
    "schema.type_mismatch": [
        RecommendedFix(
            fix_id="add_explicit_cast",
            title="Add an explicit, validated cast",
            steps=[
                "Add a TRY_CAST/SAFE_CAST with a quarantine path for values"
                " that cannot convert.",
                "Coordinate the type migration with the upstream owner.",
            ],
        )
    ],
    "resources.out_of_memory": [
        RecommendedFix(
            fix_id="right_size_memory",
            title="Right-size memory or partition the workload",
            steps=[
                "Raise the task memory limit or executor memory.",
                "Partition the input (by date or key range) so each task"
                " processes a bounded slice.",
                "Check for accidental cross joins or exploding aggregations.",
            ],
        )
    ],
    "resources.disk_full": [
        RecommendedFix(
            fix_id="free_disk",
            title="Free or expand local disk",
            steps=[
                "Clear stale temp/spill directories on the worker.",
                "Expand the volume or move spill storage to a larger disk.",
            ],
        )
    ],
    "connectivity.transient": [
        RecommendedFix(
            fix_id="retry_with_backoff",
            title="Retry with backoff and verify endpoint health",
            steps=[
                "Re-run the pipeline; transient network failures often clear.",
                "Check the target service's health and recent deploys.",
                "Increase retry budget with exponential backoff and jitter.",
            ],
        )
    ],
    "access.permission_revoked": [
        RecommendedFix(
            fix_id="restore_grant",
            title="Restore the revoked grant or rotate the credential",
            steps=[
                "Compare current grants against the pipeline's required"
                " access list.",
                "Restore the missing grant, or rotate and redeploy the"
                " expired credential.",
            ],
        )
    ],
    "data.volume_anomaly": [
        RecommendedFix(
            fix_id="validate_upstream_extract",
            title="Validate the upstream extract volume",
            steps=[
                "Confirm the upstream source produced the expected volume"
                " for the period.",
                "Add a row-count guard that fails the run before loading"
                " when volume is anomalous.",
            ],
        )
    ],
    "data.quality_regression": [
        RecommendedFix(
            fix_id="quarantine_and_gate",
            title="Quarantine affected rows and add a quality gate",
            steps=[
                "Quarantine rows failing the affected column's expectations.",
                "Raise the regression with the upstream owner with example"
                " rows.",
                "Add a null-ratio/distinct-count gate at ingestion.",
            ],
        )
    ],
    "unknown.needs_human": [
        RecommendedFix(
            fix_id="triage_checklist",
            title="Manual triage checklist",
            steps=[
                "Read the first ERROR in the earliest failing task's log.",
                "Diff the last successful run's configuration against this"
                " run.",
                "Check upstream runs, recent deploys, and infrastructure"
                " status pages.",
            ],
        )
    ],
}

TITLES: dict[str, str] = {
    "schema.upstream_contract_change": "Upstream schema contract change",
    "schema.type_mismatch": "Column type mismatch",
    "resources.out_of_memory": "Out of memory",
    "resources.disk_full": "Disk full",
    "connectivity.transient": "Transient connectivity failure",
    "access.permission_revoked": "Permission or credential failure",
    "data.volume_anomaly": "Anomalous data volume",
    "data.quality_regression": "Data quality regression",
    "unknown.needs_human": "Unclassified incident — needs human triage",
}
