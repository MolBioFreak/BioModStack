#!/usr/bin/env python3
"""Prepare every canonical CM ensemble candidate for global FrustraMPNN v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.contracts import canonical_json_bytes  # noqa: E402
from services.conformational_mapping.frustrampnn_adapter import (  # noqa: E402
    prepare_cm_candidate_v2,
)
from services.frustrampnn.settings import (  # noqa: E402
    validate_persisted_requested_settings,
)


_CANDIDATE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _safe_candidate_source(canonical_dir: Path, relative_value: object) -> Path:
    if not isinstance(relative_value, str) or not relative_value or "\\" in relative_value:
        raise ValueError("candidate authoritative structure path is missing or unsafe")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or relative.as_posix() != relative_value or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ValueError("candidate authoritative structure path is noncanonical")
    source = canonical_dir.joinpath(*relative.parts)
    if source.is_symlink() or not source.is_file():
        raise ValueError("candidate authoritative structure is missing or not a regular file")
    return source


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_ensemble_candidates(
    *,
    parent_job_id: str,
    request_path: Path,
    snapshots_path: Path,
    canonical_dir: Path,
    output_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    ensemble = json.loads(
        (canonical_dir / "cm_ensemble_v1.json").read_text(encoding="utf-8")
    )
    snapshots_value = json.loads(snapshots_path.read_text(encoding="utf-8"))
    snapshots = snapshots_value if isinstance(snapshots_value, list) else [snapshots_value]
    snapshot_by_target = {
        str(snapshot.get("target_id")): snapshot
        for snapshot in snapshots
        if isinstance(snapshot, Mapping)
    }
    candidates = ensemble.get("candidates")
    expected_cardinality = ensemble.get("expected_cardinality")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("CM canonical ensemble has no candidates")
    if expected_cardinality != len(candidates):
        raise ValueError("CM canonical ensemble candidate cardinality is incomplete")
    if request.get("frustrampnn_requiredness") != "required":
        raise ValueError("CM FrustraMPNN v2 candidates must be required")
    backend = request.get("backend")
    if ensemble.get("backend") not in {None, backend}:
        raise ValueError("CM ensemble backend does not match parent request")
    requested_settings = validate_persisted_requested_settings(
        request.get("frustrampnn_settings")
    )
    candidate_ids: list[str] = []
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id") if isinstance(candidate, Mapping) else None
        if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
            raise ValueError("CM candidate ID is missing or path-unsafe")
        if candidate_id in candidate_ids:
            raise ValueError("CM canonical ensemble has duplicate candidate IDs")
        coordinates = candidate.get("backend_coordinates")
        if not isinstance(coordinates, Mapping) or coordinates.get("backend") != backend:
            raise ValueError("CM candidate backend coordinates do not match parent request")
        candidate_ids.append(candidate_id)

    if output_dir.exists() or manifest_path.exists():
        raise ValueError("CM FrustraMPNN preparation output already exists")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".cm-frustrampnn-v2.", dir=output_dir.parent))
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
    published = False
    try:
        records: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_id = str(candidate["candidate_id"])
            coordinates = candidate["backend_coordinates"]
            target_id = str(coordinates["target_id"])
            snapshot = snapshot_by_target.get(target_id)
            if snapshot is None:
                raise ValueError(f"CM candidate target has no complex snapshot: {target_id}")
            source = _safe_candidate_source(
                canonical_dir, candidate["authoritative_structure_path"]
            )
            candidate_root = staging / candidate_id
            candidate_root.mkdir()
            request_file = candidate_root / "workflow_component_request_v3.json"
            normalized_file = candidate_root / "canonical_source.pdb"
            structure_map_file = candidate_root / "frustrampnn_structure_map_v1.json"
            component_request = prepare_cm_candidate_v2(
                source=source,
                output_pdb_path=normalized_file,
                structure_map_path=structure_map_file,
                request_path=request_file,
                authority_artifact_path=candidate_root / ".authority_artifact_v1.json",
                parent_job_id=parent_job_id,
                parent_workflow_id="conformational_mapping",
                candidate=candidate,
                complex_snapshot=snapshot,
                requested_settings=requested_settings,
            )
            records.append({
                "candidate_id": candidate_id,
                "invocation_id": component_request["invocation_id"],
                "backend_coordinates": dict(coordinates),
                "source_sha256": component_request["source_artifact"]["sha256"],
                "cm_complex_snapshot_sha256": component_request[
                    "identity_authority_artifact"
                ]["cm_complex_snapshot_sha256"],
                "requested_settings_sha256": component_request[
                    "requested_settings_sha256"
                ],
                "effective_settings_sha256": component_request[
                    "effective_settings_sha256"
                ],
                "request_sha256": _sha256(request_file),
                "tuple": [
                    f"{candidate_id}/workflow_component_request_v3.json",
                    f"{candidate_id}/canonical_source.pdb",
                    f"{candidate_id}/frustrampnn_structure_map_v1.json",
                ],
            })
        manifest = {
            "schema_name": "cm_frustrampnn_preparation_manifest",
            "schema_version": 1,
            "parent_job_id": parent_job_id,
            "parent_workflow_id": "conformational_mapping",
            "backend": backend,
            "requiredness": "required",
            "expected_cardinality": expected_cardinality,
            "candidates": records,
        }
        temporary_manifest.write_bytes(canonical_json_bytes(manifest))
        os.replace(staging, output_dir)
        published = True
        os.replace(temporary_manifest, manifest_path)
        return manifest
    except BaseException:
        if published:
            shutil.rmtree(output_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        temporary_manifest.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-job-id", required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        prepare_ensemble_candidates(
            parent_job_id=args.parent_job_id,
            request_path=args.request,
            snapshots_path=args.snapshots,
            canonical_dir=args.canonical,
            output_dir=args.output_dir,
            manifest_path=args.manifest,
        )
    except Exception as exc:
        print(f"cm_frustrampnn_v2_preparation_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
