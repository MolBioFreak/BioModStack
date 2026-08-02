from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_SCHEMAS = {
    "workflow_component_request_v1.schema.json",
    "workflow_component_result_v1.schema.json",
    "frustrampnn_structure_map_v1.schema.json",
    "frustrampnn_landscape_v1.schema.json",
    "frustrampnn_summary_v1.schema.json",
    "frustrampnn_execution_receipt_v1.schema.json",
    "frustrampnn_result_manifest_v1.schema.json",
    "frustrampnn_comparison_v1.schema.json",
    "frustrampnn_guidance_v1.schema.json",
    "frustrampnn_multistate_comparison_v1.schema.json",
}


def _contracts():
    path = REPO_ROOT / "platform/api/services/frustrampnn/contracts.py"
    assert path.is_file(), "neutral FrustraMPNN contracts module is missing"
    return importlib.import_module("services.frustrampnn.contracts")


def _request() -> dict:
    return {
        "schema_name": "workflow_component_request",
        "schema_version": 1,
        "component_id": "frustrampnn",
        "component_contract_version": "1.0",
        "invocation_id": "invoke-1",
        "parent_job_id": "job-1",
        "parent_workflow_id": "structure_prediction",
        "candidate_id": "candidate-1",
        "source_artifact": {
            "relative_path": "inputs/candidate-1.cif",
            "sha256": "1" * 64,
            "media_type": "chemical/x-mmcif",
            "producer_stage": "structure_prediction",
            "artifact_id": "artifact-1",
        },
        "requiredness": "required",
        "identity_authority": "mmcif_atom_site",
        "protein_selection": {
            "mode": "explicit",
            "entities": [{
                "entity_instance_id": "protein-1",
                "source_entity_id": "1",
                "label_asym_id": "AA",
                "auth_asym_id": "X",
                "sequence": "GA",
            }],
        },
        "parameters": {
            "checkpoint_id": "megascale.ckpt",
            "threshold_policy_id": "frustrampnn_class_v1",
            "selected_model_number": 1,
            "altloc_policy": "blank_or_explicit:A",
        },
        "requested_outputs": [
            "structure_map", "raw_csv", "landscape", "summary", "execution_receipt"
        ],
    }


def _result() -> dict:
    return {
        "schema_name": "workflow_component_result",
        "schema_version": 1,
        "request_sha256": "2" * 64,
        "invocation_id": "invoke-1",
        "component_id": "frustrampnn",
        "component_contract_version": "1.0",
        "candidate_id": "candidate-1",
        "parent_job_id": "job-1",
        "parent_workflow_id": "structure_prediction",
        "status": "succeeded",
        "failure_class": None,
        "diagnostic": None,
        "source_artifact": _request()["source_artifact"],
        "runtime_identity": {
            "sif_sha256": "3" * 64,
            "executable_sha256": "4" * 64,
            "checkpoint_id": "megascale.ckpt",
            "checkpoint_sha256": "5" * 64,
        },
        "artifacts": [
            {
                "relative_path": path,
                "schema_name": schema_name,
                "schema_version": 1 if schema_name is not None else None,
                "sha256": str(index) * 64,
                "bytes": 10,
                "cardinality": cardinality,
            }
            for index, (path, schema_name, cardinality) in enumerate(
                (
                    ("normalized_input.pdb", None, {"kind": "residues", "count": 2}),
                    ("frustrampnn_structure_map_v1.json", "frustrampnn_structure_map", {"kind": "residues", "count": 2}),
                    ("raw_frustrampnn.csv", None, {"kind": "rows", "count": 40}),
                    ("frustrampnn_landscape_v1.json", "frustrampnn_landscape", {"kind": "residues", "count": 2}),
                    ("frustrampnn_summary_v1.json", "frustrampnn_summary", {"kind": "records", "count": 1}),
                    ("frustrampnn_stdout.log", None, None),
                    ("frustrampnn_stderr.log", None, None),
                    ("frustrampnn_execution_receipt_v1.json", "frustrampnn_execution_receipt", {"kind": "records", "count": 1}),
                ),
                start=1,
            )
        ],
        "result_payload": {"schema_name": "frustrampnn_summary", "schema_version": 1},
        "started_at": "2026-07-30T12:00:00Z",
        "ended_at": "2026-07-30T12:00:01Z",
        "duration_seconds": 1.0,
        "assigned_gpu": {"physical_device_id": "GPU-abc", "task_visible_device_index": 0},
    }


