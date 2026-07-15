from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline_copilot.api.app import create_app
from pipeline_copilot.core.config import AppConfig
from pipeline_copilot.core.security import new_session_token, validate_loopback_host


def test_health_requires_session_token(tmp_path: Path) -> None:
    app = create_app(AppConfig(workspace=tmp_path), token="secret")
    client = TestClient(app)

    assert client.get("/api/health").status_code == 401
    response = client.get("/api/health", headers={"X-Session-Token": "secret"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_untrusted_origins_are_rejected(tmp_path: Path) -> None:
    app = create_app(
        AppConfig(workspace=tmp_path, allowed_origin="http://127.0.0.1:9001"),
        token="secret",
    )
    client = TestClient(app)
    headers = {"X-Session-Token": "secret"}

    trusted = client.get(
        "/api/health", headers={**headers, "Origin": "http://127.0.0.1:9001"}
    )
    untrusted = client.get(
        "/api/health", headers={**headers, "Origin": "https://example.com"}
    )

    assert trusted.status_code == 200
    assert untrusted.status_code == 403


def test_loopback_validation_and_tokens() -> None:
    for host in ("localhost", "127.0.0.1", "::1"):
        assert validate_loopback_host(host) == host
    with pytest.raises(ValueError, match="loopback"):
        validate_loopback_host("0.0.0.0")

    tokens = {new_session_token() for _ in range(8)}
    assert len(tokens) == 8
    assert all(len(token) >= 32 for token in tokens)
