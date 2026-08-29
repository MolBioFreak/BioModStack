#!/usr/bin/env python3
"""Postprocess manifest-validated modern FrustraMPNN bundles for CM."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.analysis import analyze_landscapes  # noqa: E402
from services.conformational_mapping.clash import build_clash_rows  # noqa: E402
from services.conformational_mapping.contracts import canonical_json_bytes, canonical_sha256  # noqa: E402
from services.conformational_mapping.resampling import pair_terminal_manifests  # noqa: E402
from services.conformational_mapping.state_landscape_analysis import (  # noqa: E402
    derive_state_landscape_analysis_for_request,
)
from services.frustrampnn.manifests import (  # noqa: E402
    V2_MANIFEST_PATH,
    V3_MANIFEST_PATH,
    load_result_manifest_bytes_and_document,
    validate_result_manifest,
)


class CMFrustraMPNNPostprocessError(RuntimeError):
    """A required canonical FrustraMPNN v2 result is absent or stale."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(root: Path, path: Path, role: str) -> dict[str, object]:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "semantic_role": role,
        "candidate_id": None,
    }


def _decode(payloads: Mapping[str, bytes], name: str) -> dict[str, Any]:
    try:
        value = json.loads(payloads[name])
    except Exception as exc:
        raise CMFrustraMPNNPostprocessError(
            f"validated canonical bundle artifact is unreadable: {name}"
        ) from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != payloads[name]:
        raise CMFrustraMPNNPostprocessError(
            f"validated canonical bundle artifact is not canonical JSON: {name}"
        )
    return value