def test_all_frustrampnn_schemas_are_draft_2020_12_fail_closed() -> None:
    directory = REPO_ROOT / "schemas/frustrampnn"
    assert directory.is_dir(), "schemas/frustrampnn is missing"
    paths = {path.name for path in directory.glob("*.schema.json")}
    assert paths == EXPECTED_SCHEMAS
    for name in sorted(paths):
        schema = json.loads((directory / name).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_canonical_json_is_deterministic_and_rejects_duplicate_and_nonfinite_values() -> None:
    contracts = _contracts()
    value = {"z": "é", "a": [1, 2.5, True, None]}
    assert contracts.canonical_json_bytes(value) == b'{"a":[1,2.5,true,null],"z":"\xc3\xa9"}'
    assert contracts.canonical_sha256({"a": 1, "b": 2}) == contracts.canonical_sha256({"b": 2, "a": 1})
    with pytest.raises(contracts.ContractValidationError, match="duplicate"):
        contracts.canonical_json_loads('{"a":1,"a":2}')
    with pytest.raises(contracts.ContractValidationError, match="non-finite"):
        contracts.canonical_json_loads('{"a":NaN}')
    with pytest.raises(contracts.ContractValidationError, match="non-finite"):
        contracts.canonical_json_bytes({"a": float("inf")})


def test_request_and_result_envelopes_validate_exact_fields_and_status_semantics() -> None:
    contracts = _contracts()
    request = _request()
    contracts.validate_schema("workflow_component_request_v1", request)
    result = _result()
    contracts.validate_schema("workflow_component_result_v1", result)

    unknown = copy.deepcopy(request)
    unknown["candidate_rank"] = 1
    with pytest.raises(contracts.ContractValidationError):
        contracts.validate_schema("workflow_component_request_v1", unknown)

    escaped = copy.deepcopy(request)
    escaped["source_artifact"]["relative_path"] = "../candidate.cif"
    with pytest.raises(contracts.ContractValidationError, match="relative path"):
        contracts.validate_schema("workflow_component_request_v1", escaped)

    false_success = copy.deepcopy(result)
    false_success["failure_class"] = "raw_output_invalid"
    false_success["diagnostic"] = "should not coexist with success"
    with pytest.raises(contracts.ContractValidationError, match="succeeded"):
        contracts.validate_schema("workflow_component_result_v1", false_success)

    incomplete_success = copy.deepcopy(result)
    incomplete_success["artifacts"].pop()
    with pytest.raises(contracts.ContractValidationError):
        contracts.validate_schema("workflow_component_result_v1", incomplete_success)


def test_request_hash_binds_every_request_field_without_self_reference() -> None:
    contracts = _contracts()
    request = _request()
    original = contracts.request_sha256(request)
    changed = copy.deepcopy(request)
    changed["candidate_id"] = "candidate-2"
    assert contracts.request_sha256(changed) != original


def test_request_requires_the_exact_canonical_output_set_in_canonical_order() -> None:
    contracts = _contracts()
    request = _request()
    for outputs in (
        ["summary"],
        request["requested_outputs"][:-1],
        list(reversed(request["requested_outputs"])),
    ):
        hostile = copy.deepcopy(request)
        hostile["requested_outputs"] = outputs
        with pytest.raises(contracts.ContractValidationError, match="requested output|canonical"):
            contracts.validate_schema("workflow_component_request_v1", hostile)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda result: result["artifacts"][0].update(bytes=0), "positive|bytes"),
        (lambda result: result["artifacts"][1].update(schema_name=None, schema_version=None), "schema"),
        (lambda result: result["artifacts"][2].update(cardinality={"kind": "rows", "count": 0}), "cardinality|positive"),
        (lambda result: result["artifacts"][3].update(cardinality={"kind": "slots", "count": 40}), "cardinality"),
        (lambda result: result["artifacts"][0].update(cardinality=None), "cardinality"),
        (lambda result: result["artifacts"][4].update(cardinality=None), "cardinality"),
        (lambda result: result["result_payload"].update(schema_name="anything", schema_version=99), "payload|summary"),
        (lambda result: result["assigned_gpu"].update(physical_device_id=None), "GPU|gpu|physical"),
        (lambda result: result["assigned_gpu"].update(task_visible_device_index=None), "GPU|gpu|visible"),
    ],
)
def test_success_result_requires_exact_nonempty_scientific_inventory_runtime_and_summary_payload(
    mutation, expected: str
) -> None:
    contracts = _contracts()
    result = _result()
    mutation(result)
    with pytest.raises(contracts.ContractValidationError, match=expected):
        contracts.validate_schema("workflow_component_result_v1", result)


