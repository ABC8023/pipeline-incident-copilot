from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FileKind = Literal["log", "schema_before", "schema_after", "metrics", "manifest"]


class StagedFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: FileKind
    filename: str
    size_bytes: int = Field(ge=0)
    sha256: str


class IncidentManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    pipeline: str
    files: list[StagedFile]
    state: Literal["staged", "analyzed"] = "staged"
