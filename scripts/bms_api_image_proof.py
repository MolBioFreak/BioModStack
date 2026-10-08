#!/usr/bin/env python3
"""Preflight and operator plan for BMS API image rebuild proof.

This helper is intentionally non-destructive. It validates that the normal
`bms-api` rebuild target is the lightweight `api-runtime` stage and prints the
exact recreate plan operators can run when they want image-level proof.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROJECT = "biomodstack-core-runtime"
COMPOSE_FILE = "compose.core-runtime.yml"
API_SERVICE = "bms-api"
API_TARGET = "api-runtime"
STATS_SERVICE = "bms-stats-tools"
STATS_TARGET = "stats-tools-runtime"
FORBIDDEN_API_RUNTIME_MARKERS = (
    "r-base",
    "r-cran-",
    "Rscript /app/docker/install_assay_r_packages.R",
)

_URL_CREDENTIAL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:)([^@\s]+)(@)")
_SECRET_LINE_RE = re.compile(
    r"(?im)^(?P<prefix>\s*(?:[A-Z0-9_]*PASSWORD[A-Z0-9_]*|BMS_ANALYTICAL_DATABASE_URL)\s*=\s*).*$"
)


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def redact_text(text: str) -> str:
    """Remove password-like values from logs/plans before display."""
    text = _URL_CREDENTIAL_RE.sub(r"\1***\3", text)
    return _SECRET_LINE_RE.sub(r"\g<prefix>[REDACTED]", text)


def _load_compose(repo_root: Path) -> dict[str, Any]:
    with (repo_root / COMPOSE_FILE).open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{COMPOSE_FILE} did not parse to a mapping")
    return loaded


def _stage_index(dockerfile: str, stage: str) -> int:
    needle = f"FROM api-base AS {stage}"
    return dockerfile.find(needle)


def _api_runtime_prefix(dockerfile: str) -> str:
    stats_idx = _stage_index(dockerfile, STATS_TARGET)
    if stats_idx < 0:
        return dockerfile
    return dockerfile[:stats_idx]


def assess_repo_contract(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Return a machine-readable proof that bms-api is isolated from R stats layers."""
    root = Path(repo_root) if repo_root is not None else repo_root_from_script()
    compose = _load_compose(root)
    services = compose.get("services", {})
    api_service = services.get(API_SERVICE, {})
    stats_service = services.get(STATS_SERVICE, {})

    api_target = api_service.get("build", {}).get("target")
    stats_target = stats_service.get("build", {}).get("target")

    dockerfile = (root / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    api_idx = _stage_index(dockerfile, API_TARGET)
    stats_idx = _stage_index(dockerfile, STATS_TARGET)
    api_prefix = _api_runtime_prefix(dockerfile)
    forbidden_markers = [marker for marker in FORBIDDEN_API_RUNTIME_MARKERS if marker in api_prefix]

    dockerignore_lines = {
        line.strip()
        for line in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    dockerignore_excludes_env_files = {".env", ".env.*", "!.env.core-runtime.example"}.issubset(dockerignore_lines)

    issues: list[str] = []
    if api_target != API_TARGET:
        issues.append(f"{API_SERVICE} build target is {api_target!r}, expected {API_TARGET!r}")
    if stats_target != STATS_TARGET:
        issues.append(f"{STATS_SERVICE} build target is {stats_target!r}, expected {STATS_TARGET!r}")
    if api_idx < 0:
        issues.append(f"missing Dockerfile stage {API_TARGET!r}")
    if stats_idx < 0:
        issues.append(f"missing Dockerfile stage {STATS_TARGET!r}")
    if not (api_idx >= 0 and stats_idx >= 0 and api_idx < stats_idx):
        issues.append(f"{API_TARGET!r} must appear before {STATS_TARGET!r}")
    if forbidden_markers:
        issues.append(f"{API_TARGET!r} prefix contains R/stats markers: {forbidden_markers}")
    if not dockerignore_excludes_env_files:
        issues.append(".dockerignore must exclude .env and .env.* while allowing .env.core-runtime.example")

    return {
        "ok": not issues,
        "issues": issues,
        "compose_file": COMPOSE_FILE,
        "compose_project": DEFAULT_PROJECT,
        "api_service": {
            "name": API_SERVICE,
            "build_target": api_target,
            "container_name": api_service.get("container_name"),
        },
        "stats_tools_service": {
            "name": STATS_SERVICE,
            "build_target": stats_target,
            "container_name": stats_service.get("container_name"),
        },
        "api_runtime_stage_before_stats_tools_stage": api_idx >= 0 and stats_idx >= 0 and api_idx < stats_idx,
        "api_runtime_prefix_has_r_stack": bool(forbidden_markers),
        "api_runtime_forbidden_markers": forbidden_markers,
        "dockerignore_excludes_env_files": dockerignore_excludes_env_files,
    }


def render_recreate_plan(repo_root: str | Path | None = None) -> str:
    """Render explicit non-implicit-build commands for clean API image proof."""
    root = Path(repo_root) if repo_root is not None else repo_root_from_script()
    assessment = assess_repo_contract(root)
    project = assessment["compose_project"]
    compose = assessment["compose_file"]
    return "\n".join(
        [
            "# BMS API image rebuild/recreate proof plan",
            "# Scope: bms-api only; stats-tools is intentionally not part of this rebuild path.",
            "# First preflight should report ok=true and api-runtime before stats-tools-runtime.",
            f"python3 scripts/bms_api_image_proof.py preflight",
            f"docker compose -p {project} -f {compose} build {API_SERVICE}",
            f"docker compose -p {project} -f {compose} up -d --no-deps --force-recreate {API_SERVICE}",
            f"docker compose -p {project} -f {compose} ps {API_SERVICE}",
            "./scripts/bms db-service health  # BMS DB service should remain running or honestly degraded",
            "# Then verify /api/health and any relevant assay import smoke before reporting deployment proof.",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BMS API image rebuild proof preflight and plan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="validate API image/stage isolation contract")
    preflight.add_argument("--repo-root", default=None)
    preflight.add_argument("--pretty", action="store_true", help="pretty-print JSON")

    plan = subparsers.add_parser("plan", help="print explicit non-destructive rebuild/recreate proof commands")
    plan.add_argument("--repo-root", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "preflight":
        assessment = assess_repo_contract(args.repo_root)
        output = json.dumps(assessment, indent=2 if args.pretty else None, sort_keys=True)
        print(redact_text(output))
        return 0 if assessment["ok"] else 1

    if args.command == "plan":
        print(redact_text(render_recreate_plan(args.repo_root)))
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
