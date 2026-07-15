from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace: Path
    max_bundle_bytes: int = 512 * 1024**2
    max_log_bytes: int = 64 * 1024**2
    allowed_origin: str = "http://127.0.0.1"
