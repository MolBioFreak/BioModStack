#!/usr/bin/env python3
"""Normalize, score, and index one canonical conformational ensemble."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.analysis import analyze_landscapes  # noqa: E402
from services.conformational_mapping.clash import build_clash_rows  # noqa: E402
from services.conformational_mapping.contracts import canonical_json_bytes, canonical_sha256  # noqa: E402
from services.conformational_mapping.frustrampnn_adapter import (  # noqa: E402
    bind_cm_candidate_snapshot_bytes,
    normalize_cm_structure,
    project_cm_landscape,
    project_cm_structure_map,
)
from services.conformational_mapping.resampling import pair_terminal_manifests  # noqa: E402
from services.conformational_mapping.state_landscape_analysis import (  # noqa: E402
    derive_state_landscape_analysis_for_request,
)
from services.frustrampnn.analysis import (  # noqa: E402
    finalize_landscape as finalize_neutral_landscape,
)
from services.frustrampnn.structure import read_structure_bytes  # noqa: E402
from services.frustrampnn import runtime as _frustrampnn_runtime  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(root: Path, path: Path, role: str, candidate_id: str | None) -> dict[str, object]:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "semantic_role": role,
        "candidate_id": candidate_id,
    }


def _candidate_structure_path(root: Path, relative_value: object) -> Path:
    """Preserve path components so the adapter can reject symlinks no-follow."""

    if not isinstance(relative_value, str) or not relative_value or "\\" in relative_value:
        raise RuntimeError("candidate structure path is unsafe")
    relative = Path(relative_value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RuntimeError("candidate structure path must be a safe relative path")
    return root / relative


def _container_sha256(
    apptainer: str, container: Path, internal_path: str, *, container_fd: int | None = None,
) -> str:
    return _frustrampnn_runtime.container_sha256(
        apptainer, container, internal_path, container_fd=container_fd,
    )


def _sha256_fd(fd: int) -> str:
    return _frustrampnn_runtime.sha256_fd(fd)


def _open_verified_container(path: Path, expected_sha256: object) -> tuple[int, str]:
    return _frustrampnn_runtime.open_verified_container(path, expected_sha256).detach()


def _frustrampnn_command(
    *, apptainer: str, container: Path, tool: str, normalized: Path,
    checkpoint: Path, raw: Path, output_root: Path, gpu_id: int,
) -> _frustrampnn_runtime.FrustraMPNNInvocation:
    return _frustrampnn_runtime.build_frustrampnn_command(
        apptainer=apptainer,
        container=container,
        tool=tool,
        normalized=normalized,
        checkpoint=checkpoint,
        raw=raw,
        output_root=output_root,
        physical_gpu_id=gpu_id,
    )



def _main(active_pins: list[_frustrampnn_runtime.PinnedContainer]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--runtime-registry", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--frustrampnn-container", type=Path, required=True)
    parser.add_argument("--frustrampnn-bin", default="/opt/venv/bin/frustrampnn")
    parser.add_argument("--apptainer-bin", default="apptainer")
    parser.add_argument("--gpu-id", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.gpu_id < 0:
        parser.error("--gpu-id must be non-negative")
    configured_container = Path(
        _frustrampnn_runtime.validate_configured_container_path(
            args.frustrampnn_container,
        )
    )

    request = json.loads(args.request.read_text(encoding="utf-8"))
    runtime_registry = json.loads(args.runtime_registry.read_text(encoding="utf-8"))
    snapshots_value = json.loads(args.snapshots.read_text(encoding="utf-8"))
    snapshots = snapshots_value if isinstance(snapshots_value, list) else [snapshots_value]
    snapshot_by_target = {item["target_id"]: item for item in snapshots}
    if args.out.exists():
        raise RuntimeError("analysis-plane output already exists")
    shutil.copytree(
        args.canonical, args.out, copy_function=shutil.copy2, symlinks=True,
    )
    ensemble = json.loads((args.out / "cm_ensemble_v1.json").read_text(encoding="utf-8"))
    derived = args.out / "derived"
    records: list[dict[str, object]] = []
    landscapes: dict[str, dict[str, object]] = {}
    maps: list[dict[str, object]] = []
    clash_rows: dict[tuple[str, tuple[object, ...], str], dict[str, object]] = {}
    analysis_runtime = runtime_registry.get("analysis_runtime")
    if not isinstance(analysis_runtime, dict) or set(analysis_runtime) != {
        "container_name", "container_sha256",
    }:
        raise RuntimeError("registered FrustraMPNN image identity is missing or malformed")
    if analysis_runtime["container_name"] != configured_container.name:
        raise RuntimeError("registered FrustraMPNN image name does not match the selected container")
    container_fd, container_sha256 = _open_verified_container(
        configured_container, analysis_runtime["container_sha256"]
    )
    container_pin = _frustrampnn_runtime.PinnedContainer(
        container_fd, container_sha256
    )
    active_pins.append(container_pin)
    container = Path(f"/proc/self/fd/{container_fd}")
    apptainer = shutil.which(args.apptainer_bin)
    if not apptainer:
        raise RuntimeError("registered Apptainer executable is unavailable")
    checkpoint_sha256 = _container_sha256(
        apptainer, container, str(args.checkpoint), container_fd=container_fd,
    )
    tool_sha256 = _container_sha256(
        apptainer, container, args.frustrampnn_bin, container_fd=container_fd,
    )
    identity = _frustrampnn_runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
    if checkpoint_sha256 != identity.checkpoint_sha256:
        raise RuntimeError(
            "FrustraMPNN checkpoint SHA-256 does not match the central runtime registry"
        )
    if tool_sha256 != identity.executable_sha256:
        raise RuntimeError(
            "FrustraMPNN executable SHA-256 does not match the central runtime registry"
        )

    for candidate in ensemble["candidates"]:
        candidate_id = candidate["candidate_id"]
        target_id = candidate["backend_coordinates"]["target_id"]
        snapshot = snapshot_by_target.get(target_id)
        if snapshot is None:
            raise RuntimeError(f"candidate target has no authoritative complex snapshot: {target_id}")
        structure = _candidate_structure_path(
            args.out, candidate["authoritative_structure_path"],
        )
        if structure.suffix.lower() not in {".cif", ".mmcif", ".pdb"}:
            raise RuntimeError("canonical analysis requires an authoritative coordinate structure")
        source_bytes = read_structure_bytes(structure)
        candidate_snapshot = bind_cm_candidate_snapshot_bytes(
            snapshot,
            candidate_id=candidate_id,
            source_bytes=source_bytes,
            source_suffix=structure.suffix,
        )
        candidate_root = derived / candidate_id
        normalized = candidate_root / "normalized.pdb"
        map_path = candidate_root / "cm_structure_map_v1.json"
        neutral_map_path = candidate_root / ".frustrampnn_structure_map_v1.json"
        authority_path = candidate_root / ".frustrampnn_producer_manifest_v1.json"
        raw = candidate_root / "frustrampnn_raw.csv"
        runtime_output = candidate_root / ".frustrampnn-runtime-output"
        raw_runtime = runtime_output / raw.name
        landscape_path = candidate_root / "cm_frustration_landscape_v1.json"
        snapshot_path = candidate_root / "cm_candidate_complex_snapshot_v1.json"
        candidate_root.mkdir(parents=True)
        runtime_output.mkdir()
        snapshot_path.write_bytes(canonical_json_bytes(candidate_snapshot))
        neutral_map = normalize_cm_structure(
            input_path=structure,
            output_pdb_path=normalized,
            map_path=neutral_map_path,
            authority_artifact_path=authority_path,
            target_id=target_id,
            parent_job_id=request["request_id"],
            candidate_id=candidate_id,
            complex_snapshot=candidate_snapshot,
            selected_model=None,
            altloc_policy="blank_or_explicit:A",
            source_bytes=source_bytes,
        )
        structure_map = project_cm_structure_map(neutral_map, candidate_snapshot)
        map_path.write_bytes(canonical_json_bytes(structure_map) + b"\n")
        invocation = _frustrampnn_command(
                apptainer=apptainer, container=container, tool=args.frustrampnn_bin,
                normalized=normalized, checkpoint=args.checkpoint, raw=raw_runtime,
                output_root=runtime_output, gpu_id=args.gpu_id,
            )
        _frustrampnn_runtime.execute_frustrampnn(
            invocation,
            container_pin,
            check=True,
        )
        os.replace(raw_runtime, raw)
        runtime_output.rmdir()
        neutral_landscape = finalize_neutral_landscape(
            raw,
            neutral_map,
            expected_normalized_pdb_sha256=neutral_map["normalized_pdb_sha256"],
            expected_model_ready_sequence_sha256=neutral_map["model_ready_sequence_sha256"],
        )
        landscape = project_cm_landscape(
            neutral_landscape,
            checkpoint_id=args.checkpoint_id,
            checkpoint_sha256=checkpoint_sha256,
            tool_id="frustrampnn",
            tool_sha256=tool_sha256,
            container_sha256=container_sha256,
        )
        neutral_map_path.unlink()
        authority_path.unlink()
        landscape_path.write_bytes(canonical_json_bytes(landscape))
        candidate_clashes = build_clash_rows(
            normalized, structure_map, candidate_id=candidate_id,
            detector_id=request["analysis_policy"]["clash_detector_id"],
            detector_version=request["analysis_policy"]["clash_detector_version"],
        )
        overlap = set(clash_rows).intersection(candidate_clashes)
        if overlap:
            raise RuntimeError("clash detector emitted duplicate canonical identities")
        clash_rows.update(candidate_clashes)
        maps.append(structure_map)
        landscapes[candidate_id] = landscape
        records.extend([
            _record(args.out, snapshot_path, "candidate_complex_snapshot", candidate_id),
            _record(args.out, normalized, "normalized_pdb", candidate_id),
            _record(args.out, map_path, "structure_map", candidate_id),
            _record(args.out, raw, "frustrampnn_raw", candidate_id),
            _record(args.out, landscape_path, "frustration_landscape", candidate_id),
        ])

    comparisons: list[dict[str, object]] = []
    resampling_manifest: dict[str, object] | None = None
    pair_request_path = args.request.parent / "cm_resampling_pair_request_v1.json"
    if pair_request_path.is_file() and len(request["targets"]) == 2:
        pair_request = json.loads(pair_request_path.read_text(encoding="utf-8"))
        target_a, target_b = (item["target_id"] for item in request["targets"])
        candidates_a = [item for item in ensemble["candidates"] if item["backend_coordinates"]["target_id"] == target_a]
        candidates_b = [item for item in ensemble["candidates"] if item["backend_coordinates"]["target_id"] == target_b]
        ids_a = {item["candidate_id"] for item in candidates_a}
        ids_b = {item["candidate_id"] for item in candidates_b}
        coordinates_a = {
            (str(item["backend_coordinates"]["ordered_seed"]), str(item["backend_coordinates"]["sample_index"])): item["candidate_id"]
            for item in candidates_a
        }
        coordinates_b = {
            (str(item["backend_coordinates"]["ordered_seed"]), str(item["backend_coordinates"]["sample_index"])): item["candidate_id"]
            for item in candidates_b
        }
        if set(coordinates_a) != set(coordinates_b):
            raise RuntimeError("state-conditioned comparison has unmatched candidate coordinates")
        mutation = pair_request["substitution"]
        residue = next(
            row for candidate_id in ids_a for row in landscapes[candidate_id]["residues"]
            if row["entity_instance_id"] == mutation["entity_instance_id"]
            and row["sequence_index"] == mutation["sequence_index"]
        )
        invariant_fields = {
            "runtime_policy_sha256": canonical_sha256(request["runtime_policy"]),
            "feature_policy_mode": request["feature_policy"]["mode"],
            "pair_id": pair_request["pair_id"],
            "tool_identity_sha256": canonical_sha256(pair_request["tool_identity"]),
        }
        comparisons.append({
            "comparison_id": pair_request["pair_id"],
            "ensemble_a": {**ensemble, "candidates": candidates_a, "expected_cardinality": len(candidates_a)},
            "ensemble_b": {**ensemble, "candidates": candidates_b, "expected_cardinality": len(candidates_b)},
            "landscapes_a": [landscapes[key] for key in sorted(ids_a)],
            "landscapes_b": [landscapes[key] for key in sorted(ids_b)],
            "invariant_fields_a": invariant_fields,
            "invariant_fields_b": invariant_fields,
            "mutated_residue_keys": [canonical_sha256(list((
                residue["entity_instance_id"], residue["auth_asym_id"], residue["auth_seq_id"],
                residue.get("insertion_code") or "", residue["sequence_index"],
            )))],
        })
        resampling_manifest = pair_terminal_manifests(
            pair_request,
            {**ensemble, "candidates": candidates_a, "expected_cardinality": len(candidates_a)},
            {**ensemble, "candidates": candidates_b, "expected_cardinality": len(candidates_b)},
        )
        if resampling_manifest["terminal_status"] != "complete":
            raise RuntimeError("matched WT/mutant terminal coordinate set is incomplete")
    analysis = analyze_landscapes(
        ensemble, landscapes, policy=request["analysis_policy"], clash_rows=clash_rows,
        comparisons=comparisons,
    )
    analysis_path = derived / "cm_analysis_v1.json"
    analysis_path.write_bytes(canonical_json_bytes(analysis))
    records.append(_record(args.out, analysis_path, "analysis", None))
    state_landscape_analysis: dict[str, object] | None = None
    state_landscape_analysis = derive_state_landscape_analysis_for_request(
        request, ensemble, [landscapes[key] for key in sorted(landscapes)], maps,
    )
    if state_landscape_analysis is not None:
        state_path = derived / "cm_state_landscape_analysis_v1.json"
        state_path.write_bytes(canonical_json_bytes(state_landscape_analysis))
        records.append(_record(args.out, state_path, "state_landscape_analysis", None))
    support = {
        "schema_name": "cm_support", "schema_version": 1,
        "request_id": request["request_id"], "analysis_id": analysis["analysis_id"],
        "source_analysis_sha256": canonical_sha256(analysis),
        "records": analysis["support_records"], "pair_ledger": analysis["pair_ledger"],
        "ranking_policy": analysis["ranking_policy"], "clash_records": analysis["clash_records"],
    }
    missingness = {
        "schema_name": "cm_missingness", "schema_version": 1,
        "request_id": request["request_id"], "analysis_id": analysis["analysis_id"],
        "source_analysis_sha256": canonical_sha256(analysis),
        "coordinate_exclusions": analysis["exclusions"],
        "result_records": [
            {
                "source_row_key": row["source_row_key"], "status": row["status"],
                "failure_reason": row["failure_reason"],
                "expected_coordinate_count": row["expected_coordinate_count"],
                "valid_coordinate_count": row["valid_coordinate_count"],
                "clash_exclusions": row["components"].get("clash_exclusions", []),
            }
            for row in analysis["results"]
        ],
    }
    lineage = {
        "schema_name": "cm_lineage", "schema_version": 1,
        "request_id": request["request_id"], "request_sha256": request["request_sha256"],
        "backend": request["backend"], "source_ensemble_sha256": canonical_sha256(ensemble),
        "source_snapshot_sha256": canonical_sha256(snapshots),
        "resampling_pair_id": resampling_manifest["pair_id"] if resampling_manifest else None,
    }
    for path, payload, role in (
        (derived / "cm_support_v1.json", support, "support"),
        (derived / "cm_missingness_v1.json", missingness, "missingness"),
        (derived / "cm_lineage_v1.json", lineage, "lineage"),
    ):
        path.write_bytes(canonical_json_bytes(payload))
        records.append(_record(args.out, path, role, None))
    if resampling_manifest is not None:
        resampling_path = derived / "cm_resampling_terminal_manifest_v1.json"
        resampling_path.write_bytes(canonical_json_bytes(resampling_manifest))
        records.append(_record(args.out, resampling_path, "resampling", None))
    index_without_hash = {
        "schema_name": "cm_derived_index", "schema_version": 1,
        "request_id": request["request_id"],
        "source_ensemble_sha256": canonical_sha256(ensemble),
        "records": records,
        "structure_maps": maps,
        "landscapes": [landscapes[key] for key in sorted(landscapes)],
        "analysis": analysis,
        "lineage": lineage,
        "support": support,
        "missingness": missingness,
        "resampling": resampling_manifest,
    }
    if state_landscape_analysis is not None:
        index_without_hash["state_landscape_analyses"] = [state_landscape_analysis]
    index = {**index_without_hash, "index_sha256": canonical_sha256(index_without_hash)}
    (args.out / "cm_derived_index_v1.json").write_bytes(canonical_json_bytes(index))
    container_pin.close()
    active_pins.remove(container_pin)


def main() -> None:
    active_pins: list[_frustrampnn_runtime.PinnedContainer] = []
    try:
        _main(active_pins)
    finally:
        for pin in reversed(active_pins):
            pin.close()


if __name__ == "__main__":
    main()
