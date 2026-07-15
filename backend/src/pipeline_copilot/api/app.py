from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from pipeline_copilot.api.routes.analysis import router as analysis_router
from pipeline_copilot.api.routes.health import router as health_router
from pipeline_copilot.api.routes.incidents import router as incidents_router
from pipeline_copilot.core.config import AppConfig
from pipeline_copilot.storage.incident_repository import IncidentRepository


def create_app(config: AppConfig, token: str) -> FastAPI:
    app = FastAPI()

    def require_token(x_session_token: str | None = Header(default=None)) -> None:
        if x_session_token != token:
            raise HTTPException(status_code=401, detail="invalid session token")

    @app.middleware("http")
    async def enforce_origin(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin")
        if origin is not None and origin != config.allowed_origin:
            return JSONResponse(
                status_code=403, content={"detail": "origin not allowed"}
            )
        return await call_next(request)

    app.state.config = config
    app.state.token_dependency = require_token
    app.state.incident_repository = IncidentRepository(
        config.workspace, config.max_bundle_bytes, config.max_log_bytes
    )
    app.include_router(health_router, dependencies=[Depends(require_token)])
    app.include_router(incidents_router, dependencies=[Depends(require_token)])
    app.include_router(analysis_router, dependencies=[Depends(require_token)])
    return app
