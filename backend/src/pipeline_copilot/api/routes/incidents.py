from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pipeline_copilot.domain.incident import IncidentManifest
from pipeline_copilot.storage.incident_repository import (
    IncidentRepository,
    StagingError,
)

router = APIRouter()


class CreateIncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline: str = Field(min_length=1, max_length=200)


def get_repo(request: Request) -> IncidentRepository:
    repo: IncidentRepository = request.app.state.incident_repository
    return repo


def incident_or_404(
    repo: IncidentRepository, incident_id: str
) -> IncidentManifest:
    manifest = repo.get(incident_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return manifest


@router.post("/api/incidents")
def create_incident(
    payload: CreateIncidentRequest,
    repo: IncidentRepository = Depends(get_repo),
) -> dict[str, object]:
    return repo.create(payload.pipeline).model_dump(mode="json")


@router.get("/api/incidents/{incident_id}")
def get_incident(
    incident_id: str,
    repo: IncidentRepository = Depends(get_repo),
) -> dict[str, object]:
    return incident_or_404(repo, incident_id).model_dump(mode="json")


@router.post("/api/incidents/{incident_id}/files")
async def stage_file(
    request: Request,
    incident_id: str,
    kind: str,
    filename: str,
    repo: IncidentRepository = Depends(get_repo),
) -> dict[str, object]:
    incident_or_404(repo, incident_id)
    try:
        manifest = await repo.stage_file(
            incident_id, kind, filename, request.stream()
        )
    except StagingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return manifest.model_dump(mode="json")
