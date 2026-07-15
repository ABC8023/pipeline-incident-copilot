from __future__ import annotations

import hashlib
from pathlib import Path
from typing import AsyncIterator

import pytest

from pipeline_copilot.storage.incident_repository import (
    IncidentRepository,
    StagingError,
)


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


@pytest.fixture
def repo(tmp_path: Path) -> IncidentRepository:
    return IncidentRepository(tmp_path, max_bundle_bytes=64, max_log_bytes=32)


@pytest.mark.asyncio
async def test_stage_fingerprints_and_freezes_files(
    repo: IncidentRepository, tmp_path: Path
) -> None:
    incident = repo.create("orders-etl")

    manifest = await repo.stage_file(
        incident.id, "log", "run.log", chunks(b"ERROR ", b"boom\n")
    )

    staged = manifest.files[0]
    assert staged.kind == "log"
    assert staged.sha256 == hashlib.sha256(b"ERROR boom\n").hexdigest()
    path = tmp_path / incident.id / "log" / "run.log"
    assert path.read_bytes() == b"ERROR boom\n"
    assert path.stat().st_mode & 0o222 == 0
    assert repo.get(incident.id) == manifest
    assert repo.staged_paths(manifest, "log") == [path]


@pytest.mark.asyncio
async def test_staging_enforces_caps_kinds_and_uniqueness(
    repo: IncidentRepository,
) -> None:
    incident = repo.create("orders-etl")

    with pytest.raises(StagingError, match="per-file limit"):
        await repo.stage_file(incident.id, "log", "big.log", chunks(b"x" * 33))
    with pytest.raises(StagingError, match="unsupported file kind"):
        await repo.stage_file(incident.id, "exploit", "x.bin", chunks(b"x"))

    await repo.stage_file(
        incident.id, "schema_before", "before.json", chunks(b"{}")
    )
    with pytest.raises(StagingError, match="already has a schema_before"):
        await repo.stage_file(
            incident.id, "schema_before", "again.json", chunks(b"{}")
        )
    await repo.stage_file(incident.id, "log", "a.log", chunks(b"x"))
    with pytest.raises(StagingError, match="already staged"):
        await repo.stage_file(incident.id, "log", "a.log", chunks(b"y"))

    partial = repo.get(incident.id)
    assert partial is not None
    assert sorted(item.filename for item in partial.files) == [
        "a.log",
        "before.json",
    ]


@pytest.mark.asyncio
async def test_bundle_cap_and_failed_uploads_leave_no_partial_files(
    tmp_path: Path,
) -> None:
    repo = IncidentRepository(tmp_path, max_bundle_bytes=10, max_log_bytes=32)
    incident = repo.create("orders-etl")
    await repo.stage_file(incident.id, "log", "a.log", chunks(b"x" * 8))

    with pytest.raises(StagingError, match="total size limit"):
        await repo.stage_file(incident.id, "log", "b.log", chunks(b"y" * 8))

    assert not (tmp_path / incident.id / "log" / "b.log").exists()
    manifest = repo.get(incident.id)
    assert manifest is not None and len(manifest.files) == 1


@pytest.mark.asyncio
async def test_filenames_are_sanitized_and_ids_validated(
    repo: IncidentRepository, tmp_path: Path
) -> None:
    incident = repo.create("orders-etl")

    manifest = await repo.stage_file(
        incident.id, "log", "..\\..\\evil name.log", chunks(b"x")
    )

    assert manifest.files[0].filename == "evil_name.log"
    assert repo.get("../outside") is None
    assert repo.get("not-a-real-id") is None
    with pytest.raises(KeyError):
        await repo.stage_file("f" * 32, "log", "a.log", chunks(b"x"))


def test_bundle_fingerprint_is_order_independent(
    repo: IncidentRepository,
) -> None:
    incident = repo.create("orders-etl")
    first = repo.bundle_fingerprint(incident)
    assert first == repo.bundle_fingerprint(incident)


def test_report_persistence_round_trips(repo: IncidentRepository) -> None:
    incident = repo.create("orders-etl")

    assert repo.load_report(incident.id) is None
    repo.save_report(incident.id, '{"diagnoses": []}')

    assert repo.load_report(incident.id) == {"diagnoses": []}
    updated = repo.get(incident.id)
    assert updated is not None and updated.state == "analyzed"
    with pytest.raises(KeyError):
        repo.save_report("e" * 32, "{}")
