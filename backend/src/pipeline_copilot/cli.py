from __future__ import annotations

import argparse
import hashlib
import socket
import sys
from pathlib import Path

from pipeline_copilot.reporting.report import (
    build_report,
    collect_evidence,
    render_json,
    render_markdown,
)

EXIT_OK = 0
EXIT_INVALID_BUNDLE = 1
EXIT_NO_EVIDENCE = 3


def _bundle_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def analyze_directory(bundle_dir: Path, output_format: str) -> tuple[int, str]:
    if not bundle_dir.is_dir():
        return EXIT_INVALID_BUNDLE, f"bundle directory not found: {bundle_dir}"
    logs_dir = bundle_dir / "logs"
    log_paths = sorted(
        path
        for pattern in ("*.log", "*.txt", "*.jsonl")
        for path in logs_dir.glob(pattern)
    ) if logs_dir.is_dir() else []
    schema_before = bundle_dir / "schema" / "before.json"
    schema_after = bundle_dir / "schema" / "after.json"
    metrics = bundle_dir / "metrics" / "runs.json"
    all_inputs = [
        path
        for path in [
            *log_paths,
            schema_before if schema_before.is_file() else None,
            schema_after if schema_after.is_file() else None,
            metrics if metrics.is_file() else None,
        ]
        if path is not None
    ]
    evidence = collect_evidence(
        log_paths=log_paths,
        schema_before=schema_before if schema_before.is_file() else None,
        schema_after=schema_after if schema_after.is_file() else None,
        metrics_path=metrics if metrics.is_file() else None,
    )
    report = build_report(
        incident_id=bundle_dir.name,
        pipeline=bundle_dir.name,
        bundle_fingerprint=_bundle_fingerprint(all_inputs),
        evidence=evidence,
    )
    rendered = (
        render_markdown(report)
        if output_format == "markdown"
        else render_json(report)
    )
    if evidence.is_empty() and not evidence.unparsed:
        return EXIT_NO_EVIDENCE, rendered
    return EXIT_OK, rendered


def _reserve_port(host: str) -> int:
    with socket.socket() as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def serve(host: str, port: int | None, workspace: Path | None) -> None:
    import uvicorn

    from pipeline_copilot.api.app import create_app
    from pipeline_copilot.core.config import AppConfig
    from pipeline_copilot.core.security import (
        new_session_token,
        validate_loopback_host,
    )

    validate_loopback_host(host)
    resolved_port = port if port is not None else _reserve_port(host)
    token = new_session_token()
    resolved_workspace = (
        workspace
        if workspace is not None
        else Path.home() / ".pipeline-incident-copilot" / "incidents"
    )
    resolved_workspace.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        workspace=resolved_workspace,
        allowed_origin=f"http://{host}:{resolved_port}",
    )
    print(f"pipeline-copilot listening on http://{host}:{resolved_port}")
    print(f"session token: {token}")
    uvicorn.Server(
        uvicorn.Config(
            create_app(config, token),
            host=host,
            port=resolved_port,
            log_config=None,
        )
    ).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline-copilot",
        description="Diagnose failed pipelines from local artifacts",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze", help="analyze a bundle directory")
    analyze.add_argument("bundle_dir", type=Path)
    analyze.add_argument(
        "--format", choices=["json", "markdown"], default="markdown"
    )
    analyze.add_argument("--output", type=Path, default=None)
    server = commands.add_parser("serve", help="run the local API")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=None)
    server.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.command == "serve":
        serve(args.host, args.port, args.workspace)
        return EXIT_OK
    code, rendered = analyze_directory(args.bundle_dir, args.format)
    if code == EXIT_INVALID_BUNDLE:
        print(rendered, file=sys.stderr)
        return code
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered)
    return code


if __name__ == "__main__":
    sys.exit(main())