def test_failure_class_taxonomy_is_exact_and_success_inventory_does_not_apply_to_failures() -> None:
    contracts = _contracts()
    result = _result()
    result.update(status="failed", failure_class="raw_output_invalid", diagnostic="invalid row")
    result["artifacts"] = []
    result["result_payload"] = {"schema_name": "frustrampnn_summary", "schema_version": 1}
    result["assigned_gpu"] = {"physical_device_id": None, "task_visible_device_index": None}
    contracts.validate_schema("workflow_component_result_v1", result)

    hostile = copy.deepcopy(result)
    hostile["failure_class"] = "made_up_failure"
    with pytest.raises(contracts.ContractValidationError, match="failure_class|enum"):
        contracts.validate_schema("workflow_component_result_v1", hostile)


def test_structure_landscape_and_summary_semantics_reject_status_class_hash_fraction_and_support_tamper() -> None:
    contracts = _contracts()
    from test_frustrampnn_analysis import _map, _raw
    from services.frustrampnn.analysis import finalize_landscape, summarize_landscape
    import tempfile

    structure_map = _map()
    with tempfile.TemporaryDirectory() as directory:
        raw = Path(directory) / "raw.csv"
        raw.write_text(_raw(), encoding="utf-8")
        landscape = finalize_landscape(
            raw,
            structure_map,
            expected_normalized_pdb_sha256=structure_map["normalized_pdb_sha256"],
            expected_model_ready_sequence_sha256=structure_map["model_ready_sequence_sha256"],
        )
    summary = summarize_landscape(landscape, structure_map)

    wrong_class = copy.deepcopy(landscape)
    wrong_class["residues"][0]["slots"][2]["class"] = "high"
    with pytest.raises(contracts.ContractValidationError, match="class"):
        contracts.validate_schema("frustrampnn_landscape_v1", wrong_class)

    wrong_policy_hash = copy.deepcopy(landscape)
    wrong_policy_hash["threshold_policy_sha256"] = "f" * 64
    with pytest.raises(contracts.ContractValidationError, match="policy.*hash"):
        contracts.validate_schema("frustrampnn_landscape_v1", wrong_policy_hash)

    missing_slot = copy.deepcopy(landscape)
    missing_slot["residues"][0]["slots"][0].update({
        "score": None, "class": None, "scoreable": False,
        "status": "missing", "reason": "missing raw row",
    })
    with pytest.raises(contracts.ContractValidationError, match="successful landscape|unscoreable"):
        contracts.validate_schema("frustrampnn_landscape_v1", missing_slot)

    for mutate in (
        # The fixture has one high and one neutral native slot, so 0.5 is its
        # actual fraction; use a genuine tamper rather than a no-op.
        lambda value: value["native_slot_fractions"].update(neutral=0.4),
        lambda value: value["complete_landscape_counts"].update(neutral=39),
        lambda value: value["slot_support"].update(observed=39),
        lambda value: value["support_by_entity_chain"][0].update(scoreable_slots=39),
        lambda value: value.update(threshold_policy_sha256="e" * 64),
    ):
        hostile = copy.deepcopy(summary)
        mutate(hostile)
        with pytest.raises(contracts.ContractValidationError):
            contracts.validate_schema("frustrampnn_summary_v1", hostile)
