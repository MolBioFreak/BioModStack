#!/usr/bin/env python3
"""Validate the immutable wf-clone-validation runtime lock and emit provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn


LOCK_SCHEMA = "biomodstack.wf_clone_validation_lock.v1"
PROVENANCE_SCHEMA = "biomodstack.wf_clone_validation_runtime_provenance.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ValidationFailure(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def fail(reason_code: str, message: str) -> NoReturn:
    raise ValidationFailure(reason_code, message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail("LOCK_MISSING", f"runtime lock does not exist: {path}")
    except (OSError, json.JSONDecodeError) as exc:
        fail("LOCK_MALFORMED", f"runtime lock cannot be read as JSON: {exc}")
    if not isinstance(value, dict):
        fail("LOCK_MALFORMED", "runtime lock must be a JSON object")
    return value


def require_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        fail("LOCK_MALFORMED", f"lock field {key!r} must be an object")
    return value


def require_string(parent: dict[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        fail("LOCK_MALFORMED", f"lock field {key!r} must be a non-empty string")
    return value


def resolve_lock_path(lock_path: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else (lock_path.parent / candidate).resolve()


def git(source: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source), *args],
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        fail("SOURCE_GIT_INVALID", f"cannot inspect patched source repository: {exc}")
    return result.stdout.strip()


def validate_runtime(lock_path: Path, selected_model: str) -> dict[str, Any]:
    lock_path = lock_path.resolve()
    lock = load_object(lock_path)
    if lock.get("schema") != LOCK_SCHEMA or lock.get("lock_version") != 1:
        fail("LOCK_SCHEMA_UNSUPPORTED", f"expected {LOCK_SCHEMA} lock_version 1")
    policy = require_object(lock, "runtime_policy")
    if policy != {"network": "forbidden", "nxf_offline": True}:
        fail("NETWORK_POLICY_INVALID", "runtime policy must forbid network and require NXF offline mode")

    upstream = require_object(lock, "upstream")
    for field in ("commit", "tree"):
        if not GIT_RE.fullmatch(require_string(upstream, field)):
            fail("LOCK_MALFORMED", f"upstream {field} must be a lowercase Git object ID")

    patched = require_object(lock, "patched_source")
    source = resolve_lock_path(lock_path, require_string(patched, "path"))
    if not source.is_dir():
        fail("SOURCE_MISSING", f"patched source directory does not exist: {source}")
    actual_commit = git(source, "rev-parse", "HEAD")
    expected_commit = require_string(patched, "commit")
    if actual_commit != expected_commit:
        fail("SOURCE_COMMIT_MISMATCH", f"patched source HEAD is {actual_commit}, expected {expected_commit}")
    actual_tree = git(source, "rev-parse", "HEAD^{tree}")
    expected_tree = require_string(patched, "tree")
    if actual_tree != expected_tree:
        fail("SOURCE_TREE_MISMATCH", f"patched source tree is {actual_tree}, expected {expected_tree}")
    dirty = git(source, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        fail("SOURCE_DIRTY", "patched source has tracked or untracked changes")

    compatibility = require_object(lock, "compatibility_patch")
    patch_path = resolve_lock_path(lock_path, require_string(compatibility, "path"))
    if not patch_path.is_file() or patch_path.is_symlink():
        fail("PATCH_MISSING", f"compatibility patch is missing or is a symlink: {patch_path}")
    actual_patch_sha = sha256_file(patch_path)
    expected_patch_sha = require_string(compatibility, "sha256")
    if actual_patch_sha != expected_patch_sha:
        fail("PATCH_SHA256_MISMATCH", f"compatibility patch SHA-256 is {actual_patch_sha}, expected {expected_patch_sha}")
    if compatibility.get("base_commit") != upstream.get("commit"):
        fail("PATCH_IDENTITY_MISMATCH", "compatibility patch base does not match locked upstream commit")
    if compatibility.get("result_commit") != expected_commit or compatibility.get("result_tree") != expected_tree:
        fail("PATCH_IDENTITY_MISMATCH", "compatibility patch result identity does not match patched source identity")

    runtime = require_object(lock, "nextflow")
    executable = resolve_lock_path(lock_path, require_string(runtime, "executable"))
    if not executable.is_file():
        fail("NEXTFLOW_MISSING", f"Nextflow executable does not exist: {executable}")
    try:
        version_result = subprocess.run(
            [str(executable), "-version"], text=True, capture_output=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        fail("NEXTFLOW_RUNTIME_INVALID", f"Nextflow version probe failed: {exc}")
    version_text = f"{version_result.stdout}\n{version_result.stderr}"
    expected_version = require_string(runtime, "version")
    expected_build = require_string(runtime, "build")
    match = re.search(r"version\s+([0-9.]+)\s+build\s+([0-9]+)", version_text, re.IGNORECASE)
    if match is None or match.groups() != (expected_version, expected_build):
        found = match.groups() if match else ("unparseable", "unparseable")
        fail(
            "NEXTFLOW_VERSION_MISMATCH",
            f"Nextflow identity is version {found[0]} build {found[1]}, expected {expected_version} build {expected_build}",
        )

    containers = require_object(lock, "containers")
    cache_dir = resolve_lock_path(lock_path, require_string(containers, "cache_dir"))
    images = containers.get("images")
    if not isinstance(images, list) or len(images) != 5:
        fail("LOCK_MALFORMED", "container lock must contain exactly five images")
    validated_images: list[dict[str, str]] = []
    for index, image in enumerate(images):
        if not isinstance(image, dict):
            fail("LOCK_MALFORMED", f"container image {index} must be an object")
        uri = require_string(image, "uri")
        cache_file = require_string(image, "cache_file")
        if Path(cache_file).name != cache_file:
            fail("LOCK_MALFORMED", f"container cache_file must be a basename: {cache_file}")
        expected_sha = require_string(image, "sha256")
        if not SHA256_RE.fullmatch(expected_sha):
            fail("LOCK_MALFORMED", f"container SHA-256 is malformed for {uri}")
        image_path = cache_dir / cache_file
        if not image_path.is_file() or image_path.is_symlink():
            fail("IMAGE_MISSING", f"locked image is missing or is a symlink: {image_path}")
        actual_sha = sha256_file(image_path)
        if actual_sha != expected_sha:
            fail("IMAGE_SHA256_MISMATCH", f"image {uri} SHA-256 is {actual_sha}, expected {expected_sha}")
        validated_images.append({"uri": uri, "path": str(image_path), "sha256": actual_sha})

    models = require_object(lock, "models")
    accepted = models.get("accepted_upstream_ids")
    if not isinstance(accepted, list) or not accepted or not all(isinstance(item, str) for item in accepted):
        fail("LOCK_MALFORMED", "accepted_upstream_ids must be a non-empty string array")
    if selected_model not in accepted:
        fail("MODEL_ID_UNSUPPORTED", f"selected exact upstream model identity is unsupported: {selected_model}")
    model_store = resolve_lock_path(lock_path, require_string(models, "store"))
    selected_model_path = model_store / selected_model
    if not selected_model_path.is_dir() or selected_model_path.is_symlink():
        fail("MODEL_MISSING", f"selected locked model directory does not exist: {selected_model_path}")

    return {
        "schema": PROVENANCE_SCHEMA,
        "validation_status": "valid",
        "lock": {"path": str(lock_path), "sha256": sha256_file(lock_path)},
        "upstream": upstream,
        "patched_source": {"path": str(source), "commit": actual_commit, "tree": actual_tree, "status": "clean"},
        "compatibility_patch": {"path": str(patch_path), "sha256": actual_patch_sha},
        "nextflow": {"executable": str(executable), "version": expected_version, "build": expected_build},
        "images": validated_images,
        "selected_model_id": selected_model,
        "selected_model_path": str(selected_model_path),
        "network_policy": "forbidden",
        "nxf_offline": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        provenance = validate_runtime(args.lock, args.model)
    except ValidationFailure as exc:
        print(
            json.dumps(
                {"schema": PROVENANCE_SCHEMA, "validation_status": "invalid", "reason_code": exc.reason_code, "reason": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    payload = json.dumps(provenance, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output == "-":
        sys.stdout.write(payload)
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
