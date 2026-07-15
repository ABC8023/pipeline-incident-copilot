from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline_copilot.api.app import create_app
from pipeline_copilot.core.config import AppConfig

HEADERS = {"X-Session-Token": "secret"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(AppConfig(workspace=tmp_path), token="secret")
    return TestClient(app)


def test_incident_upload_flow(client: TestClient) -> None:
    created = client.post(
        "/api/incidents", headers=HEADERS, json={"pipeline": "orders-etl"}
    )
    assert created.status_code == 200
    incident_id = created.json()["id"]

    staged = client.post(
        f"/api/incidents/{incident_id}/files",
        params={"kind": "log", "filename": "run.log"},
        headers=HEADERS,
        content=b"2026-07-15T01:02:03Z ERROR boom\n",
    )
    assert staged.status_code == 200
    assert staged.json()["files"][0]["filename"] == "run.log"

    fetched = client.get(f"/api/incidents/{incident_id}", headers=HEADERS)
    assert fetched.json() == staged.json()


def test_incident_routes_reject_bad_input(client: TestClient) -> None:
    incident_id = client.post(
        "/api/incidents", headers=HEADERS, json={"pipeline": "p"}
    ).json()["id"]

    bad_kind = client.post(
        f"/api/incidents/{incident_id}/files",
        params={"kind": "exploit", "filename": "x"},
        headers=HEADERS,
        content=b"x",
    )
    missing = client.get(
        "/api/incidents/00000000000000000000000000000000", headers=HEADERS
    )
    unauthenticated = client.post("/api/incidents", json={"pipeline": "p"})

    assert bad_kind.status_code == 400
    assert "unsupported file kind" in bad_kind.json()["detail"]
    assert missing.status_code == 404
    assert unauthenticated.status_code == 401
