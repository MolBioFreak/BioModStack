from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from routers.conformational_mapping import SubmitRequest
from services.conformational_mapping.contracts import canonical_json_bytes
from services.conformational_mapping.request_builder import (
    ConformationalMappingRequestError,
    materialize_trusted_internal_request,
)
from services.frustrampnn.settings import default_settings


def _candidate_pdb() -> bytes:
    lines: list[str] = []
    for serial, (atom, element) in enumerate(
        (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1,
    ):
        lines.append(
            f"ATOM  {serial:5d} {f' {atom:<3}'} GLY X  10    "
            f"{serial:8.3f}{serial + 1:8.3f}{serial + 2:8.3f}"
            f"{1.0:6.2f}{20.0:6.2f}          {element:>2}  \n"
        )
    return "".join(lines).encode("ascii") + b"END\n"


def _candidate_mmcif() -> bytes:
    columns = (
        "group_PDB", "id", "type_symbol", "label_atom_id", "label_alt_id",
        "label_comp_id", "label_asym_id", "label_entity_id", "label_seq_id",
        "pdbx_PDB_ins_code", "Cartn_x", "Cartn_y", "Cartn_z", "occupancy",
        "B_iso_or_equiv", "auth_seq_id", "auth_comp_id", "auth_asym_id",
        "auth_atom_id", "pdbx_PDB_model_num",
    )
    rows: list[str] = []
    for serial, (atom, element) in enumerate(
        (("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), 1,
    ):
        rows.append(" ".join((
            "ATOM", str(serial), element, atom, ".", "GLY", "X",
            "generated-protein", "1", "?", str(serial), str(serial + 1),
            str(serial + 2), "1.0", "20.0", "10", "GLY", "X", atom, "1",
        )))
    return (
        "data_candidate\n#\nloop_\n"
        + "".join(f"_atom_site.{column}\n" for column in columns)
        + "\n".join(rows)
        + "\n#\n"
    ).encode("ascii")


def _candidate_snapshot(source_sha256: str) -> dict[str, object]:
    from services.conformational_mapping.contracts import canonical_sha256

    snapshot: dict[str, object] = {
        "schema_name": "cm_complex_snapshot",
        "schema_version": 1,
        "target_id": "target-a",
        "target_order": 0,
        "original_source_path": "inputs/source.pdb",
        "original_source_sha256": source_sha256,
        "normalized_source_sha256": "0" * 64,
        "entities": [{
            "entity_type": "protein",
            "source_entity_id": "protein",
            "count": 1,
            "ordered_instance_ids": ["protein-1"],
            "sequence": "G",
        }],
        "bonds": [],
        "instance_mappings": [{
            "source_entity_id": "protein",
            "source_instance_id": "protein-1",
            "runtime_target_id": "target-a",
            "runtime_entity_id": "runtime-protein",
            "runtime_instance_id": "runtime-protein-1",
            "runtime_order": 0,
            "candidate_id": "candidate-a",
            "output_entity_id": "protein",
            "output_label_asym_id": "X",
            "output_auth_asym_id": "X",
            "output_entity_order": 0,
        }],
        "admission": {
            "token_count": 1,
            "atom_count": 4,
            "token_limit": 100,
            "conversion_omissions": [],
        },
        "unsupported_fields": [],
    }
    snapshot["normalized_source_sha256"] = canonical_sha256({
        key: value
        for key, value in snapshot.items()
        if key != "normalized_source_sha256"
    })
    return snapshot


def _load_cm_preparer():
    path = Path(__file__).resolve().parents[3] / "scripts" / "prepare_conformational_mapping_frustrampnn_v2.py"
    spec = importlib.util.spec_from_file_location("cm_frustrampnn_preparer_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_cm_postprocessor():
    path = Path(__file__).resolve().parents[3] / "scripts" / "postprocess_conformational_mapping_frustrampnn_v2.py"
    spec = importlib.util.spec_from_file_location("cm_frustrampnn_postprocessor_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_component_fixture():
    path = Path(__file__).with_name("test_frustrampnn_component_phase3.py")
    spec = importlib.util.spec_from_file_location("cm_frustrampnn_component_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _external_import_params() -> dict[str, object]:
    return {
        "backend": "external_import",
        "targets": [{"target_id": "target-a", "target_order": 0}],
        "ordered_seeds": [0],
        "samples_per_seed": 1,
        "feature_policy": {
            "mode": "regenerate_mutated_protein_v1",
            "protein_msa_enabled": True,
            "templates_enabled": True,
            "rna_msa_enabled": True,
        },
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {
            "sign_zero_epsilon": 0.000001,
            "clash_detector_id": "bms_clash",
            "clash_detector_version": "1",
            "outer_support_minimum": 0.8,
            "inner_support_minimum": 0.6,
            "sign_consistency_minimum": 0.8,
            "clash_free_minimum": 0.9,
            "rank_stability_minimum": 0.6,
            "minimum_common_ranked_universe_size": 3,
        },
        "import_receipt_id": "9" * 64,
        "resolved_import_entries": [
            {"staged_index": 0, "source_content_sha256": "8" * 64}
        ],
    }


def test_cm_materialization_binds_canonical_default_settings_and_requiredness(
    tmp_path: Path,
) -> None:
    materialized = materialize_trusted_internal_request(
        _external_import_params(),
        output_dir=tmp_path,
        request_id="00000000-0000-4000-8000-000000000901",
    )
    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))

    assert request["frustrampnn_settings"] == default_settings().model_dump(
        mode="json", exclude_none=False
    )
    assert request["frustrampnn_settings"]["settings_value_origin"] == "bms_default"
    assert request["frustrampnn_requiredness"] == "required"


def _submit_payload() -> dict[str, object]:
    return {
        "name": "CM canonical settings",
        "backend": "external_import",
        "registered_artifact_ids": ["artifact-1"],
        "registered_snapshot_id": "snapshot-1",
        "ordered_seeds": [0],
        "samples_per_seed": 1,
        "feature_policy": {
            "mode": "regenerate_mutated_protein_v1",
            "protein_msa_enabled": True,
            "templates_enabled": True,
            "rna_msa_enabled": True,
        },
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {
            "sign_zero_epsilon": 0.000001,
            "clash_detector_id": "bms_clash",
            "clash_detector_version": "1",
            "outer_support_minimum": 0.8,
            "inner_support_minimum": 0.6,
            "sign_consistency_minimum": 0.8,
            "clash_free_minimum": 0.9,
            "rank_stability_minimum": 0.6,
            "minimum_common_ranked_universe_size": 3,
        },
    }


def test_cm_submission_omission_binds_complete_canonical_defaults() -> None:
    body = SubmitRequest.model_validate(_submit_payload())

    assert body.frustrampnn_settings == default_settings()
    assert body.frustrampnn_settings.settings_value_origin == "bms_default"
    assert "frustrampnn_settings" not in body.model_fields_set


def test_cm_submission_accepts_complete_settings_but_rejects_caller_owned_origin() -> None:
    complete = default_settings().model_dump(mode="json", exclude_none=False)
    complete.pop("settings_value_origin")
    complete["classification_policy"] = {
        "mode": "custom",
        "high_max": -0.75,
        "minimal_min": 0.7,
    }
    body = SubmitRequest.model_validate(
        {**_submit_payload(), "frustrampnn_settings": complete}
    )
    assert body.frustrampnn_settings.settings_value_origin == "operator_request"
    assert body.frustrampnn_settings.classification_policy.high_max == -0.75
    assert "frustrampnn_settings" in body.model_fields_set

    forged = dict(complete)
    forged["settings_value_origin"] = "bms_default"
    with pytest.raises(ValidationError, match="origin"):
        SubmitRequest.model_validate(
            {**_submit_payload(), "frustrampnn_settings": forged}
        )


def test_cm_materialization_preserves_complete_server_bound_settings(
    tmp_path: Path,
) -> None:
    supplied = default_settings().model_dump(mode="json", exclude_none=False)
    supplied["settings_value_origin"] = "operator_request"
    supplied["classification_policy"] = {
        "mode": "custom",
        "high_max": -0.75,
        "minimal_min": 0.7,
    }
    params = _external_import_params()
    params["frustrampnn_settings"] = supplied

    materialized = materialize_trusted_internal_request(
        params,
        output_dir=tmp_path,
        request_id="00000000-0000-4000-8000-000000000902",
    )
    request = json.loads(materialized.request_path.read_text(encoding="utf-8"))

    assert request["frustrampnn_settings"] == supplied
    assert request["frustrampnn_requiredness"] == "required"


def test_cm_materialization_rejects_incomplete_server_bound_settings(
    tmp_path: Path,
) -> None:
    params = _external_import_params()
    params["frustrampnn_settings"] = {
        "schema_name": "frustrampnn_settings",
        "schema_version": 1,
        "settings_value_origin": "operator_request",
    }
    with pytest.raises(ConformationalMappingRequestError, match="complete"):
        materialize_trusted_internal_request(
            params,
            output_dir=tmp_path,
            request_id="00000000-0000-4000-8000-000000000903",
        )


def test_cm_materialization_rejects_explicit_null_settings(tmp_path: Path) -> None:
    params = _external_import_params()
    params["frustrampnn_settings"] = None

    with pytest.raises(ConformationalMappingRequestError, match="complete"):
        materialize_trusted_internal_request(
            params,
            output_dir=tmp_path,
            request_id="00000000-0000-4000-8000-000000000905",
        )


@pytest.mark.parametrize("caller_requiredness", ["required", "optional", "unknown"])
def test_cm_materialization_rejects_caller_owned_requiredness(
    tmp_path: Path,
    caller_requiredness: str,
) -> None:
    params = _external_import_params()
    params["frustrampnn_requiredness"] = caller_requiredness
    with pytest.raises(ConformationalMappingRequestError, match="unknown request fields"):
        materialize_trusted_internal_request(
            params,
            output_dir=tmp_path,
            request_id="00000000-0000-4000-8000-000000000904",
        )


def test_cm_candidate_v2_preparation_binds_snapshot_source_settings_and_invocation(
    tmp_path: Path,
) -> None:
    from services.conformational_mapping.contracts import canonical_sha256
    from services.conformational_mapping.frustrampnn_adapter import (
        bind_cm_candidate_snapshot_bytes,
        prepare_cm_candidate_v2,
    )
    from services.frustrampnn.contracts import validate_schema
    from services.frustrampnn.settings import requested_settings_sha256

    source_bytes = _candidate_pdb()
    source = tmp_path / "candidate.pdb"
    source.write_bytes(source_bytes)
    snapshot = _candidate_snapshot(hashlib.sha256(source_bytes).hexdigest())
    settings = default_settings()
    candidate = {
        "candidate_id": "candidate-a",
        "authoritative_structure_path": "native/candidate.pdb",
        "backend_coordinates": {
            "backend": "protenix_v2_ensemble",
            "target_id": "target-a",
            "ordered_seed": 7,
            "sample_index": 2,
        },
    }
    request_path = tmp_path / "workflow_component_request_v2.json"
    normalized_path = tmp_path / "canonical_source.pdb"
    structure_map_path = tmp_path / "frustrampnn_structure_map_v1.json"

    request = prepare_cm_candidate_v2(
        source=source,
        output_pdb_path=normalized_path,
        structure_map_path=structure_map_path,
        request_path=request_path,
        authority_artifact_path=tmp_path / "authority_artifact_v1.json",
        parent_job_id="cm-parent-job",
        parent_workflow_id="conformational_mapping",
        candidate=candidate,
        complex_snapshot=snapshot,
        requested_settings=settings,
    )

    validate_schema("workflow_component_request_v2", request)
    assert request_path.is_file()
    assert normalized_path.is_file()
    assert structure_map_path.is_file()
    assert request["parent_job_id"] == "cm-parent-job"
    assert request["parent_workflow_id"] == "conformational_mapping"
    assert request["candidate_id"] == "candidate-a"
    assert request["invocation_id"] == "frustrampnn:cm-parent-job:candidate-a"
    assert request["source_artifact"]["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert request["identity_authority"] == "cm_complex_snapshot"
    bound_snapshot = bind_cm_candidate_snapshot_bytes(
        snapshot,
        candidate_id="candidate-a",
        source_bytes=source_bytes,
        source_suffix=".pdb",
        source_relative_path="native/candidate.pdb",
    )
    assert request["identity_authority_artifact"][
        "cm_complex_snapshot_sha256"
    ] == canonical_sha256(bound_snapshot)
    assert request["requested_settings_sha256"] == requested_settings_sha256(settings)
    assert request["effective_settings_sha256"] == request["effective_settings"][
        "effective_settings_sha256"
    ]
    assert json.loads(request["producer_provenance"]["producer_sample"])[
        "backend"
    ] == "protenix_v2_ensemble"


@pytest.mark.parametrize(
    "selection_mode",
    ["selected_entities", "selected_regions", "selected_residues"],
)
def test_cm_candidate_source_scope_resolves_per_generated_map_and_compiles_zero_based_positions(
    tmp_path: Path, selection_mode: str,
) -> None:
    from services.conformational_mapping.frustrampnn_adapter import prepare_cm_candidate_v2
    from services.frustrampnn.manifests import validate_v2_input_closure
    from services.frustrampnn.runtime import compile_frustrampnn_command_plan
    from services.frustrampnn.settings import (
        FrustraMPNNEffectiveSettings,
        validate_complete_requested_settings,
    )

    generated_entity_differs = selection_mode != "selected_residues"
    source_bytes = _candidate_mmcif() if generated_entity_differs else _candidate_pdb()
    source = tmp_path / ("candidate.cif" if generated_entity_differs else "candidate.pdb")
    source.write_bytes(source_bytes)
    snapshot = _candidate_snapshot(hashlib.sha256(source_bytes).hexdigest())
    settings_payload = default_settings().model_dump(mode="json", exclude_none=False)
    settings_payload.pop("settings_value_origin", None)
    selector = {
        "entity_instance_id": "protein-1",
        "source_entity_id": "protein",
        "label_asym_id": None,
        "auth_asym_id": None,
    }
    settings_payload["protein_selection"] = {
        "mode": selection_mode,
        "entities": [selector] if selection_mode == "selected_entities" else [],
        "regions": [
            {**selector, "sequence_start": 1, "sequence_end": 1}
        ] if selection_mode == "selected_regions" else [],
        "residues": [{
            **selector,
            "label_asym_id": "X",
            "auth_asym_id": "X",
            "auth_seq_id": 10,
            "insertion_code": "",
            "sequence_index": 1,
        }] if selection_mode == "selected_residues" else [],
    }
    settings_payload["source_structure"] = {
        "selected_model_number": 2,
        "preferred_altloc": "A",
    }
    settings = validate_complete_requested_settings(settings_payload)
    request = prepare_cm_candidate_v2(
        source=source,
        output_pdb_path=tmp_path / "canonical_source.pdb",
        structure_map_path=tmp_path / "frustrampnn_structure_map_v1.json",
        request_path=tmp_path / "workflow_component_request_v2.json",
        authority_artifact_path=tmp_path / "authority_artifact_v1.json",
        parent_job_id="cm-parent-job",
        parent_workflow_id="conformational_mapping",
        candidate={
            "candidate_id": "candidate-a",
            "authoritative_structure_path": (
                "native/candidate.cif" if generated_entity_differs
                else "native/candidate.pdb"
            ),
            "backend_coordinates": {
                "backend": "protenix_v2_ensemble",
                "target_id": "target-a",
                "ordered_seed": 7,
                "sample_index": 2,
            },
        },
        complex_snapshot=snapshot,
        requested_settings=settings,
    )

    effective = FrustraMPNNEffectiveSettings.model_validate(
        request["effective_settings"],
        strict=True,
    )
    selector_collection = (
        "entities" if selection_mode == "selected_entities"
        else "regions" if selection_mode == "selected_regions"
        else "residues"
    )
    assert request["requested_settings"]["protein_selection"][selector_collection][0][
        "source_entity_id"
    ] == "protein"
    structure_map = json.loads(
        (tmp_path / "frustrampnn_structure_map_v1.json").read_text()
    )
    assert structure_map["rows"][0]["source_entity_id"] == (
        "generated-protein" if generated_entity_differs else "protein"
    )
    assert effective.resolved_chains[0].entity.source_entity_id == "protein"
    if selection_mode == "selected_regions":
        assert effective.requested_settings.protein_selection.regions[0].sequence_start == 1
    assert effective.requested_settings.source_structure.model_dump() == {
        "selected_model_number": 1,
        "preferred_altloc": "",
    }
    validated_map, validated_effective, _ = validate_v2_input_closure(
        request,
        (tmp_path / "canonical_source.pdb").read_bytes(),
        (tmp_path / "frustrampnn_structure_map_v1.json").read_bytes(),
    )
    assert validated_map == structure_map
    assert validated_effective == effective
    assert [
        residue.model_position
        for chain in effective.resolved_chains
        for residue in chain.residues
    ] == [0]
    plan = compile_frustrampnn_command_plan(effective)
    assert len(plan.entries) == 1
    assert plan.entries[0].chains == (structure_map["rows"][0]["pdb_chain_id"],)
    assert plan.entries[0].positions == (
        None if selection_mode == "selected_entities" else (0,)
    )


def test_cm_external_v2_component_seals_identity_authority_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping.frustrampnn_adapter import prepare_cm_candidate_v2
    from services.frustrampnn.contracts import (
        canonical_json_bytes,
        canonical_json_loads,
        canonical_sha256,
    )
    from services.frustrampnn.manifests import (
        ManifestValidationError,
        validate_external_authority_artifact,
        validate_result_manifest,
    )
    from services.frustrampnn.persistence import (
        _artifact_values,
        load_and_validate_result_bundle,
    )

    source_bytes = _candidate_pdb()
    source = tmp_path / "candidate.pdb"
    source.write_bytes(source_bytes)
    snapshot = _candidate_snapshot(hashlib.sha256(source_bytes).hexdigest())
    candidate = {
        "candidate_id": "candidate-a",
        "authoritative_structure_path": "native/candidate.pdb",
        "backend_coordinates": {
            "backend": "protenix_v2_ensemble",
            "target_id": "target-a",
            "ordered_seed": 7,
            "sample_index": 2,
        },
    }
    request_path = tmp_path / "workflow_component_request_v2.json"
    normalized_path = tmp_path / "canonical_source.pdb"
    structure_map_path = tmp_path / "frustrampnn_structure_map_v1.json"
    authority_path = tmp_path / "authority_artifact_v1.json"
    request = prepare_cm_candidate_v2(
        source=source,
        output_pdb_path=normalized_path,
        structure_map_path=structure_map_path,
        request_path=request_path,
        authority_artifact_path=authority_path,
        parent_job_id="cm-parent-job",
        parent_workflow_id="conformational_mapping",
        candidate=candidate,
        complex_snapshot=snapshot,
        requested_settings=default_settings(),
    )
    fixture = _load_component_fixture()
    authority_bytes = base64.b64decode(
        request["identity_authority_artifact"]["canonical_json_base64"],
        validate=True,
    )
    component = fixture._component()
    fixture._mock_v2_runtime(component, monkeypatch, tmp_path)
    runtime = __import__("services.frustrampnn.runtime", fromlist=["execute_frustrampnn"])

    def execute_all_protein(invocation, _pinned, **_kwargs):
        argv = list(invocation.argv)
        binds = [argv[index + 1] for index, token in enumerate(argv) if token == "--bind"]
        output_root = Path(next(value.split(":", 1)[0] for value in binds if value.endswith(":/bms/output:rw")))
        output = output_root / Path(argv[argv.index("--output") + 1]).name
        rows = ["frustration_pred,position,wildtype,mutation,chain,pdb"]
        rows.extend(f"0.0,0,G,{mutation},X,normalized" for mutation in "ACDEFGHIKLMNPQRSTVWY")
        output.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(runtime, "execute_frustrampnn", execute_all_protein)
    output = tmp_path / "candidate_bundle"

    manifest = component.run_component(
        request=request,
        source_structure=normalized_path,
        structure_map=structure_map_path,
        output_dir=output,
        container=tmp_path / "mock.sif",
        physical_gpu_id=3,
    )

    assert (output / "authority_artifact_v1.json").read_bytes() == authority_bytes
    assert manifest["artifact_count"] == 11
    assert [record["relative_path"] for record in manifest["artifacts"]] == [
        "workflow_component_request_v2.json",
        "authority_artifact_v1.json",
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
    assert canonical_json_loads((output / "authority_artifact_v1.json").read_bytes())["schema_name"] == "producer_manifest"
    validate_result_manifest(output, manifest)
    bundle = load_and_validate_result_bundle(
        output,
        expected_parent_job_id="cm-parent-job",
        terminal_envelope=canonical_json_loads(
            (output / "workflow_component_result_v2.json").read_bytes()
        ),
    )
    authority_value = next(
        value
        for value in _artifact_values(bundle)
        if value["relative_path"] == "authority_artifact_v1.json"
    )
    assert authority_value["role"] == "identity_authority"
    assert authority_value["media_type"] == "application/json"

    manifest_path = output / "frustrampnn_result_manifest_v2.json"
    terminal_path = output / "workflow_component_result_v2.json"
    original_manifest = canonical_json_loads(manifest_path.read_bytes())
    original_terminal = canonical_json_loads(terminal_path.read_bytes())

    forged = canonical_json_loads(authority_bytes)
    forged["entities"][0]["sequence"] = "A"
    forged_bytes = canonical_json_bytes(forged)
    forged_manifest = canonical_json_loads(canonical_json_bytes(original_manifest))
    authority_record = forged_manifest["artifacts"][1]
    authority_record["sha256"] = hashlib.sha256(forged_bytes).hexdigest()
    authority_record["bytes"] = len(forged_bytes)
    forged_terminal = canonical_json_loads(canonical_json_bytes(original_terminal))
    forged_terminal["result_manifest"]["sha256"] = canonical_sha256(forged_manifest)
    (output / "authority_artifact_v1.json").write_bytes(forged_bytes)
    manifest_path.write_bytes(canonical_json_bytes(forged_manifest))
    terminal_path.write_bytes(canonical_json_bytes(forged_terminal))
    with pytest.raises(
        ManifestValidationError,
        match="physical authority bytes disagree with the request-bound authority artifact",
    ):
        validate_result_manifest(output, forged_manifest)

    tampered_request = canonical_json_loads(canonical_json_bytes(request))
    tampered_request["identity_authority_artifact"]["canonical_json_base64"] = (
        base64.b64encode(forged_bytes).decode("ascii")
    )
    tampered_request["identity_authority_artifact"]["sha256"] = hashlib.sha256(
        forged_bytes
    ).hexdigest()
    tampered_structure = canonical_json_loads(structure_map_path.read_bytes())
    tampered_structure["authority_artifact_sha256"] = hashlib.sha256(forged_bytes).hexdigest()
    with pytest.raises(
        ManifestValidationError,
        match="external authority sequence identity disagrees with structure map",
    ):
        validate_external_authority_artifact(
            tampered_request,
            tampered_structure,
            forged_bytes,
        )

    legacy_manifest = canonical_json_loads(canonical_json_bytes(original_manifest))
    legacy_manifest["artifacts"].pop(1)
    legacy_manifest["artifact_count"] = 10
    legacy_request = canonical_json_loads(
        (output / "workflow_component_request_v2.json").read_bytes()
    )
    del legacy_request["identity_authority_artifact"]["bytes"]
    legacy_request_bytes = canonical_json_bytes(legacy_request)
    (output / "workflow_component_request_v2.json").write_bytes(legacy_request_bytes)
    legacy_manifest["request_sha256"] = hashlib.sha256(legacy_request_bytes).hexdigest()
    legacy_request_record = legacy_manifest["artifacts"][0]
    legacy_request_record["sha256"] = hashlib.sha256(legacy_request_bytes).hexdigest()
    legacy_request_record["bytes"] = len(legacy_request_bytes)
    legacy_terminal = canonical_json_loads(canonical_json_bytes(original_terminal))
    legacy_terminal["request_sha256"] = legacy_manifest["request_sha256"]
    legacy_terminal["result_manifest"]["sha256"] = canonical_sha256(legacy_manifest)
    (output / "authority_artifact_v1.json").unlink()
    manifest_path.write_bytes(canonical_json_bytes(legacy_manifest))
    terminal_path.write_bytes(canonical_json_bytes(legacy_terminal))
    with pytest.raises(ManifestValidationError):
        validate_result_manifest(output, legacy_manifest)
    validate_result_manifest(
        output,
        legacy_manifest,
        allow_legacy_v2_external_authority=True,
    )


def test_self_authoritative_v2_rejects_external_authority_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.frustrampnn.contracts import canonical_json_bytes, canonical_json_loads
    from services.frustrampnn.manifests import ManifestValidationError, build_result_manifest

    fixture = _load_component_fixture()
    component = fixture._component()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    request, normalized, structure_map, _ = fixture._v2_inputs(
        inputs,
        residues=[("A", 1)],
        selected=[("A", 0)],
    )
    fixture._mock_v2_runtime(component, monkeypatch, inputs)
    output = tmp_path / "bundle"
    manifest = component.run_component(
        request=request,
        source_structure=normalized,
        structure_map=structure_map,
        output_dir=output,
        container=inputs / "mock.sif",
        physical_gpu_id=3,
    )
    assert manifest["artifact_count"] == 10
    (output / "frustrampnn_result_manifest_v2.json").unlink()
    (output / "workflow_component_result_v2.json").unlink()
    authority = {
        "schema_name": "producer_manifest",
        "schema_version": 1,
        "source_sha256": request["source_artifact"]["sha256"],
        "entities": [],
    }
    (output / "authority_artifact_v1.json").write_bytes(canonical_json_bytes(authority))
    with pytest.raises(
        ManifestValidationError,
        match="external identity authority artifact presence disagrees with request authority",
    ):
        build_result_manifest(output)


def test_external_authority_without_residue_mappings_does_not_invent_label_mapping() -> None:
    from services.frustrampnn.contracts import canonical_json_bytes
    from services.frustrampnn.manifests import validate_external_authority_artifact

    authority = {
        "schema_name": "producer_manifest",
        "schema_version": 1,
        "source_sha256": "a" * 64,
        "cm_complex_snapshot_sha256": "b" * 64,
        "entities": [
            {
                "entity_type": "protein",
                "entity_instance_id": "A",
                "source_entity_id": "1",
                "label_asym_id": "A",
                "auth_asym_id": "A",
                "sequence": "MG",
            }
        ],
    }
    payload = canonical_json_bytes(authority)
    digest = hashlib.sha256(payload).hexdigest()
    request = {
        "identity_authority": "cm_complex_snapshot",
        "identity_authority_artifact": {
            "relative_path": "authority_artifact_v1.json",
            "media_type": "application/json",
            "sha256": digest,
            "canonical_json_base64": base64.b64encode(payload).decode("ascii"),
            "cm_complex_snapshot_sha256": "b" * 64,
        },
        "source_artifact": {"sha256": "a" * 64},
    }
    structure = {
        "authority_artifact_sha256": digest,
        "source_sha256": "a" * 64,
        "rows": [
            {
                "entity_instance_id": "A",
                "source_entity_id": "1",
                "label_asym_id": "A",
                "auth_asym_id": "A",
                "sequence_index": index,
                "label_seq_id": index,
                "auth_seq_id": index,
                "insertion_code": "",
                "wt": wt,
            }
            for index, wt in enumerate("MG", start=1)
        ],
    }

    validate_external_authority_artifact(request, structure, payload)


@pytest.mark.parametrize(
    ("backend", "coordinates"),
    [
        (
            "protenix_v2_ensemble",
            {"backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 7, "sample_index": 2},
        ),
        (
            "confornets",
            {
                "backend": "confornets", "target_id": "target-a", "task": "diversity",
                "test_case_id": "case-a", "reference_id": None, "run_index": 0,
                "saved_step": 4, "confornet_index": 1, "sample_index": 2,
            },
        ),
        (
            "external_import",
            {"backend": "external_import", "target_id": "target-a", "staged_index": 2},
        ),
    ],
)
def test_cm_preparer_fans_in_every_producer_with_exact_candidate_identity_and_cardinality(
    tmp_path: Path,
    backend: str,
    coordinates: dict[str, object],
) -> None:
    module = _load_cm_preparer()
    canonical = tmp_path / "canonical"
    native = canonical / "native"
    native.mkdir(parents=True)
    payload = _candidate_pdb()
    candidates = []
    for index in range(2):
        candidate_id = f"candidate-{index}"
        relative = f"native/{candidate_id}.pdb"
        (canonical / relative).write_bytes(payload)
        candidate_coordinates = {**coordinates}
        if "sample_index" in candidate_coordinates:
            candidate_coordinates["sample_index"] = index
        if "staged_index" in candidate_coordinates:
            candidate_coordinates["staged_index"] = index
        candidates.append({
            "candidate_id": candidate_id,
            "authoritative_structure_path": relative,
            "backend_coordinates": candidate_coordinates,
        })
    ensemble = {
        "backend": backend,
        "expected_cardinality": 2,
        "candidates": candidates,
    }
    (canonical / "cm_ensemble_v1.json").write_text(json.dumps(ensemble), encoding="utf-8")
    request = {
        "request_id": "cm-parent-job",
        "backend": backend,
        "frustrampnn_requiredness": "required",
        "frustrampnn_settings": default_settings().model_dump(mode="json", exclude_none=False),
    }
    request_path = tmp_path / "cm_request_v1.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    snapshot = _candidate_snapshot(hashlib.sha256(payload).hexdigest())
    snapshots_path = tmp_path / "cm_complex_snapshots_v1.json"
    snapshots_path.write_text(json.dumps([snapshot]), encoding="utf-8")
    prepared = tmp_path / "prepared"
    manifest_path = tmp_path / "cm_frustrampnn_preparation_manifest_v1.json"

    manifest = module.prepare_ensemble_candidates(
        parent_job_id="cm-parent-job",
        request_path=request_path,
        snapshots_path=snapshots_path,
        canonical_dir=canonical,
        output_dir=prepared,
        manifest_path=manifest_path,
    )

    assert manifest["expected_cardinality"] == 2
    assert [item["candidate_id"] for item in manifest["candidates"]] == [
        "candidate-0", "candidate-1",
    ]
    assert [
        json.loads(
            (prepared / item["candidate_id"] / "workflow_component_request_v2.json").read_text()
        )["candidate_id"]
        for item in manifest["candidates"]
    ] == ["candidate-0", "candidate-1"]
    for item in manifest["candidates"]:
        candidate_root = prepared / item["candidate_id"]
        assert sorted(path.name for path in candidate_root.iterdir()) == [
            "canonical_source.pdb",
            "frustrampnn_structure_map_v1.json",
            "workflow_component_request_v2.json",
        ]


def test_cm_preparer_required_candidate_failure_removes_partial_fanout(
    tmp_path: Path,
) -> None:
    module = _load_cm_preparer()
    canonical = tmp_path / "canonical"
    (canonical / "native").mkdir(parents=True)
    payload = _candidate_pdb()
    (canonical / "native/good.pdb").write_bytes(payload)
    (canonical / "cm_ensemble_v1.json").write_text(json.dumps({
        "backend": "external_import",
        "expected_cardinality": 2,
        "candidates": [
            {
                "candidate_id": "candidate-good",
                "authoritative_structure_path": "native/good.pdb",
                "backend_coordinates": {"backend": "external_import", "target_id": "target-a", "staged_index": 0},
            },
            {
                "candidate_id": "candidate-missing",
                "authoritative_structure_path": "native/missing.pdb",
                "backend_coordinates": {"backend": "external_import", "target_id": "target-a", "staged_index": 1},
            },
        ],
    }), encoding="utf-8")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({
        "request_id": "cm-parent-job",
        "backend": "external_import",
        "frustrampnn_requiredness": "required",
        "frustrampnn_settings": default_settings().model_dump(mode="json", exclude_none=False),
    }), encoding="utf-8")
    snapshots_path = tmp_path / "snapshots.json"
    snapshots_path.write_text(json.dumps([
        _candidate_snapshot(hashlib.sha256(payload).hexdigest())
    ]), encoding="utf-8")
    prepared = tmp_path / "prepared"
    manifest_path = tmp_path / "manifest.json"

    with pytest.raises(Exception, match="missing|regular|candidate"):
        module.prepare_ensemble_candidates(
            parent_job_id="cm-parent-job",
            request_path=request_path,
            snapshots_path=snapshots_path,
            canonical_dir=canonical,
            output_dir=prepared,
            manifest_path=manifest_path,
        )

    assert not prepared.exists()
    assert not manifest_path.exists()


def test_cm_postprocessor_validates_v2_bundle_and_passes_global_landscape_directly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_cm_postprocessor()
    candidate_id = "candidate-a"
    invocation_id = "frustrampnn:cm-parent-job:candidate-a"
    source_sha256 = "1" * 64
    snapshot_sha256 = "2" * 64
    request_sha256 = "3" * 64
    request = {
        "request_id": "cm-request-record",
        "request_sha256": "4" * 64,
        "backend": "external_import",
        "targets": [{"target_id": "target-a"}],
        "analysis_policy": {
            "clash_detector_id": "bms_clash",
            "clash_detector_version": "1",
        },
    }
    request_path = tmp_path / "cm_request_v1.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    ensemble = {
        "expected_cardinality": 1,
        "candidates": [{
            "candidate_id": candidate_id,
            "backend_coordinates": {
                "backend": "external_import", "target_id": "target-a", "staged_index": 0,
            },
        }],
    }
    (canonical / "cm_ensemble_v1.json").write_text(json.dumps(ensemble), encoding="utf-8")
    preparation = {
        "parent_job_id": "cm-parent-job",
        "expected_cardinality": 1,
        "candidates": [{
            "candidate_id": candidate_id,
            "invocation_id": invocation_id,
            "source_sha256": source_sha256,
            "cm_complex_snapshot_sha256": snapshot_sha256,
            "requested_settings_sha256": "5" * 64,
            "effective_settings_sha256": "6" * 64,
            "request_sha256": request_sha256,
        }],
    }
    preparation_path = tmp_path / "preparation.json"
    preparation_path.write_text(json.dumps(preparation), encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "frustrampnn_result_manifest_v2.json").write_bytes(
        b'{"schema_version":2}'
    )
    global_landscape = {
        "schema_name": "frustrampnn_landscape",
        "schema_version": 2,
        "candidate_id": candidate_id,
        "source_artifact_sha256": source_sha256,
        "residues": [],
    }
    component_request = {
        "candidate_id": candidate_id,
        "invocation_id": invocation_id,
        "parent_job_id": "cm-parent-job",
        "parent_workflow_id": "conformational_mapping",
        "requiredness": "required",
        "source_artifact": {"sha256": source_sha256},
        "identity_authority_artifact": {
            "cm_complex_snapshot_sha256": snapshot_sha256,
        },
        "requested_settings_sha256": "5" * 64,
        "effective_settings_sha256": "6" * 64,
    }
    component_result = {
        "status": "succeeded",
        "candidate_id": candidate_id,
        "invocation_id": invocation_id,
        "parent_job_id": "cm-parent-job",
    }
    payloads = {
        "workflow_component_request_v2.json": canonical_json_bytes(component_request),
        "workflow_component_result_v2.json": canonical_json_bytes(component_result),
        "normalized_input.pdb": _candidate_pdb(),
        "frustrampnn_structure_map_v1.json": canonical_json_bytes({
            "schema_name": "frustrampnn_structure_map", "schema_version": 1,
            "candidate_id": candidate_id, "rows": [],
        }),
        "frustrampnn_landscape_v2.json": canonical_json_bytes(global_landscape),
    }
    preparation["candidates"][0]["request_sha256"] = hashlib.sha256(
        payloads["workflow_component_request_v2.json"]
    ).hexdigest()
    preparation_path.write_text(json.dumps(preparation), encoding="utf-8")
    validated: list[tuple[Path, dict[str, object]]] = []

    def fake_validate(root: Path, manifest: dict[str, object]):
        validated.append((Path(root), manifest))
        return payloads

    seen_landscapes: list[object] = []

    def fake_analyze(_ensemble, landscapes, **_kwargs):
        seen_landscapes.append(landscapes[candidate_id])
        return {
            "analysis_id": "analysis-a", "support_records": [], "pair_ledger": [],
            "ranking_policy": {}, "clash_records": [], "exclusions": [], "results": [],
        }

    clash_calls: list[dict[str, object]] = []

    def fake_build_clash_rows(
        normalized_pdb: Path,
        structure_map: dict[str, object],
        *,
        candidate_id: str,
        detector_id: str,
        detector_version: str,
    ) -> dict[object, object]:
        clash_calls.append({
            "normalized_pdb": normalized_pdb,
            "structure_map": structure_map,
            "candidate_id": candidate_id,
            "detector_id": detector_id,
            "detector_version": detector_version,
        })
        return {}

    monkeypatch.setattr(module, "validate_result_manifest", fake_validate)
    monkeypatch.setattr(module, "analyze_landscapes", fake_analyze)
    monkeypatch.setattr(module, "build_clash_rows", fake_build_clash_rows)
    monkeypatch.setattr(module, "derive_state_landscape_analysis_for_request", lambda *_args: None)
    output = tmp_path / "output"

    module.postprocess_canonical_bundles(
        request_path=request_path,
        canonical_dir=canonical,
        preparation_manifest_path=preparation_path,
        bundle_dirs=[bundle],
        output_dir=output,
    )

    assert validated == [
        (bundle, {"schema_version": 2}),
        (output / "frustrampnn/results/candidate-a", {"schema_version": 2}),
    ]
    assert clash_calls == [{
        "normalized_pdb": output / "frustrampnn/results/candidate-a/normalized_input.pdb",
        "structure_map": json.loads(
            payloads["frustrampnn_structure_map_v1.json"]
        ),
        "candidate_id": candidate_id,
        "detector_id": "bms_clash",
        "detector_version": "1",
    }]
    assert seen_landscapes == [global_landscape]
    references = json.loads(
        (output / "derived/cm_frustrampnn_result_references_v1.json").read_text()
    )
    assert references["results"][0]["candidate_id"] == candidate_id
    assert references["results"][0]["source_sha256"] == source_sha256
    assert references["results"][0]["cm_complex_snapshot_sha256"] == snapshot_sha256
    assert references["parent_job_id"] == "cm-parent-job"
    index = json.loads((output / "cm_derived_index_v1.json").read_text())
    assert "landscapes" not in index
    assert not list(output.rglob("cm_frustration_landscape_v1.json"))


def test_cm_state_analysis_accepts_global_v2_landscapes_without_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.conformational_mapping import state_landscape_analysis as state
    from services.conformational_mapping.contracts import AA_ORDER

    def landscape(candidate_id: str, native_score: float) -> dict[str, object]:
        return {
            "schema_name": "frustrampnn_landscape",
            "schema_version": 2,
            "target_id": "target-a",
            "candidate_id": candidate_id,
            "execution_configuration_sha256": "a" * 64,
            "requested_settings_sha256": "b" * 64,
            "effective_settings_sha256": "c" * 64,
            "runtime_identity_sha256": "d" * 64,
            "threshold_policy_id": "frustrampnn_class_v1",
            "threshold_policy_sha256": "e" * 64,
            "residues": [{
                "entity_instance_id": "protein-1", "auth_asym_id": "A",
                "auth_seq_id": 1, "insertion_code": "", "sequence_index": 1, "wt": "A",
                "slots": [{
                    "mutation_aa": aa,
                    "score": native_score if aa == "A" else 0.0,
                    "class": "neutral", "scoreable": True, "status": "ok",
                    "reason": None, "native": aa == "A",
                } for aa in AA_ORDER],
            }],
        }

    def structure_map(candidate_id: str) -> dict[str, object]:
        return {
            "schema_name": "frustrampnn_structure_map", "schema_version": 1,
            "target_id": "target-a", "candidate_id": candidate_id,
            "rows": [{
                "entity_instance_id": "protein-1", "auth_asym_id": "A",
                "auth_seq_id": 1, "insertion_code": "", "sequence_index": 1,
                "residue_name": "ALA", "wt": "A", "status": "mapped",
            }],
        }

    monkeypatch.setattr(state, "validate_frustrampnn_schema", lambda *_args: None, raising=False)
    ensemble = {"candidates": [
        {"candidate_id": "state-a", "backend_coordinates": {
            "backend": "protenix_v2_ensemble", "target_id": "target-a",
            "ordered_seed": 1, "sample_index": 0,
        }},
        {"candidate_id": "state-b", "backend_coordinates": {
            "backend": "protenix_v2_ensemble", "target_id": "target-a",
            "ordered_seed": 2, "sample_index": 0,
        }},
    ]}

    artifact = state.derive_state_landscape_analysis(
        ensemble,
        [landscape("state-a", -0.5), landscape("state-b", -0.25)],
        [structure_map("state-a"), structure_map("state-b")],
        comparison={
            "mode": "pairwise", "comparison_target_id": "target-a",
            "comparison_scope": "all_within_target", "reference_candidate_id": None,
            "reference_backend_coordinates": None,
            "resolved_pairs": [{
                "pair_id": "state-a__state-b",
                "candidate_a_id": "state-a", "candidate_b_id": "state-b",
            }],
        },
    )

    assert artifact["resolved_pairs"][0]["candidate_a_id"] == "state-a"
    assert artifact["resolved_pairs"][0]["candidate_b_id"] == "state-b"


def test_cm_nextflow_wires_every_candidate_through_canonical_v2_and_no_direct_runtime() -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = (root / "workflows" / "conformational_mapping.nf").read_text(encoding="utf-8")
    module = (root / "modules" / "conformational_mapping_frustrampnn.nf").read_text(encoding="utf-8")

    assert "CONFORMATIONAL_MAPPING_PROTENIX" in workflow
    assert "CONFORMATIONAL_MAPPING_CONFORNETS" in workflow
    assert "CONFORMATIONAL_MAPPING_IMPORT" in workflow
    assert "PrepareConformationalMappingFrustraMPNNV2" in workflow
    assert "CanonicalFrustraMPNNV2" in workflow
    assert "StageConformationalMappingFrustraMPNNResult" in workflow
    assert "CanonicalConformationalAnalysisPlaneV2" in workflow
    assert "flatMap" in workflow
    assert "workflow_component_request_v2.json" in workflow
    assert "canonical_source.pdb" in workflow
    assert "frustrampnn_structure_map_v1.json" in workflow
    assert "errorStrategy 'terminate'" in module
    assert "postprocess_conformational_mapping_frustrampnn_v2.py" in module
    assert "run_conformational_mapping_analysis_plane.py" not in module
    assert "--checkpoint" not in module
    assert "--container" not in module
    assert "--gpu-id" not in module


def test_cm_persistence_reuses_global_result_rows_without_legacy_projection() -> None:
    root = Path(__file__).resolve().parents[3]
    ingester = (root / "platform/api/services/result_ingester.py").read_text(encoding="utf-8")
    persistence = (
        root / "platform/api/services/conformational_mapping/persistence.py"
    ).read_text(encoding="utf-8")

    assert "is_conformational_mapping" in ingester
    assert "canonical_count is not None and not is_conformational_mapping" in ingester
    assert "canonical_count != len(ensemble" not in ingester
    assert "frustrampnn_result_references" in ingester
    assert "frustrampnn_landscape_v2.json" in ingester
    assert "FrustraMPNNResult" in persistence
    assert "FrustraMPNNLandscapeRow" in persistence
    assert "canonical_global_mode" in persistence
    assert "required canonical FrustraMPNN results are not persisted" in persistence
    assert 'landscapes_to_insert = bundle.get("cm_frustration_landscapes") or []' in persistence
