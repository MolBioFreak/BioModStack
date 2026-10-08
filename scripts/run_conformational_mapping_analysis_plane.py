#!/usr/bin/env python3
"""Normalize, score, and index one canonical conformational ensemble."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.analysis import analyze_landscapes  # noqa: E402
from services.conformational_mapping.clash import build_clash_rows  # noqa: E402
from services.conformational_mapping.contracts import canonical_json_bytes, canonical_sha256  # noqa: E402
from services.conformational_mapping.frustration import finalize_landscape  # noqa: E402
from services.conformational_mapping.resampling import pair_terminal_manifests  # noqa: E402
from services.conformational_mapping.structure_normalizer import (  # noqa: E402
    bind_candidate_complex_snapshot,
    normalize_conformational_mapping_structure,
)


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    request = json.loads(args.request.read_text(encoding="utf-8"))
    snapshots_value = json.loads(args.snapshots.read_text(encoding="utf-8"))
    snapshots = snapshots_value if isinstance(snapshots_value, list) else [snapshots_value]
    snapshot_by_target = {item["target_id"]: item for item in snapshots}
    if args.out.exists():
        raise RuntimeError("analysis-plane output already exists")
    shutil.copytree(args.canonical, args.out, copy_function=shutil.copy2)
    ensemble = json.loads((args.out / "cm_ensemble_v1.json").read_text(encoding="utf-8"))
    derived = args.out / "derived"
    records: list[dict[str, object]] = []
    landscapes: dict[str, dict[str, object]] = {}
    maps: list[dict[str, object]] = []
    clash_rows: dict[tuple[str, tuple[object, ...], str], dict[str, object]] = {}
    checkpoint_sha256 = _sha256(args.checkpoint)
    tool_path_value = shutil.which("frustrampnn")
    if not tool_path_value:
        raise RuntimeError("registered FrustraMPNN executable is unavailable")
    tool_path = Path(tool_path_value).resolve(strict=True)
    if not tool_path.is_file() or tool_path.is_symlink():
        raise RuntimeError("registered FrustraMPNN executable is not a regular file")
    tool_sha256 = _sha256(tool_path)

    for candidate in ensemble["candidates"]:
        candidate_id = candidate["candidate_id"]
        target_id = candidate["backend_coordinates"]["target_id"]
        snapshot = snapshot_by_target.get(target_id)
        if snapshot is None:
            raise RuntimeError(f"candidate target has no authoritative complex snapshot: {target_id}")
        structure = (args.out / candidate["authoritative_structure_path"]).resolve(strict=True)
        structure.relative_to(args.out.resolve())
        if structure.suffix.lower() not in {".cif", ".mmcif", ".pdb"}:
            raise RuntimeError("canonical analysis requires an authoritative coordinate structure")
        candidate_snapshot = bind_candidate_complex_snapshot(
            snapshot, candidate_id=candidate_id, structure_path=structure,
        )
        candidate_root = derived / candidate_id
        normalized = candidate_root / "normalized.pdb"
        map_path = candidate_root / "cm_structure_map_v1.json"
        raw = candidate_root / "frustrampnn_raw.csv"
        landscape_path = candidate_root / "cm_frustration_landscape_v1.json"
        snapshot_path = candidate_root / "cm_candidate_complex_snapshot_v1.json"
        candidate_root.mkdir(parents=True)
        snapshot_path.write_bytes(canonical_json_bytes(candidate_snapshot))
        structure_map = normalize_conformational_mapping_structure(
            input_path=structure, output_pdb_path=normalized, map_path=map_path,
            target_id=target_id, candidate_id=candidate_id,
            complex_snapshot=candidate_snapshot,
        )
        subprocess.run(
            ["frustrampnn", "predict", "--pdb", str(normalized),
             "--checkpoint", str(args.checkpoint), "--output", str(raw)],
            check=True,
        )
        landscape = finalize_landscape(
            raw, structure_map, checkpoint_id=args.checkpoint_id,
            checkpoint_sha256=checkpoint_sha256,
            tool_id="frustrampnn",
            tool_sha256=tool_sha256,
        )
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
    index = {**index_without_hash, "index_sha256": canonical_sha256(index_without_hash)}
    (args.out / "cm_derived_index_v1.json").write_bytes(canonical_json_bytes(index))


if __name__ == "__main__":
    main()
