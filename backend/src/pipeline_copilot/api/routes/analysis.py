from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from pipeline_copilot.api.routes.incidents import get_repo, incident_or_404
from pipeline_copilot.reporting.report import (
    build_report,
    collect_evidence,
    render_json,
)
from pipeline_copilot.storage.incident_repository import IncidentRepository

router = APIRouter()


def _single(paths: list[Path]) -> Path | None:
    return paths[0] if paths else None


@router.post("/api/incidents/{incident_id}/analyze")
def analyze_incident(
    incident_id: str,
    repo: IncidentRepository = Depends(get_repo),
) -> dict[str, object]:
    manifest = incident_or_404(repo, incident_id)
    evidence = collect_evidence(
        log_paths=repo.staged_paths(manifest, "log"),
        schema_before=_single(repo.staged_paths(manifest, "schema_before")),
        schema_after=_single(repo.staged_paths(manifest, "schema_after")),
        metrics_path=_single(repo.staged_paths(manifest, "metrics")),
    )
    report = build_report(
        incident_id=manifest.id,
        pipeline=manifest.pipeline,
        bundle_fingerprint=repo.bundle_fingerprint(manifest),
        evidence=evidence,
    )
    repo.save_report(incident_id, render_json(report))
    return report.model_dump(mode="json")


@router.get("/api/incidents/{incident_id}/report")
def get_report(
    incident_id: str,
    repo: IncidentRepository = Depends(get_repo),
) -> dict[str, object]:
    incident_or_404(repo, incident_id)
    saved = repo.load_report(incident_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="report not found")
    return saved
