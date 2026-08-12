from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from services.frustrampnn.analysis import finalize_landscape_v2
from services.frustrampnn.configuration import execution_configuration
from services.frustrampnn.contracts import (
    ContractValidationError,
    canonical_sha256,
    validate_schema,
)
from services.frustrampnn.settings import (
    FrustraMPNNRequestedSettings,
    FrustraMPNNResolutionIdentity,
    FrustraMPNNResolvedChainSelection,
    _build_effective_settings,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_ROOT = REPO_ROOT / "schemas/frustrampnn"
AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"
V2_PATHS = [
    "workflow_component_request_v2.json",
    "normalized_input.pdb",
    "frustrampnn_structure_map_v1.json",
    "raw_frustrampnn.csv",
    "frustrampnn_landscape_v2.json",
    "frustrampnn_summary_v2.json",
    "frustrampnn_stdout.log",
    "frustrampnn_stderr.log",
    "frustrampnn_execution_receipt_v2.json",
    "frustrampnn_statistics_v1.json",
]


def _assert_closed_objects(node: object, path: str = "$") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, path
        for key, value in node.items():
            _assert_closed_objects(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_closed_objects(value, f"{path}/{index}")


def _effective():
    requested = FrustraMPNNRequestedSettings.model_validate({
        "protein_selection": {
            "mode": "selected_residues",
            "entities": [],
            "residues": [{
                "entity_instance_id": "entity-A",
                "source_entity_id": "1",
                "label_asym_id": "AA",
                "auth_asym_id": "X",
                "auth_seq_id": 10,
                "insertion_code": "A",
                "sequence_index": 1,
            }],
        },
        "classification_policy": {
            "mode": "custom",
            "high_max": -0.5,
            "minimal_min": 0.5,
        },
    })
    chain = FrustraMPNNResolvedChainSelection.model_validate({
        "entity": {
            "entity_instance_id": "entity-A",
            "source_entity_id": "1",
            "label_asym_id": "AA",
            "auth_asym_id": "X",
        },
        "pdb_chain_id": "A",
        "residues": [{
            "entity_instance_id": "entity-A",
            "source_entity_id": "1",
            "label_asym_id": "AA",
            "auth_asym_id": "X",
            "auth_seq_id": 10,
            "insertion_code": "A",
            "sequence_index": 1,
            "wt": "G",
            "pdb_chain_id": "A",
            "model_position": 0,
        }],
    })
    return _build_effective_settings(
        requested,
        resolved_chains=(chain,),
        resolution_identity=FrustraMPNNResolutionIdentity(
            source_artifact_sha256="1" * 64,
            structure_map_sha256="2" * 64,
            normalized_pdb_sha256="3" * 64,
        ),
    )


def _raw() -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(
        handle,
        fieldnames=["frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"],
        lineterminator="\n",
    )
    writer.writeheader()
    for index, mutation in enumerate(AA_ORDER):
        writer.writerow({
            "frustration_pred": -0.5 if index == 0 else 0.5 if index == 1 else 0.0,
            "position": 0,
            "wildtype": "G",
            "mutation": mutation,
            "chain": "A",
            "pdb": "normalized.pdb",
        })
    return handle.getvalue().encode("utf-8")


def _artifacts():
    effective = _effective()
    configuration = execution_configuration(effective)
    merged, landscape = finalize_landscape_v2(
        (_raw(),),
        effective,
        execution_configuration=configuration,
        target_id="target-1",
        parent_job_id="job-1",
        candidate_id="candidate-1",
        source_artifact_sha256=effective.resolution_identity.source_artifact_sha256,
    )
    bindings = {
        key: landscape[key]
        for key in (
            "execution_configuration_id",
            "execution_configuration_sha256",
            "requested_settings_sha256",
            "effective_settings_sha256",
            "runtime_identity_sha256",
            "target_id",
            "parent_job_id",
            "candidate_id",
            "source_artifact_sha256",
            "structure_map_sha256",
            "normalized_pdb_sha256",
            "threshold_policy_id",
            "threshold_policy",
            "threshold_policy_sha256",
        )
    }
    classes = [slot["class"] for slot in landscape["residues"][0]["slots"]]
    counts = {name: classes.count(name) for name in ("high", "neutral", "minimal")}
    summary = {
        "schema_name": "frustrampnn_summary",
        "schema_version": 2,
        **bindings,
        "landscape_sha256": canonical_sha256(landscape),
        "residue_support": {
            "expected": 1, "mapped": 1, "scoreable": 1, "excluded": 0, "ambiguous": 0,
        },
        "slot_support": {"expected": 20, "observed": 20, "scoreable": 20},
        "missingness_by_reason": {},
        "native_slot_counts": {name: int(landscape["residues"][0]["slots"][5]["class"] == name) for name in ("high", "neutral", "minimal")},
        "native_slot_fractions": {name: float(landscape["residues"][0]["slots"][5]["class"] == name) for name in ("high", "neutral", "minimal")},
        "complete_landscape_counts": counts,
        "complete_landscape_fractions": {name: counts[name] / 20 for name in counts},
        "support_by_entity_chain": [{
            "entity_instance_id": "entity-A", "auth_asym_id": "X",
            "expected_residues": 1, "mapped_residues": 1, "scoreable_residues": 1,
            "expected_slots": 20, "observed_slots": 20, "scoreable_slots": 20,
        }],
    }
    entry = {
        "ordinal": 0,
        "chains": ["A"],
        "positions": [0],
        "shard_relative_path": "raw_frustrampnn_shard_0000.csv",
    }
    plan_entries = [entry]
    plan = {
        "entries": plan_entries,
        "plan_sha256": canonical_sha256({"entries": plan_entries}),
    }
    argv = [
        "apptainer", "exec", "--containall", "image.sif", "frustrampnn", "predict",
        "--pdb", "normalized.pdb", "--checkpoint", "megascale.ckpt",
        "--output", entry["shard_relative_path"], "--device", "cuda",
        "--chains", "A", "--positions", "0",
    ]
    receipt = {
        "schema_name": "frustrampnn_execution_receipt",
        "schema_version": 2,
        "invocation_id": "invoke-1",
        "execution_configuration_sha256": configuration.configuration_sha256,
        "requested_settings_sha256": effective.settings_sha256,
        "effective_settings_sha256": effective.effective_settings_sha256,
        "runtime_identity_sha256": configuration.runtime_identity_sha256,
        "source_artifact_sha256": "1" * 64,
        "structure_map_sha256": "2" * 64,
        "normalized_pdb_sha256": "3" * 64,
        "command_plan": plan,
        "command_count": 1,
        "commands": [{
            **entry,
            "argv": argv,
            "argv_sha256": canonical_sha256(argv),
            "status": "succeeded",
            "exit_code": 0,
            "shard_sha256": hashlib.sha256(_raw()).hexdigest(),
            "shard_row_count": 20,
            "started_at": "2026-08-08T20:00:00Z",
            "ended_at": "2026-08-08T20:00:01Z",
            "duration_seconds": 1.0,
        }],
        "merged_raw_csv_sha256": hashlib.sha256(merged).hexdigest(),
        "landscape_sha256": canonical_sha256(landscape),
        "summary_sha256": canonical_sha256(summary),
        "assigned_physical_gpu_id": "3",
        "task_visible_device_index": 0,
        "stdout_artifact": "frustrampnn_stdout.log",
        "stderr_artifact": "frustrampnn_stderr.log",
        "started_at": "2026-08-08T20:00:00Z",
        "ended_at": "2026-08-08T20:00:01Z",
        "duration_seconds": 1.0,
    }
    artifact_specs = {
        "workflow_component_request_v2.json": ("workflow_component_request", 2, None),
        "normalized_input.pdb": (None, None, {"kind": "residues", "count": 1}),
        "frustrampnn_structure_map_v1.json": ("frustrampnn_structure_map", 1, {"kind": "residues", "count": 1}),
        "raw_frustrampnn.csv": (None, None, {"kind": "rows", "count": 20}),
        "frustrampnn_landscape_v2.json": ("frustrampnn_landscape", 2, {"kind": "residues", "count": 1}),
        "frustrampnn_summary_v2.json": ("frustrampnn_summary", 2, {"kind": "records", "count": 1}),
        "frustrampnn_stdout.log": (None, None, None),
        "frustrampnn_stderr.log": (None, None, None),
        "frustrampnn_execution_receipt_v2.json": ("frustrampnn_execution_receipt", 2, {"kind": "records", "count": 1}),
        "frustrampnn_statistics_v1.json": ("frustrampnn_statistics", 1, {"kind": "records", "count": 1}),
    }
    manifest = {
        "schema_name": "frustrampnn_result_manifest",
        "schema_version": 2,
        "invocation_id": "invoke-1",
        "parent_job_id": "job-1",
        "candidate_id": "candidate-1",
        "request_sha256": "4" * 64,
        "source_artifact_sha256": "1" * 64,
        "execution_configuration_sha256": configuration.configuration_sha256,
        "statistics_sha256": "5" * 64,
        "comparison_compatibility_id": "6" * 64,
        "artifact_count": len(V2_PATHS),
        "artifacts": [{
            "relative_path": path,
            "schema_name": artifact_specs[path][0],
            "schema_version": artifact_specs[path][1],
            "sha256": hashlib.sha256(path.encode("utf-8")).hexdigest(),
            "bytes": 0 if path.endswith(".log") else 1,
            "cardinality": artifact_specs[path][2],
        } for path in V2_PATHS],
    }
    result = {
        "schema_name": "workflow_component_result",
        "schema_version": 2,
        "component_id": "frustrampnn",
        "component_contract_version": "2.0",
        "request_sha256": manifest["request_sha256"],
        "invocation_id": "invoke-1",
        "parent_job_id": "job-1",
        "parent_workflow_id": "structure_prediction",
        "candidate_id": "candidate-1",
        "status": "succeeded",
        "failure_class": None,
        "diagnostic": None,
        "result_manifest": {
            "relative_path": "frustrampnn_result_manifest_v2.json",
            "sha256": canonical_sha256(manifest),
        },
        "result_payload": {
            "relative_path": "frustrampnn_summary_v2.json",
            "schema_name": "frustrampnn_summary",
            "schema_version": 2,
            "sha256": canonical_sha256(summary),
        },
    }
    return receipt, landscape, summary, manifest, result


def test_phase3_output_schemas_are_separate_registered_closed_draft_2020_12() -> None:
    expected = {
        "frustrampnn_execution_receipt_v2.schema.json": "frustrampnn_execution_receipt_v2",
        "workflow_component_result_v2.schema.json": "workflow_component_result_v2",
        "frustrampnn_result_manifest_v2.schema.json": "frustrampnn_result_manifest_v2",
        "frustrampnn_landscape_v2.schema.json": "frustrampnn_landscape_v2",
        "frustrampnn_summary_v2.schema.json": "frustrampnn_summary_v2",
    }
    for filename, title in expected.items():
        schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{filename}")
        assert schema["title"] == title
        _assert_closed_objects(schema)


def test_v2_output_contracts_accept_exact_selected_custom_policy_bundle() -> None:
    receipt, landscape, summary, manifest, result = _artifacts()
    for key, artifact in (
        ("frustrampnn_execution_receipt_v2", receipt),
        ("frustrampnn_landscape_v2", landscape),
        ("frustrampnn_summary_v2", summary),
        ("frustrampnn_result_manifest_v2", manifest),
        ("workflow_component_result_v2", result),
    ):
        validate_schema(key, artifact)


@pytest.mark.parametrize(
    ("artifact_index", "mutation", "message"),
    [
        (0, lambda value: value["command_plan"].update({"plan_sha256": "0" * 64}), "plan"),
        (0, lambda value: value["commands"][0].update({"argv_sha256": "0" * 64}), "argv"),
        (0, lambda value: value["commands"][0].update({"positions": [1]}), "selection|command"),
        (0, lambda value: value["commands"][0].update({"duration_seconds": 2.0}), "timing|duration"),
        (1, lambda value: value["residues"][0]["slots"][0].update({"class": "neutral"}), "class"),
        (2, lambda value: value["slot_support"].update({"scoreable": 19}), "support|slot"),
        (3, lambda value: value["artifacts"][0].update({"relative_path": "workflow_component_request_v1.json"}), "artifact|path|canonical"),
        (4, lambda value: value["result_manifest"].update({"sha256": None}), "manifest|None|string"),
    ],
)
def test_v2_output_contracts_reject_hash_selection_policy_and_generation_tampering(
    artifact_index: int,
    mutation,
    message: str,
) -> None:
    artifacts = list(_artifacts())
    artifact = copy.deepcopy(artifacts[artifact_index])
    mutation(artifact)
    keys = (
        "frustrampnn_execution_receipt_v2",
        "frustrampnn_landscape_v2",
        "frustrampnn_summary_v2",
        "frustrampnn_result_manifest_v2",
        "workflow_component_result_v2",
    )
    with pytest.raises(ContractValidationError, match=message):
        validate_schema(keys[artifact_index], artifact)
