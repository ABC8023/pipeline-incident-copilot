from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pipeline_copilot.analysis.log_parser import parse_log
from pipeline_copilot.domain.evidence import LogSignatureHit

EXCERPT_CHARS = 300


@dataclass(frozen=True)
class SignatureRule:
    rule_id: str
    rule_version: int
    category: str
    severity: Literal["warning", "error"]
    pattern: re.Pattern[str]
    is_cascade: bool = False


CATALOG: tuple[SignatureRule, ...] = (
    SignatureRule(
        "resources.out_of_memory",
        1,
        "resources",
        "error",
        re.compile(
            r"OutOfMemory|java\.lang\.OutOfMemoryError|MemoryError"
            r"|Killed.*exit code 137|exit code 137|OOM[- ]?kill",
            re.IGNORECASE,
        ),
    ),
    SignatureRule(
        "resources.disk_full",
        1,
        "resources",
        "error",
        re.compile(
            r"No space left on device|DiskFull|disk quota exceeded",
            re.IGNORECASE,
        ),
    ),
    SignatureRule(
        "access.permission_denied",
        1,
        "access",
        "error",
        re.compile(
            r"Permission denied|Access Denied|403 Forbidden"
            r"|insufficient privileges|authentication failed",
            re.IGNORECASE,
        ),
    ),
    SignatureRule(
        "connectivity.connection_failed",
        1,
        "connectivity",
        "error",
        re.compile(
            r"Connection refused|Connection reset|Connection timed out"
            r"|could not connect|connection timeout|ETIMEDOUT|ECONNREFUSED",
            re.IGNORECASE,
        ),
    ),
    SignatureRule(
        "connectivity.retry_exhausted",
        1,
        "connectivity",
        "error",
        re.compile(
            r"retr(?:y|ies) exhausted|max retries exceeded"
            r"|giving up after \d+ attempts",
            re.IGNORECASE,
        ),
    ),
    SignatureRule(
        "sql.syntax_error",
        1,
        "sql",
        "error",
        re.compile(r"syntax error at or near|SQL syntax error", re.IGNORECASE),
    ),
    SignatureRule(
        "schema.missing_column",
        1,
        "schema",
        "error",
        re.compile(
            r"column \"?(?P<column>[A-Za-z0-9_.]+)\"? (?:does not exist"
            r"|not found)|Unknown column '(?P<column2>[A-Za-z0-9_.]+)'"
            r"|KeyError: '(?P<column3>[A-Za-z0-9_.]+)'",
            re.IGNORECASE,
        ),
    ),
    SignatureRule(
        "schema.missing_table",
        1,
        "schema",
        "error",
        re.compile(
            r"(?:table|relation) \"?(?P<table>[A-Za-z0-9_.]+)\"? does not exist"
            r"|Table or view not found",
            re.IGNORECASE,
        ),
    ),
    SignatureRule(
        "schema.cast_failure",
        1,
        "schema",
        "error",
        re.compile(
            r"could not convert|invalid input syntax for type"
            r"|Conversion Error|cannot cast (?:type )?(?P<from>[A-Za-z ]+)"
            r" to (?P<to>[A-Za-z ]+)",
            re.IGNORECASE,
        ),
    ),
    SignatureRule(
        "cascade.upstream_failure",
        1,
        "cascade",
        "warning",
        re.compile(
            r"upstream (?:task|job|dependency) .{0,40}failed"
            r"|dependency .{0,40} failed|skipped because upstream",
            re.IGNORECASE,
        ),
        is_cascade=True,
    ),
)


def _hit_id(rule_id: str, file: str, line_number: int) -> str:
    material = f"{rule_id}|{file}|{line_number}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def scan_logs(paths: list[Path]) -> list[LogSignatureHit]:
    hits: list[LogSignatureHit] = []
    for path in sorted(paths, key=lambda item: item.name):
        for line in parse_log(path):
            for rule in CATALOG:
                match = rule.pattern.search(line.message)
                if match is None:
                    continue
                matched = {
                    key: value
                    for key, value in (match.groupdict() or {}).items()
                    if value
                }
                hits.append(
                    LogSignatureHit(
                        id=_hit_id(rule.rule_id, line.file, line.line_number),
                        rule_id=rule.rule_id,
                        rule_version=rule.rule_version,
                        category=rule.category,
                        severity=rule.severity,
                        file=line.file,
                        line_number=line.line_number,
                        timestamp=line.timestamp,
                        excerpt=line.message[:EXCERPT_CHARS],
                        matched=matched,
                        is_cascade=rule.is_cascade,
                    )
                )
    return _mark_root_and_cascades(hits)


def _mark_root_and_cascades(
    hits: list[LogSignatureHit],
) -> list[LogSignatureHit]:
    """The earliest non-cascade hit is the root candidate.

    Ordering uses (file, line) within the already file-sorted scan, which is
    deterministic even when timestamps are missing. Every other hit attaches
    to the root as cascade context.
    """
    root = next((hit for hit in hits if not hit.is_cascade), None)
    if root is None:
        return hits
    marked: list[LogSignatureHit] = []
    for hit in hits:
        if hit.id == root.id:
            marked.append(hit.model_copy(update={"is_root_candidate": True}))
        else:
            marked.append(hit.model_copy(update={"cascaded_from": root.id}))
    return marked
