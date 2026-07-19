#!/usr/bin/env python3
"""Preflight and operator plan for the BMS API image rebuild."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROJECT = "biomodstack-core-runtime"
COMPOSE_FILE = "compose.core-runtime.yml"
API_SERVICE = "bms-api"
API_TARGET = "api-runtime"

_URL_CREDENTIAL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:)([^@\s]+)(@)")
_SECRET_LINE_RE = re.compile(r"(?im)^(?P<prefix>\s*[A-Z0-9_]*PASSWORD[A-Z0-9_]*\s*=\s*).*$")


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def redact_text(text: str) -> str:
    text = _URL_CREDENTIAL_RE.sub(r"\1***\3", text)
    return _SECRET_LINE_RE.sub(r"\g<prefix>[REDACTED]", text)


def _load_compose(repo_root: Path) -> dict[str, Any]:
    with (repo_root / COMPOSE_FILE).open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{COMPOSE_FILE} did not parse to a mapping")
    return loaded


def assess_repo_contract(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Return a machine-readable check of the API build target."""
    root = Path(repo_root) if repo_root is not None else repo_root_from_script()
    compose = _load_compose(root)
    api_service = compose.get("services", {}).get(API_SERVICE, {})
    api_target = api_service.get("build", {}).get("target")
    dockerfile = (root / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    api_stage_present = f"FROM scratch AS {API_TARGET}" in dockerfile

    dockerignore_lines = {
        line.strip()
        for line in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    env_files_excluded = {".env", ".env.*", "!.env.core-runtime.example"}.issubset(dockerignore_lines)

    issues: list[str] = []
    if api_target != API_TARGET:
        issues.append(f"{API_SERVICE} build target is {api_target!r}, expected {API_TARGET!r}")
    if not api_stage_present:
        issues.append(f"missing Dockerfile stage {API_TARGET!r}")
    if not env_files_excluded:
        issues.append(".dockerignore must exclude local environment files and allow the example")

    return {
        "ok": not issues,
        "issues": issues,
        "compose_file": COMPOSE_FILE,
        "compose_project": os.getenv("BMS_DOCKER_COMPOSE_PROJECT", DEFAULT_PROJECT),
        "api_service": {
            "name": API_SERVICE,
            "build_target": api_target,
            "container_name": api_service.get("container_name"),
        },
        "api_runtime_stage_present": api_stage_present,
        "dockerignore_excludes_env_files": env_files_excluded,
    }


def render_recreate_plan(repo_root: str | Path | None = None) -> str:
    root = Path(repo_root) if repo_root is not None else repo_root_from_script()
    assessment = assess_repo_contract(root)
    project = assessment["compose_project"]
    compose = assessment["compose_file"]
    return "\n".join(
        [
            "# BMS API image rebuild/recreate proof plan",
            "python3 scripts/bms_api_image_proof.py preflight",
            f"docker compose -p {project} -f {compose} build {API_SERVICE}",
            f"docker compose -p {project} -f {compose} up -d --no-deps --force-recreate {API_SERVICE}",
            f"docker compose -p {project} -f {compose} ps {API_SERVICE}",
            "# Then verify /api/health before reporting deployment proof.",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BMS API image rebuild proof preflight and plan")
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight", help="validate the API image contract")
    preflight.add_argument("--repo-root", default=None)
    preflight.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    plan = subparsers.add_parser("plan", help="print non-destructive rebuild/recreate commands")
    plan.add_argument("--repo-root", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "preflight":
        assessment = assess_repo_contract(args.repo_root)
        print(redact_text(json.dumps(assessment, indent=2 if args.pretty else None, sort_keys=True)))
        return 0 if assessment["ok"] else 1
    if args.command == "plan":
        print(redact_text(render_recreate_plan(args.repo_root)))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
