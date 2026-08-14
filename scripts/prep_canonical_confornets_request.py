#!/usr/bin/env python3
"""Materialize an authenticated legacy-native ConforNets request from cm_request_v1."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any


class CanonicalPrepError(ValueError):
    pass


_CANONICAL_CONFORNETS_REPO = Path("/opt/confornets")


def _instrumented_confornets_runtime_available() -> bool:
    return (
        _CANONICAL_CONFORNETS_REPO / "confornet/utils/cm_coordinate_ledger.py"
    ).is_file()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanonicalPrepError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CanonicalPrepError(f"{label} must be an object")
    return value


def _lstat_owned(path: Path, *, owner_uid: int, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CanonicalPrepError(f"{label} is not an approved request-owned path: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise CanonicalPrepError(f"{label} symlink is forbidden")
    if metadata.st_uid != owner_uid:
        raise CanonicalPrepError(f"{label} is not owned by the request owner")
    return metadata


def _resolved_request_root(request_root: Path) -> tuple[Path, os.stat_result]:
    try:
        resolved = request_root.resolve(strict=True)
    except OSError as exc:
        raise CanonicalPrepError(f"request root is not resolvable: {exc}") from exc
    allowed_root = Path(
        os.environ.get("BMS_RESULTS_ROOT", "/home/dalab/.biomodstack-dev/bms_results")
    ).resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise CanonicalPrepError("request root is outside the server-owned results root") from exc
    metadata = resolved.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise CanonicalPrepError("request root is not a directory")
    return resolved, metadata


def _request_owned_path(
    request_root: Path,
    value: str,
    *,
    label: str,
    expect_directory: bool,
) -> Path:
    if not isinstance(value, str) or not value:
        raise CanonicalPrepError(f"{label} path must be a nonempty relative path")
    relative = Path(value)
    if (
        relative.is_absolute()
        or "\\\\" in value
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise CanonicalPrepError(f"{label} path escapes or is not canonical under the request root")

    resolved_root, root_metadata = _resolved_request_root(request_root)
    owner_uid = root_metadata.st_uid
    current = resolved_root
    final_metadata = root_metadata
    for index, component in enumerate(relative.parts):
        current = current / component
        final_metadata = _lstat_owned(current, owner_uid=owner_uid, label=label)
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(final_metadata.st_mode):
            raise CanonicalPrepError(f"{label} has a non-directory path component")

    if expect_directory:
        if not stat.S_ISDIR(final_metadata.st_mode):
            raise CanonicalPrepError(f"{label} is not an approved request-owned directory")
    else:
        if not stat.S_ISREG(final_metadata.st_mode) or final_metadata.st_nlink != 1:
            raise CanonicalPrepError(f"{label} is not an unaliased request-owned regular file")

    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise CanonicalPrepError(f"{label} path escapes the request root") from exc
    return resolved


def _validate_owned_tree(request_root: Path, relative_value: str, *, label: str) -> Path:
    root = _request_owned_path(
        request_root, relative_value, label=label, expect_directory=True
    )
    _, root_metadata = _resolved_request_root(request_root)
    owner_uid = root_metadata.st_uid
    pending = [root]
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            metadata = _lstat_owned(child, owner_uid=owner_uid, label=label)
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(child)
            elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise CanonicalPrepError(
                    f"{label} contains a symlink or non-regular/aliased entry"
                )
    return root


def _resolve_authenticated(
    request_root: Path, value: str, expected: str, label: str
) -> Path:
    resolved = _request_owned_path(
        request_root, value, label=label, expect_directory=False
    )
    if _sha256_file(resolved) != expected:
        raise CanonicalPrepError(f"{label} SHA-256 mismatch")
    return resolved


def prepare(request_path: Path, plan_path: Path, assets_dir: Path, output: Path) -> None:
    request_path = Path(os.path.abspath(request_path))
    plan_path = Path(os.path.abspath(plan_path))
    if not _instrumented_confornets_runtime_available():
        raise CanonicalPrepError("canonical instrumented ConforNets runtime is unavailable")
    request_root = request_path.parent
    _, root_metadata = _resolved_request_root(request_root)
    owner_uid = root_metadata.st_uid
    request_metadata = _lstat_owned(
        request_path, owner_uid=owner_uid, label="canonical request"
    )
    plan_metadata = _lstat_owned(
        plan_path, owner_uid=owner_uid, label="coordinate plan"
    )
    if request_path.parent != plan_path.parent:
        raise CanonicalPrepError("coordinate plan must share the canonical request root")
    if not stat.S_ISREG(request_metadata.st_mode) or request_metadata.st_nlink != 1:
        raise CanonicalPrepError("canonical request must be an unaliased regular file")
    if not stat.S_ISREG(plan_metadata.st_mode) or plan_metadata.st_nlink != 1:
        raise CanonicalPrepError("coordinate plan must be an unaliased regular file")
    request = _load(request_path, "canonical request")
    plan = _load(plan_path, "coordinate plan")
    request_without_hash = {key: value for key, value in request.items() if key != "request_sha256"}
    if _canonical_sha256(request_without_hash) != request.get("request_sha256"):
        raise CanonicalPrepError("canonical request SHA-256 mismatch")
    plan_without_hash = {key: value for key, value in plan.items() if key != "coordinate_plan_sha256"}
    if _canonical_sha256(plan_without_hash) != plan.get("coordinate_plan_sha256"):
        raise CanonicalPrepError("coordinate plan SHA-256 mismatch")
    if plan.get("request_sha256") != request["request_sha256"]:
        raise CanonicalPrepError("coordinate plan is not bound to canonical request")
    if request.get("backend") != "confornets" or plan.get("backend") != "confornets":
        raise CanonicalPrepError("canonical prep supports only confornets")
    settings = request.get("confornets")
    if not isinstance(settings, dict):
        raise CanonicalPrepError("canonical ConforNets settings are missing")
    targets = request.get("targets")
    if not isinstance(targets, list) or len(targets) != 1:
        raise CanonicalPrepError("canonical ConforNets prep requires exactly one target")
    target = targets[0]
    if target.get("sequence") != settings.get("sequence"):
        raise CanonicalPrepError("canonical target sequence mismatch")

    checkpoint = _resolve_authenticated(
        request_root,
        settings["checkpoint"]["path"],
        settings["checkpoint"]["sha256"],
        "checkpoint",
    )
    repo_path = Path(settings["backend_identity"]["repo_path"])
    if repo_path != _CANONICAL_CONFORNETS_REPO or not _instrumented_confornets_runtime_available():
        raise CanonicalPrepError("canonical instrumented ConforNets runtime is unavailable")
    registry = _load(request_root / "cm_runtime_registry_v1.json", "runtime registry")
    for field in (
        "backend_version", "backend_commit", "runtime_identity", "container_digest",
        "model_id", "feature_identity_sha256",
    ):
        if registry.get(field) != settings["backend_identity"].get(field):
            raise CanonicalPrepError(f"ConforNets runtime registry mismatch: {field}")

    if assets_dir.exists():
        raise CanonicalPrepError(f"assets output already exists: {assets_dir}")
    benchmark = settings["benchmark_name"]
    test_case = settings["test_case_id"]
    test_case_root = assets_dir / benchmark / "test_cases" / test_case
    reference_root = test_case_root / "reference"
    query_root = test_case_root / "query"
    reference_root.mkdir(parents=True)
    query_root.mkdir(parents=True)

    native_references: list[dict[str, Any]] = []
    for reference in settings["references"]:
        source = _resolve_authenticated(
            request_root,
            reference["staged_path"],
            reference["content_sha256"],
            f"reference {reference['reference_id']}",
        )
        destination = reference_root / f"{reference['reference_id']}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        native_references.append(
            {
                "name": reference["reference_id"],
                "path": str(destination.resolve()),
                "staged_path": str(destination.resolve()),
                "sha256": reference["content_sha256"],
                "state": reference["state"],
                "source": reference["source"],
            }
        )

    query = {
        "chains": [
            {
                "molecule_type": "PROTEIN",
                "chain_ids": [settings["chain_id"]],
                "sequence": settings["sequence"],
            }
        ],
    }
    (query_root / f"{test_case}.json").write_text(json.dumps(query, indent=2), encoding="utf-8")
    with (assets_dir / benchmark / "references.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["test_case", "pdbidchain1", "pdbidchain2"])
        writer.writeheader()
        writer.writerow(
            {
                "test_case": test_case,
                "pdbidchain1": native_references[0]["name"] if native_references else "",
                "pdbidchain2": native_references[1]["name"] if len(native_references) > 1 else "",
            }
        )
    (test_case_root / "align_metric_info.json").write_text(
        json.dumps({"queries": {}, "references": {}}, indent=2), encoding="utf-8"
    )
    (assets_dir / "config.yaml").write_text(
        "defaults:\n  rmsd_threshold: 3.0\nbenchmarks:\n"
        f"  {benchmark}:\n    rmsd_threshold: 3.0\n",
        encoding="utf-8",
    )
    (assets_dir / "canonical_execution_context.json").write_bytes(
        _canonical_bytes(
            {
                "request_sha256": request["request_sha256"],
                "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
                "coordinates": plan["coordinates"],
            }
        )
    )

    config_path = ""
    if settings["config"] is not None:
        config_path = str(
            _resolve_authenticated(
                request_root,
                settings["config"]["path"],
                settings["config"]["sha256"],
                "ConforNets config",
            )
        )
    transfer_path = ""
    mse_dir = ""
    source_test_cases = ""
    if settings["transfer_source"] is not None:
        transfer = settings["transfer_source"]
        resolved_transfer = _resolve_authenticated(
            request_root,
            transfer["staged_path"],
            transfer["content_sha256"],
            "ConforNets transfer source",
        )
        if transfer["kind"] == "confornet_state":
            transfer_path = str(resolved_transfer)
        else:
            mse_dir = str(resolved_transfer)
        source_test_cases = transfer["source_test_cases"]

    native = {
        "schema_version": 1,
        "workflow": "confornets_experimental",
        "job_id": request["request_id"],
        "job_name": "conformational_mapping",
        "task": settings["task"],
        "benchmark": benchmark,
        "test_case": test_case,
        "query_id": test_case,
        "assets_dir": str(assets_dir.resolve()),
        "sequence": settings["sequence"],
        "chain_id": settings["chain_id"],
        "references": native_references,
        "backend_identity": settings["backend_identity"],
        "params": {
            "checkpoint_path": str(checkpoint),
            "config_yaml": config_path,
            "confornets_repo_path": str(repo_path),
            "skip_msa": settings["skip_msa"],
            "num_runs": settings["runs"],
            "k_confornets": settings["confornet_count"],
            "num_samples": settings["samples"],
            "max_steps": settings["max_steps"],
            "save_steps": settings["saved_steps"],
            "num_recycles": settings["num_recycles"],
            "num_diffusion_steps": settings["num_diffusion_steps"],
            "lr": settings["learning_rate"],
            "grad_clip": settings["gradient_clip"],
            "compute_confidence": settings["compute_confidence"],
            "save_full_confidence": settings["save_full_confidence"],
            "compute_evaluation": settings["compute_evaluation"],
            "confornet_path": transfer_path,
            "mse_dir": mse_dir,
            "source_test_cases": source_test_cases,
        },
        "input_hashes": {
            "sequence_sha256": hashlib.sha256(settings["sequence"].encode()).hexdigest(),
            "checkpoint_sha256": settings["checkpoint"]["sha256"],
            "references": {
                reference["reference_id"]: reference["content_sha256"]
                for reference in settings["references"]
            },
        },
        "upstream_contract": {
            "monomer_only": True,
            "max_reference_states": 2,
            "outputs_are_real_upstream_artifacts": True,
        },
        "canonical_binding": {
            "request_sha256": request["request_sha256"],
            "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
            "coordinates": plan["coordinates"],
            "target_id": target["target_id"],
            "coordinate_mapping": {
                "target_id": {"constant": target["target_id"]},
            },
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_canonical_bytes(native))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--coordinate-plan", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.request, args.coordinate_plan, args.assets_dir, args.output)
    except (CanonicalPrepError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"Canonical ConforNets prep failed: {exc}\n")
    print(f"Wrote authenticated native ConforNets request: {args.output}")


if __name__ == "__main__":
    main()
