from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline_copilot.api.app import create_app
from pipeline_copilot.core.config import AppConfig

HEADERS = {"X-Session-Token": "secret"}

LOG = (
    "2026-07-15T00:00:01Z WARN upstream task extract_orders failed\n"
    '2026-07-15T00:00:02Z ERROR column "customer_id" does not exist\n'
    "2026-07-15T00:00:03Z ERROR Connection refused during cleanup\n"
)
SCHEMA_BEFORE = {"customer_id": "varchar", "amount": "int64"}
SCHEMA_AFTER = {"customer_ref": "varchar", "amount": "int64"}
RUNS = {
    "runs": [
        {"run_id": f"r{index}", "status": "success", "row_count": 1000}
        for index in range(4)
    ]
    + [{"run_id": "r9", "status": "failed", "row_count": 100}]
}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(AppConfig(workspace=tmp_path), token="secret")
    return TestClient(app)


def stage_bundle(client: TestClient) -> str:
    incident_id = client.post(
        "/api/incidents", headers=HEADERS, json={"pipeline": "orders-etl"}
    ).json()["id"]
    uploads = [
        ("log", "run.log", LOG.encode()),
        ("schema_before", "before.json", json.dumps(SCHEMA_BEFORE).encode()),
        ("schema_after", "after.json", json.dumps(SCHEMA_AFTER).encode()),
        ("metrics", "runs.json", json.dumps(RUNS).encode()),
    ]
    for kind, filename, content in uploads:
        response = client.post(
            f"/api/incidents/{incident_id}/files",
            params={"kind": kind, "filename": filename},
            headers=HEADERS,
            content=content,
        )
        assert response.status_code == 200
    return incident_id


def test_analyze_produces_ranked_persisted_report(client: TestClient) -> None:
    incident_id = stage_bundle(client)

    analyzed = client.post(
        f"/api/incidents/{incident_id}/analyze", headers=HEADERS
    )

    assert analyzed.status_code == 200
    report = analyzed.json()
    assert report["incident_id"] == incident_id
    assert report["bundle_fingerprint"]
    causes = [item["cause_id"] for item in report["diagnoses"]]
    assert causes[0] == "schema.upstream_contract_change"
    top = report["diagnoses"][0]
    assert top["confidence"] == 0.95
    assert "customer_id" in top["affected"]["columns"]
    assert top["recommended_fixes"][0]["fix_id"] == "restore_or_alias_column"
    assert "connectivity.transient" in causes
    assert "Most likely: Upstream schema contract change" in report["summary"]

    evidence_ids = {
        item["id"]
        for section in ("log_signatures", "schema_drifts", "metric_anomalies")
        for item in report["evidence"][section]
    }
    for diagnosis in report["diagnoses"]:
        assert set(diagnosis["evidence_refs"]) <= evidence_ids

    persisted = client.get(
        f"/api/incidents/{incident_id}/report", headers=HEADERS
    )
    assert persisted.status_code == 200
    assert persisted.json() == report
    assert client.get(
        f"/api/incidents/{incident_id}", headers=HEADERS
    ).json()["state"] == "analyzed"


def test_analyze_is_deterministic_and_handles_bad_inputs(
    client: TestClient,
) -> None:
    incident_id = stage_bundle(client)
    first = client.post(
        f"/api/incidents/{incident_id}/analyze", headers=HEADERS
    ).json()
    second = client.post(
        f"/api/incidents/{incident_id}/analyze", headers=HEADERS
    ).json()
    assert first == second

    broken_id = client.post(
        "/api/incidents", headers=HEADERS, json={"pipeline": "p"}
    ).json()["id"]
    client.post(
        f"/api/incidents/{broken_id}/files",
        params={"kind": "metrics", "filename": "runs.json"},
        headers=HEADERS,
        content=b"{nope",
    )
    report = client.post(
        f"/api/incidents/{broken_id}/analyze", headers=HEADERS
    ).json()
    assert any("metrics:" in note for note in report["evidence"]["unparsed"])
    assert report["diagnoses"] == []


def test_empty_incident_reports_no_evidence(client: TestClient) -> None:
    incident_id = client.post(
        "/api/incidents", headers=HEADERS, json={"pipeline": "p"}
    ).json()["id"]

    report = client.post(
        f"/api/incidents/{incident_id}/analyze", headers=HEADERS
    ).json()

    assert report["diagnoses"] == []
    assert "No evidence" in report["summary"]
    missing = client.get(
        "/api/incidents/00000000000000000000000000000000/report",
        headers=HEADERS,
    )
    assert missing.status_code == 404
