from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from pipeline_copilot.domain.evidence import LogLine

MAX_LINE_CHARS = 4000

_LEVELS = {
    "trace": "debug",
    "debug": "debug",
    "info": "info",
    "warn": "warning",
    "warning": "warning",
    "error": "error",
    "err": "error",
    "critical": "fatal",
    "fatal": "fatal",
}
_TIMESTAMP = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_LEVEL_TOKEN = re.compile(
    r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|ERR|CRITICAL|FATAL)\b"
)


def _parse_json_line(raw: str, file: str, line_number: int) -> LogLine | None:
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    level_raw = str(
        record.get("level") or record.get("severity") or "unknown"
    ).lower()
    message = str(
        record.get("message") or record.get("msg") or record.get("event") or raw
    )
    timestamp = record.get("timestamp") or record.get("time") or record.get("ts")
    return LogLine(
        file=file,
        line_number=line_number,
        timestamp=None if timestamp is None else str(timestamp),
        level=_LEVELS.get(level_raw, "unknown"),  # type: ignore[arg-type]
        message=message[:MAX_LINE_CHARS],
    )


def _parse_text_line(raw: str, file: str, line_number: int) -> LogLine:
    timestamp_match = _TIMESTAMP.search(raw)
    level_match = _LEVEL_TOKEN.search(raw)
    level = "unknown"
    if level_match is not None:
        level = _LEVELS.get(level_match.group(1).lower(), "unknown")
    return LogLine(
        file=file,
        line_number=line_number,
        timestamp=None if timestamp_match is None else timestamp_match.group("ts"),
        level=level,  # type: ignore[arg-type]
        message=raw[:MAX_LINE_CHARS],
    )


def parse_log(path: Path) -> Iterator[LogLine]:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, raw in enumerate(stream, start=1):
            stripped = raw.rstrip("\r\n")
            if not stripped.strip():
                continue
            parsed = None
            if stripped.lstrip().startswith("{"):
                parsed = _parse_json_line(stripped, path.name, line_number)
            if parsed is None:
                parsed = _parse_text_line(stripped, path.name, line_number)
            yield parsed
