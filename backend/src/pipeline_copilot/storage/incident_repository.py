from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import AsyncIterator, get_args
from uuid import uuid4

from pipeline_copilot.domain.incident import FileKind, IncidentManifest, StagedFile

VALID_KINDS: frozenset[str] = frozenset(get_args(FileKind))
SINGLETON_KINDS = frozenset({"schema_before", "schema_after", "metrics", "manifest"})
_FILENAME_SANITIZER = re.compile(r"[^A-Za-z0-9._-]")


class StagingError(ValueError):
    """Raised when an upload violates the incident bundle contract."""


def _sanitize_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    cleaned = _FILENAME_SANITIZER.sub("_", name).lstrip(".")
    if not cleaned:
        raise StagingError("filename is empty after sanitization")
    return cleaned


class IncidentRepository:
    def __init__(self, root: Path, max_bundle_bytes: int, max_log_bytes: int):
        self.root = root
        self.max_bundle_bytes = max_bundle_bytes
        self.max_log_bytes = max_log_bytes

    def create(self, pipeline: str) -> IncidentManifest:
        incident_id = uuid4().hex
        directory = self.root / incident_id
        directory.mkdir(parents=True)
        manifest = IncidentManifest(id=incident_id, pipeline=pipeline, files=[])
        self._publish(manifest)
        return manifest

    def get(self, incident_id: str) -> IncidentManifest | None:
        if re.fullmatch(r"[0-9a-f]{32}", incident_id) is None:
            return None
        path = self.root / incident_id / "incident.json"
        try:
            return IncidentManifest.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None

    def incident_dir(self, incident_id: str) -> Path:
        return self.root / incident_id

    async def stage_file(
        self,
        incident_id: str,
        kind: str,
        filename: str,
        chunks: AsyncIterator[bytes],
    ) -> IncidentManifest:
        manifest = self.get(incident_id)
        if manifest is None:
            raise KeyError(incident_id)
        if kind not in VALID_KINDS:
            raise StagingError(f"unsupported file kind {kind!r}")
        if kind in SINGLETON_KINDS and any(
            staged.kind == kind for staged in manifest.files
        ):
            raise StagingError(f"incident already has a {kind} file")
        safe_name = _sanitize_filename(filename)
        if any(staged.filename == safe_name for staged in manifest.files):
            raise StagingError(f"filename {safe_name!r} already staged")
        bundle_bytes = sum(staged.size_bytes for staged in manifest.files)
        directory = self.root / incident_id / kind
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / safe_name
        digest = hashlib.sha256()
        written = 0
        try:
            with destination.open("xb") as target:
                async for chunk in chunks:
                    written += len(chunk)
                    if written > self.max_log_bytes:
                        raise StagingError("file exceeds the per-file limit")
                    if bundle_bytes + written > self.max_bundle_bytes:
                        raise StagingError("bundle exceeds the total size limit")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(destination, 0o444)
        except BaseException:
            try:
                os.chmod(destination, 0o600)
            except FileNotFoundError:
                pass
            destination.unlink(missing_ok=True)
            raise
        staged = StagedFile(
            kind=kind,  # type: ignore[arg-type]
            filename=safe_name,
            size_bytes=written,
            sha256=digest.hexdigest(),
        )
        updated = manifest.model_copy(
            update={"files": [*manifest.files, staged]}
        )
        self._publish(updated)
        return updated

    def bundle_fingerprint(self, manifest: IncidentManifest) -> str:
        material = "|".join(
            f"{staged.kind}:{staged.filename}:{staged.sha256}"
            for staged in sorted(
                manifest.files, key=lambda item: (item.kind, item.filename)
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def staged_paths(
        self, manifest: IncidentManifest, kind: FileKind
    ) -> list[Path]:
        return [
            self.root / manifest.id / staged.kind / staged.filename
            for staged in sorted(manifest.files, key=lambda item: item.filename)
            if staged.kind == kind
        ]

    def save_report(self, incident_id: str, report_json: str) -> None:
        manifest = self.get(incident_id)
        if manifest is None:
            raise KeyError(incident_id)
        self._write_durable(
            self.root / incident_id / "report.json", report_json
        )
        self._publish(manifest.model_copy(update={"state": "analyzed"}))

    def load_report(self, incident_id: str) -> dict[str, object] | None:
        if self.get(incident_id) is None:
            return None
        path = self.root / incident_id / "report.json"
        try:
            loaded: dict[str, object] = json.loads(
                path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        return loaded

    def _publish(self, manifest: IncidentManifest) -> None:
        self._write_durable(
            self.root / manifest.id / "incident.json",
            json.dumps(manifest.model_dump(mode="json"), indent=2),
        )

    def _write_durable(self, path: Path, payload: str) -> None:
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