def _exact_binding(
    prepared: Mapping[str, Any],
    request: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    scheduler_child = prepared.get("scheduler_child") is True
    exact = (
        request.get("candidate_id") == prepared.get("candidate_id")
        and request.get("invocation_id") == prepared.get("invocation_id")
        and request.get("parent_job_id") == prepared.get("parent_job_id")
        and request.get("parent_workflow_id") == "conformational_mapping"
        and request.get("requiredness") == "required"
        and request.get("source_artifact", {}).get("sha256") == prepared.get("source_sha256")
        and (
            scheduler_child
            or request.get("identity_authority_artifact", {}).get(
                "cm_complex_snapshot_sha256"
            ) == prepared.get("cm_complex_snapshot_sha256")
        )
        and request.get("requested_settings_sha256")
        == prepared.get("requested_settings_sha256")
        and request.get("effective_settings_sha256")
        == prepared.get("effective_settings_sha256")
        and result.get("candidate_id") == prepared.get("candidate_id")
        and result.get("invocation_id") == prepared.get("invocation_id")
        and result.get("parent_job_id") == prepared.get("parent_job_id")
        and result.get("status") == "succeeded"
    )
    if not exact:
        raise CMFrustraMPNNPostprocessError(
            f"required canonical result binding failed: {prepared.get('candidate_id')}"
        )


def postprocess_canonical_bundles(
    *,
    request_path: Path,
    canonical_dir: Path,
    preparation_manifest_path: Path,
    bundle_dirs: Sequence[Path],
    output_dir: Path,
    scheduler_terminal_receipt_path: Path | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise CMFrustraMPNNPostprocessError("CM postprocessing output already exists")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    ensemble = json.loads(
        (canonical_dir / "cm_ensemble_v1.json").read_text(encoding="utf-8")
    )
    preparation = json.loads(preparation_manifest_path.read_text(encoding="utf-8"))
    candidates = ensemble.get("candidates")
    prepared_rows = preparation.get("candidates")
    expected = ensemble.get("expected_cardinality")
    if (
        not isinstance(candidates, list)
        or not isinstance(prepared_rows, list)
        or expected != len(candidates)
        or expected != preparation.get("expected_cardinality")
        or expected != len(prepared_rows)
        or expected != len(bundle_dirs)
        or not isinstance(preparation.get("parent_job_id"), str)
        or not preparation["parent_job_id"]
    ):
        raise CMFrustraMPNNPostprocessError(
            "required canonical result cardinality/parent binding is incomplete"
        )
    candidate_ids = [item.get("candidate_id") for item in candidates]
    prepared_ids = [item.get("candidate_id") for item in prepared_rows]
    if len(set(candidate_ids)) != expected or candidate_ids != prepared_ids:
        raise CMFrustraMPNNPostprocessError(
            "canonical result candidate identity/order differs from CM ensemble"
        )
    prepared_by_id = {str(item["candidate_id"]): item for item in prepared_rows}
    scheduler_by_id: dict[str, dict[str, Any]] = {}
    if scheduler_terminal_receipt_path is not None:
        scheduler_terminal = json.loads(
            scheduler_terminal_receipt_path.read_text(encoding="utf-8")
        )
        if (
            scheduler_terminal.get("schema_name")
            != "bms.frustrampnn.parent-fanout-terminal.v1"
            or scheduler_terminal.get("parent_job_id") != preparation["parent_job_id"]
            or scheduler_terminal.get("parent_workflow_id") != "conformational_mapping"
            or scheduler_terminal.get("status") != "complete"
        ):
            raise CMFrustraMPNNPostprocessError("scheduler terminal receipt is invalid")
        for child in scheduler_terminal.get("child_receipts") or []:
            child_id = child.get("job_id") if isinstance(child, Mapping) else None
            for candidate in child.get("candidates") or []:
                candidate_id = candidate.get("candidate_id")
                if candidate_id in scheduler_by_id:
                    raise CMFrustraMPNNPostprocessError("scheduler candidate receipt is duplicate")
                scheduler_by_id[str(candidate_id)] = {
                    "scheduler_child": True,
                    "parent_job_id": child_id,
                    "invocation_id": candidate.get("invocation_id"),
                    "request_sha256": candidate.get("component_request_sha256"),
                    "source_sha256": candidate.get("source_artifact_sha256"),
                    "requested_settings_sha256": candidate.get("requested_settings_sha256"),
                    "effective_settings_sha256": candidate.get("effective_settings_sha256"),
                }
        if set(scheduler_by_id) != set(prepared_by_id):
            raise CMFrustraMPNNPostprocessError("scheduler terminal candidate set is incomplete")

    loaded: dict[str, dict[str, Any]] = {}
    for bundle in bundle_dirs:
        manifest_name, manifest_bytes, manifest = load_result_manifest_bytes_and_document(bundle)
        generation = manifest.get("schema_version")
        expected_manifest_name = {
            2: V2_MANIFEST_PATH,
            3: V3_MANIFEST_PATH,
        }.get(generation)
        if manifest_name != expected_manifest_name:
            raise CMFrustraMPNNPostprocessError(
                "CM postprocessing requires a recognized modern result manifest"
            )
        payloads = validate_result_manifest(bundle, manifest)
        request_name = f"workflow_component_request_v{generation}.json"
        result_name = f"workflow_component_result_v{generation}.json"
        landscape_name = f"frustrampnn_landscape_v{generation}.json"
        component_request = _decode(payloads, request_name)
        component_result = _decode(payloads, result_name)
        candidate_id = str(component_request.get("candidate_id") or "")
        if candidate_id in loaded or candidate_id not in prepared_by_id:
            raise CMFrustraMPNNPostprocessError(
                "canonical result candidate identity is duplicate or unexpected"
            )
        prepared = {**prepared_by_id[candidate_id], "parent_job_id": preparation["parent_job_id"]}
        if scheduler_by_id:
            prepared.update(scheduler_by_id[candidate_id])
        if hashlib.sha256(payloads[request_name]).hexdigest() != prepared[
            "request_sha256"
        ]:
            raise CMFrustraMPNNPostprocessError(
                f"canonical result request bytes are stale: {candidate_id}"
            )
        _exact_binding(prepared, component_request, component_result)
        landscape = _decode(payloads, landscape_name)
        structure_map = _decode(payloads, "frustrampnn_structure_map_v1.json")
        if (
            landscape.get("schema_name") != "frustrampnn_landscape"
            or landscape.get("schema_version") != generation
            or landscape.get("candidate_id") != candidate_id
            or landscape.get("source_artifact_sha256") != prepared["source_sha256"]
            or structure_map.get("candidate_id") != candidate_id
        ):
            raise CMFrustraMPNNPostprocessError(
                f"canonical landscape/map candidate or source binding is stale: {candidate_id}"
            )
        loaded[candidate_id] = {
            "bundle": Path(bundle),
            "manifest": manifest,
            "manifest_bytes": manifest_bytes,
            "payloads": payloads,
            "landscape_name": landscape_name,
            "landscape": landscape,
            "structure_map": structure_map,
            "execution_binding": prepared,
        }
    if set(loaded) != set(candidate_ids):
        raise CMFrustraMPNNPostprocessError(
            "required canonical result set is incomplete"
        )

    try:
        shutil.copytree(canonical_dir, output_dir)
        derived = output_dir / "derived"
        derived.mkdir(exist_ok=True)
        global_results = output_dir / "frustrampnn" / "results"
        global_results.mkdir(parents=True)
        references: list[dict[str, Any]] = []
        landscapes: dict[str, dict[str, Any]] = {}
        structure_maps: list[dict[str, Any]] = []
        clash_rows: dict[tuple[str, str, int, str], bool] = {}
        for candidate_id in candidate_ids:
            item = loaded[str(candidate_id)]
            prepared = prepared_by_id[str(candidate_id)]
            execution_binding = item["execution_binding"]
            destination = global_results / str(candidate_id)
            shutil.copytree(item["bundle"], destination)
            copied_payloads = validate_result_manifest(destination, item["manifest"])
            if copied_payloads != item["payloads"]:
                raise CMFrustraMPNNPostprocessError(
                    f"copied canonical result bundle changed: {candidate_id}"
                )
            normalized = destination / "normalized_input.pdb"
            candidate_clashes = build_clash_rows(
                normalized,
                item["structure_map"],
                candidate_id=str(candidate_id),
                detector_id=request["analysis_policy"]["clash_detector_id"],
                detector_version=request["analysis_policy"]["clash_detector_version"],
            )
            if set(clash_rows).intersection(candidate_clashes):
                raise CMFrustraMPNNPostprocessError(
                    "clash detector emitted duplicate canonical identities"
                )
            clash_rows.update(candidate_clashes)
            landscapes[str(candidate_id)] = item["landscape"]
            structure_maps.append(item["structure_map"])
            references.append({
                "candidate_id": candidate_id,
                "invocation_id": execution_binding["invocation_id"],
                "source_sha256": execution_binding["source_sha256"],
                "cm_complex_snapshot_sha256": prepared[
                    "cm_complex_snapshot_sha256"
                ],
                "requested_settings_sha256": prepared[
                    "requested_settings_sha256"
                ],
                "effective_settings_sha256": execution_binding[
                    "effective_settings_sha256"
                ],
                "bundle_relative_path": destination.relative_to(output_dir).as_posix(),
                "result_manifest_sha256": hashlib.sha256(
                    item["manifest_bytes"]
                ).hexdigest(),
                "landscape_sha256": hashlib.sha256(
                    item["payloads"][item["landscape_name"]]
                ).hexdigest(),
                "structure_map_sha256": hashlib.sha256(
                    item["payloads"]["frustrampnn_structure_map_v1.json"]
                ).hexdigest(),
            })

        comparisons: list[dict[str, object]] = []
        resampling_manifest: dict[str, object] | None = None
        pair_request_path = request_path.parent / "cm_resampling_pair_request_v1.json"
        if pair_request_path.is_file() and len(request["targets"]) == 2:
            pair_request = json.loads(pair_request_path.read_text(encoding="utf-8"))
            target_a, target_b = (item["target_id"] for item in request["targets"])
            candidates_a = [item for item in candidates if item["backend_coordinates"]["target_id"] == target_a]
            candidates_b = [item for item in candidates if item["backend_coordinates"]["target_id"] == target_b]
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
                raise CMFrustraMPNNPostprocessError(
                    "state-conditioned comparison has unmatched candidate coordinates"
                )
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
                raise CMFrustraMPNNPostprocessError(
                    "matched WT/mutant terminal coordinate set is incomplete"
                )

        analysis = analyze_landscapes(
            ensemble, landscapes, policy=request["analysis_policy"],
            clash_rows=clash_rows, comparisons=comparisons,
        )
        analysis_path = derived / "cm_analysis_v1.json"
        analysis_path.write_bytes(canonical_json_bytes(analysis))
        records = [_record(output_dir, analysis_path, "analysis")]
        state_analysis = derive_state_landscape_analysis_for_request(
            request, ensemble,
            [landscapes[key] for key in sorted(landscapes)], structure_maps,
        )
        if state_analysis is not None:
            state_path = derived / "cm_state_landscape_analysis_v1.json"
            state_path.write_bytes(canonical_json_bytes(state_analysis))
            records.append(_record(output_dir, state_path, "state_landscape_analysis"))
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
        snapshots_path = request_path.parent / "cm_complex_snapshots_v1.json"
        snapshots = json.loads(snapshots_path.read_text(encoding="utf-8")) if snapshots_path.is_file() else []
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
            records.append(_record(output_dir, path, role))
        if resampling_manifest is not None:
            path = derived / "cm_resampling_terminal_manifest_v1.json"
            path.write_bytes(canonical_json_bytes(resampling_manifest))
            records.append(_record(output_dir, path, "resampling"))
        result_references = {
            "schema_name": "cm_frustrampnn_result_references",
            "schema_version": 1,
            "parent_job_id": preparation["parent_job_id"],
            "parent_workflow_id": "conformational_mapping",
            "expected_cardinality": expected,
            "results": references,
        }
        references_path = derived / "cm_frustrampnn_result_references_v1.json"
        references_path.write_bytes(canonical_json_bytes(result_references))
        records.append(_record(output_dir, references_path, "frustrampnn_result_references"))
        index_without_hash: dict[str, Any] = {
            "schema_name": "cm_derived_index", "schema_version": 1,
            "request_id": request["request_id"],
            "source_ensemble_sha256": canonical_sha256(ensemble),
            "records": records,
            "frustrampnn_result_references": result_references,
            "analysis": analysis,
            "lineage": lineage,
            "support": support,
            "missingness": missingness,
            "resampling": resampling_manifest,
        }
        if state_analysis is not None:
            index_without_hash["state_landscape_analyses"] = [state_analysis]
        index = {
            **index_without_hash,
            "index_sha256": canonical_sha256(index_without_hash),
        }
        (output_dir / "cm_derived_index_v1.json").write_bytes(canonical_json_bytes(index))
        return index
    except BaseException:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--preparation-manifest", type=Path, required=True)
    parser.add_argument("--scheduler-terminal-receipt", type=Path)
    parser.add_argument("--bundle", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        postprocess_canonical_bundles(
            request_path=args.request,
            canonical_dir=args.canonical,
            preparation_manifest_path=args.preparation_manifest,
            scheduler_terminal_receipt_path=args.scheduler_terminal_receipt,
            bundle_dirs=args.bundle,
            output_dir=args.out,
        )
    except Exception as exc:
        print(f"cm_frustrampnn_v2_postprocess_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
