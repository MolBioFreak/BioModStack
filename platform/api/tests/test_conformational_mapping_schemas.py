from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.conformational_mapping.contracts import (
    AA_ORDER,
    SCHEMA_FILENAMES,
    ContractValidationError,
    ResumeDescriptor,
    candidate_id,
    canonical_json_bytes,
    canonical_sha256,
    context_difference,
    ensure_candidate_id_uniqueness,
    feature_policy_sha256,
    handoff_idempotency_key,
    hierarchical_aggregate,
    hotspot_score,
    load_schema,
    normalize_artifact_class_alias,
    parse_backend_coordinates,
    request_sha256,
    roundtrip_instance_mappings,
    substitution_difference,
    switch_score,
    validate_complex_case,
    validate_feature_policy,
    validate_landscape_slots,
    validate_manifest_set_equality,
    validate_contract_bundle,
    validate_phase0_contract_vector,
    validate_schema,
    validate_seed_sources,
    validate_structure_map_snapshot_binding,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures" / "conformational_mapping"
PHASE_0 = FIXTURES / "phase_0_vectors"
SCHEMA_FIXTURES = FIXTURES / "schemas"


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cm001_rejects_seed_conflicts() -> None:
    defaults = {case["setting"]: case for case in _json(PHASE_0 / "defaults.json")["cases"]}
    approved = defaults["ordered_seeds"]["value"]

    assert validate_seed_sources(api=approved, generated_json=approved, cli=approved) == approved
    for malformed in ([], [101, 101], [-(2**31) - 1], [2**31], [101, "202"]):
        with pytest.raises(ContractValidationError):
            validate_seed_sources(api=malformed, generated_json=malformed, cli=malformed)
    with pytest.raises(ContractValidationError, match="conflict"):
        validate_seed_sources(api=approved, generated_json=list(reversed(approved)), cli=approved)

    positive = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")["cm_request_v1"]
    validate_schema("cm_request_v1", positive)
    conflicting_modes = copy.deepcopy(positive)
    conflicting_modes["runtime_policy"]["n_cycle"] = 10
    with pytest.raises(ContractValidationError):
        validate_schema("cm_request_v1", conflicting_modes)


def test_cm001b_state_landscape_comparison_authority_is_structural_and_target_bound() -> None:
    request = copy.deepcopy(_json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")["cm_request_v1"])
    request["state_landscape_comparison"] = {
        "mode": "pairwise", "target_id": "target-a", "scope": "all_within_target",
    }
    request["request_sha256"] = request_sha256(request)
    validate_schema("cm_request_v1", request)

    malformed = copy.deepcopy(request)
    malformed["state_landscape_comparison"]["scope"] = "all_other_within_target"
    malformed["request_sha256"] = request_sha256(malformed)
    with pytest.raises(ContractValidationError, match="state_landscape_comparison"):
        validate_schema("cm_request_v1", malformed)

    reference = copy.deepcopy(request)
    reference["state_landscape_comparison"] = {
        "mode": "reference", "target_id": "target-a", "scope": "all_other_within_target",
        "reference_backend_coordinates": {
            "backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 101, "sample_index": 0,
        },
    }
    reference["request_sha256"] = request_sha256(reference)
    validate_schema("cm_request_v1", reference)

    mismatched_reference = copy.deepcopy(reference)
    mismatched_reference["state_landscape_comparison"]["reference_backend_coordinates"]["target_id"] = "other-target"
    mismatched_reference["request_sha256"] = request_sha256(mismatched_reference)
    with pytest.raises(ContractValidationError, match="reference target"):
        validate_schema("cm_request_v1", mismatched_reference)


def test_cm002_instance_ids_equal_count() -> None:
    cases = _json(PHASE_0 / "complex_cases.json")["cases"]
    for case in cases:
        if case["kind"] == "positive":
            validate_complex_case(case)
        else:
            with pytest.raises(ContractValidationError, match=case["reason"]):
                validate_complex_case(case)


def test_cm003_repeated_copy_mapping_roundtrip() -> None:
    snapshot = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")["cm_complex_snapshot_v1"]
    validate_schema("cm_complex_snapshot_v1", snapshot)
    assert roundtrip_instance_mappings(snapshot) == {
        ("cm_ptx_target-a_f297ff33e05e862170ad", "repeat_prot", "repeat_prot_copy1"): (
            "target-a",
            "runtime-protein",
            "runtime-copy1",
            0,
            "1",
            "A",
            "A",
            0,
        ),
        ("cm_ptx_target-a_f297ff33e05e862170ad", "repeat_prot", "repeat_prot_copy2"): (
            "target-a",
            "runtime-protein",
            "runtime-copy2",
            1,
            "1",
            "B",
            "B",
            1,
        ),
    }

    composition = _json(PHASE_0 / "protenix_composition.json")["cases"]
    for case in composition:
        assert len(case["source_instances"]) == len(case["runtime_instances"]) == len(case["output_instances"])
        assert len(set(case["source_instances"])) == len(case["source_instances"])


def test_cm003b_multi_candidate_output_entity_mapping_is_candidate_bijective() -> None:
    snapshot = copy.deepcopy(_json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")["cm_complex_snapshot_v1"])
    first_candidate = snapshot["instance_mappings"]
    for mapping in first_candidate:
        mapping["output_entity_id"] = "10"

    second_candidate = copy.deepcopy(first_candidate)
    for index, mapping in enumerate(second_candidate, start=40):
        mapping["candidate_id"] = "cm_ptx_target-a_second-candidate"
        mapping["output_entity_id"] = "40"
        mapping["output_label_asym_id"] = f"REN_{index}"
        mapping["output_auth_asym_id"] = f"AUTH_{index}"
    snapshot["instance_mappings"].extend(second_candidate)

    validate_schema("cm_complex_snapshot_v1", snapshot)
    result = roundtrip_instance_mappings(snapshot)
    assert set(result) == {
        ("cm_ptx_target-a_f297ff33e05e862170ad", "repeat_prot", "repeat_prot_copy1"),
        ("cm_ptx_target-a_f297ff33e05e862170ad", "repeat_prot", "repeat_prot_copy2"),
        ("cm_ptx_target-a_second-candidate", "repeat_prot", "repeat_prot_copy1"),
        ("cm_ptx_target-a_second-candidate", "repeat_prot", "repeat_prot_copy2"),
    }

    collapsed_output_entity = copy.deepcopy(snapshot)
    collapsed_output_entity["entities"].append(
        {
            "entity_type": "protein",
            "source_entity_id": "different-protein",
            "count": 1,
            "ordered_instance_ids": ["different-copy"],
            "sequence": "G",
        }
    )
    extra = copy.deepcopy(collapsed_output_entity["instance_mappings"][0])
    extra.update(source_entity_id="different-protein", source_instance_id="different-copy")
    collapsed_output_entity["instance_mappings"].insert(2, extra)
    with pytest.raises(ContractValidationError, match="candidate.*bijective"):
        validate_schema("cm_complex_snapshot_v1", collapsed_output_entity)

    missing_instance = copy.deepcopy(snapshot)
    missing_instance["instance_mappings"].pop()
    with pytest.raises(ContractValidationError, match="candidate.*complete"):
        validate_schema("cm_complex_snapshot_v1", missing_instance)


def test_cm004_backend_coordinates_are_discriminated() -> None:
    coordinates = [
        {"backend": "protenix_v2_ensemble", "target_id": "t", "ordered_seed": 101, "sample_index": 0},
        {
            "backend": "confornets",
            "target_id": "t",
            "task": "diversity",
            "test_case_id": "case",
            "reference_id": None,
            "run_index": 0,
            "saved_step": 1,
            "confornet_index": 0,
            "sample_index": 0,
        },
        {
            "backend": "external_import",
            "target_id": "t",
            "staged_index": 0,
            "source_content_sha256": "a" * 64,
            "staged_receipt_sha256": "b" * 64,
        },
    ]
    parsed = [parse_backend_coordinates(value) for value in coordinates]
    assert [value.backend for value in parsed] == ["protenix_v2_ensemble", "confornets", "external_import"]

    mixed = dict(coordinates[0], task="diversity")
    with pytest.raises(ValidationError):
        parse_backend_coordinates(mixed)

    cases = _json(PHASE_0 / "confornets_cases.json")["cases"]
    for case in cases[:3]:
        cardinality = (
            len(case["runs"])
            * len(case["saved_steps"])
            * len(case["confornet_indices"])
            * len(case["sample_indices"])
        )
        assert cardinality == case["expected_cardinality"]


def test_cm005_candidate_ids_do_not_collide() -> None:
    protenix = {"backend": "protenix_v2_ensemble", "target_id": "Target A", "ordered_seed": 101, "sample_index": 0}
    confornets = {
        "backend": "confornets",
        "target_id": "Target A",
        "task": "diversity",
        "test_case_id": "case",
        "reference_id": None,
        "run_index": 0,
        "saved_step": 1,
        "confornet_index": 0,
        "sample_index": 0,
    }
    external = {
        "backend": "external_import",
        "target_id": "Target A",
        "staged_index": 0,
        "source_content_sha256": "a" * 64,
        "staged_receipt_sha256": "b" * 64,
    }
    records = [{"candidate_id": candidate_id(c), "backend_coordinates": c} for c in (protenix, confornets, external)]
    assert len({record["candidate_id"] for record in records}) == 3
    ensure_candidate_id_uniqueness(records)

    collision = copy.deepcopy(records)
    collision[1]["candidate_id"] = collision[0]["candidate_id"]
    with pytest.raises(ContractValidationError, match="collision"):
        ensure_candidate_id_uniqueness(collision)


def test_cm006_resume_descriptor_is_complete() -> None:
    coordinates = [
        {
            "backend": "protenix_v2_ensemble",
            "target_id": "target-a",
            "ordered_seed": seed,
            "sample_index": sample,
        }
        for seed in (101, 202)
        for sample in (0, 1)
    ]
    feature_policy = {"mode": "regenerate_mutated_protein_v1"}
    descriptor = {
        "request_sha256": "a" * 64,
        "source_snapshot_sha256": "b" * 64,
        "complex_snapshot_sha256": "c" * 64,
        "backend": "protenix_v2_ensemble",
        "backend_version": "1",
        "backend_commit": "abc123",
        "runtime_identity": "runtime-1",
        "container_digest": "sha256:" + "d" * 64,
        "model_id": "protenix",
        "checkpoint_sha256": "e" * 64,
        "feature_policy": feature_policy,
        "feature_policy_sha256": feature_policy_sha256(feature_policy),
        "ordered_seeds": [101, 202],
        "samples_per_seed": 2,
        "coordinate_plan": coordinates,
        "expected_candidate_cardinality": 4,
        "expected_manifest_schema": "cm_ensemble",
        "expected_manifest_version": 1,
        "required_artifact_roles": ["authoritative_cif", "confidence_json", "full_data_json"],
        "expected_manifest_contract_sha256": "1" * 64,
        "settings_runtime_policy_sha256": "2" * 64,
    }
    model = ResumeDescriptor.model_validate(descriptor)
    assert model.resume_key == canonical_sha256(descriptor)
    for required in ResumeDescriptor.model_fields:
        if required == "resume_key":
            continue
        incomplete = dict(descriptor)
        incomplete.pop(required)
        with pytest.raises(ValidationError):
            ResumeDescriptor.model_validate(incomplete)


def test_cm007_manifest_rejects_missing_extra_partial() -> None:
    expected = {"a/structure.cif", "a/confidence.json", "a/full_data.json"}
    validate_manifest_set_equality(expected, observed=expected, referenced=expected)
    for observed, referenced in (
        (expected - {"a/full_data.json"}, expected),
        (expected | {"a/extra.log"}, expected),
        (expected, expected - {"a/confidence.json"}),
        (expected, expected | {"a/unreferenced.json"}),
    ):
        with pytest.raises(ContractValidationError):
            validate_manifest_set_equality(expected, observed=observed, referenced=referenced)
    with pytest.raises(ContractValidationError, match="basename"):
        validate_manifest_set_equality({"a/x.cif", "b/x.cif"}, observed={"a/x.cif", "b/x.cif"}, referenced={"a/x.cif", "b/x.cif"})


def test_cm008_exact_twenty_slots() -> None:
    vector = _json(PHASE_0 / "frustrampnn.json")
    assert vector["aa_order"] == AA_ORDER
    residue = vector["cases"][2]["residue"]
    slots = [
        {
            **slot,
            "wt": residue["wt"],
            "native": slot["mutation_aa"] == residue["wt"],
            "class": "high" if slot["score"] <= -1.0 else "minimally_frustrated" if slot["score"] >= 0.58 else "neutral",
            "scoreable": True,
            "reason": None,
        }
        for slot in residue["slots"]
    ]
    validate_landscape_slots(residue["wt"], slots)

    for broken in (
        slots[:-1],
        [*slots[:-1], slots[0]],
        [dict(slot, mutation_aa="B") if index == 0 else slot for index, slot in enumerate(slots)],
    ):
        with pytest.raises(ContractValidationError):
            validate_landscape_slots(residue["wt"], broken)
    with pytest.raises(ContractValidationError):
        validate_landscape_slots("A", vector["cases"][3]["rows"])


def test_cm009_analysis_formula_vectors() -> None:
    vector = _json(SCHEMA_FIXTURES / "positive" / "analysis_vectors.json")
    summary = hierarchical_aggregate(vector["hierarchical_values"], expected_inner_counts={"101": 2, "202": 2})
    assert summary.mean == pytest.approx(vector["expected"]["hierarchical_mean"])
    assert summary.mean != pytest.approx(vector["expected"]["flat_mean"])
    assert summary.outer_support_fraction == vector["expected"]["outer_support_fraction"]
    assert summary.coordinate_support_fraction == vector["expected"]["coordinate_support_fraction"]

    formula = vector["formula"]
    d_sub = substitution_difference(formula["mutant_score"], formula["native_score"])
    d_ctx = context_difference(formula["context_b"], formula["context_a"])
    assert d_sub == pytest.approx(formula["expected_d_sub"])
    assert d_ctx == pytest.approx(formula["expected_d_ctx"])
    assert hotspot_score(formula["coordinate_support"], abs(d_sub)) == pytest.approx(formula["expected_hotspot_score"])
    assert switch_score(formula["coordinate_support"], formula["context_transition_rate"], d_ctx) == pytest.approx(
        formula["expected_switch_score"]
    )


def test_cm010_feature_modes_and_hash_differences() -> None:
    modes = [
        "regenerate_mutated_protein_v1",
        "paired_regenerate_changed_protein_v1",
        "features_disabled_control_v1",
    ]
    policies = [validate_feature_policy({"mode": mode}) for mode in modes]
    hashes = [feature_policy_sha256(policy) for policy in policies]
    assert len(set(hashes)) == 3
    assert hashes == [
        "3bff1c6ec42ea0a91ab86dbc32f578461de8fd0a488a3ce2b03ee0f8359c293d",
        "d01292bc174ed4e5e09f83ac8ca6a09719b37ac1e22ecf545aed0a5efb45c63f",
        "4a1628245a1ff20ea38a0e692b19c7fba6a0519de814a6b5ab77ae1c6896241b",
    ]
    with pytest.raises(ValidationError):
        validate_feature_policy({"mode": "reuse_changed_sequence_msa"})


def test_cm011_handoff_idempotency_vector() -> None:
    vector = _json(SCHEMA_FIXTURES / "positive" / "handoff_vector.json")
    key = handoff_idempotency_key(vector["source_digest"], vector["candidate_set"], vector["resampling_settings"])
    assert key == vector["expected_idempotency_key"]
    assert key == handoff_idempotency_key(vector["source_digest"], vector["candidate_set"], vector["resampling_settings"])
    changed = dict(vector["resampling_settings"], ordered_seeds=[101, 303])
    assert handoff_idempotency_key(vector["source_digest"], vector["candidate_set"], changed) != key


def test_cm012_unknown_fields_fail_closed() -> None:
    fixtures = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")
    unknown_values = _json(SCHEMA_FIXTURES / "negative" / "unknown_fields.json")
    assert set(fixtures) == set(SCHEMA_FILENAMES) - {"cm_state_landscape_analysis_v1"}
    for schema_key, instance in fixtures.items():
        schema = load_schema(schema_key)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{SCHEMA_FILENAMES[schema_key]}")
        validate_schema(schema_key, instance)
        unknown = copy.deepcopy(instance)
        unknown["silent_drop_me"] = unknown_values["top_level_field"]
        with pytest.raises(ContractValidationError):
            validate_schema(schema_key, unknown)

    nested_unknown = copy.deepcopy(fixtures["cm_request_v1"])
    nested_unknown["runtime_policy"]["silent_drop_me"] = unknown_values["nested_field"]
    with pytest.raises(ContractValidationError):
        validate_schema("cm_request_v1", nested_unknown)

    canonical = {"z": [2, 1], "é": {"b": True, "a": None}}
    expected_bytes = b'{"z":[2,1],"\xc3\xa9":{"a":null,"b":true}}'
    assert canonical_json_bytes(canonical) == expected_bytes
    assert canonical_sha256(canonical) == hashlib.sha256(expected_bytes).hexdigest()
    with pytest.raises(ContractValidationError):
        canonical_json_bytes({"bad": float("nan")})

    assert normalize_artifact_class_alias("monomer_conformation") == "monomer_conformation"
    assert normalize_artifact_class_alias("conformer") == "monomer_conformation"
    for unknown_alias in ("conformation", "monomer-conformation", "Conformer", " conformer ", ""):
        with pytest.raises(ContractValidationError):
            normalize_artifact_class_alias(unknown_alias)


def test_all_contracts_reject_recursive_nonfinite_values() -> None:
    fixtures = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")
    mutations = {
        "cm_request_v1": ("analysis_policy", "outer_support_minimum"),
        "cm_complex_snapshot_v1": ("admission", "atom_count"),
        "cm_native_artifacts_v1": ("files", 0, "bytes"),
        "cm_ensemble_v1": ("expected_cardinality",),
        "cm_structure_map_v1": ("source_bytes",),
        "cm_frustration_landscape_v1": ("residues", 0, "slots", 0, "score"),
        "cm_analysis_v1": ("results", 0, "coordinate_support_fraction"),
        "cm_mutagenesis_handoff_v1": ("ranking_components", "hotspot_score"),
    }
    for schema_key, path in mutations.items():
        instance = copy.deepcopy(fixtures[schema_key])
        current = instance
        for part in path[:-1]:
            current = current[part]
        current[path[-1]] = float("nan")
        with pytest.raises(ContractValidationError, match="non-finite"):
            validate_schema(schema_key, instance)


def test_request_hash_and_target_identity_are_authoritative() -> None:
    request = copy.deepcopy(
        _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")["cm_request_v1"]
    )
    assert request["request_sha256"] == request_sha256(request)
    validate_schema("cm_request_v1", request)

    wrong_hash = copy.deepcopy(request)
    wrong_hash["request_sha256"] = "f" * 64
    with pytest.raises(ContractValidationError, match="request_sha256"):
        validate_schema("cm_request_v1", wrong_hash)

    for duplicate in (
        {"target_id": request["targets"][0]["target_id"], "target_order": 1},
        {"target_id": "target-b", "target_order": request["targets"][0]["target_order"]},
    ):
        malformed = copy.deepcopy(request)
        malformed["targets"].append(duplicate)
        malformed["request_sha256"] = request_sha256(malformed)
        with pytest.raises(ContractValidationError, match="target"):
            validate_schema("cm_request_v1", malformed)


def test_native_ensemble_bundle_is_exactly_cross_bound() -> None:
    fixtures = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")
    validate_contract_bundle(fixtures)

    adversarial_mutations = []
    wrong_hash = copy.deepcopy(fixtures)
    wrong_hash["cm_ensemble_v1"]["candidates"][0]["authoritative_structure_sha256"] = "f" * 64
    adversarial_mutations.append(wrong_hash)
    duplicate_path = copy.deepcopy(fixtures)
    duplicate_path["cm_native_artifacts_v1"]["files"][1]["relative_path"] = duplicate_path[
        "cm_native_artifacts_v1"
    ]["files"][0]["relative_path"]
    adversarial_mutations.append(duplicate_path)
    duplicate_role = copy.deepcopy(fixtures)
    duplicate_role["cm_native_artifacts_v1"]["files"].append(
        copy.deepcopy(duplicate_role["cm_native_artifacts_v1"]["files"][0])
    )
    adversarial_mutations.append(duplicate_role)
    wrong_coordinate = copy.deepcopy(fixtures)
    wrong_coordinate["cm_ensemble_v1"]["candidates"][0]["backend_coordinates"][
        "sample_index"
    ] = 1
    adversarial_mutations.append(wrong_coordinate)
    shared_sidecar = copy.deepcopy(fixtures)
    shared_sidecar["cm_ensemble_v1"]["candidates"][0]["sidecar_paths"] = [
        shared_sidecar["cm_ensemble_v1"]["candidates"][0]["authoritative_structure_path"]
    ]
    adversarial_mutations.append(shared_sidecar)
    wrong_snapshot_candidate = {
        key: copy.deepcopy(fixtures[key])
        for key in ("cm_complex_snapshot_v1", "cm_ensemble_v1")
    }
    for mapping in wrong_snapshot_candidate["cm_complex_snapshot_v1"]["instance_mappings"]:
        mapping["candidate_id"] = "cm_ptx_target-a_11111111111111111111"
    wrong_snapshot_candidate["cm_ensemble_v1"]["source_snapshot_sha256"] = canonical_sha256(
        wrong_snapshot_candidate["cm_complex_snapshot_v1"]
    )
    adversarial_mutations.append(wrong_snapshot_candidate)
    wrong_snapshot_target = {
        key: copy.deepcopy(fixtures[key])
        for key in ("cm_complex_snapshot_v1", "cm_ensemble_v1")
    }
    wrong_snapshot_target["cm_complex_snapshot_v1"]["target_id"] = "different-target"
    for mapping in wrong_snapshot_target["cm_complex_snapshot_v1"]["instance_mappings"]:
        mapping["runtime_target_id"] = "different-target"
    wrong_snapshot_target["cm_ensemble_v1"]["source_snapshot_sha256"] = canonical_sha256(
        wrong_snapshot_target["cm_complex_snapshot_v1"]
    )
    adversarial_mutations.append(wrong_snapshot_target)
    request_snapshot_target_mismatch = {
        key: copy.deepcopy(fixtures[key])
        for key in ("cm_request_v1", "cm_complex_snapshot_v1")
    }
    request_snapshot_target_mismatch["cm_complex_snapshot_v1"]["target_id"] = (
        "different-target"
    )
    for mapping in request_snapshot_target_mismatch["cm_complex_snapshot_v1"][
        "instance_mappings"
    ]:
        mapping["runtime_target_id"] = "different-target"
    adversarial_mutations.append(request_snapshot_target_mismatch)

    for malformed in adversarial_mutations:
        with pytest.raises(ContractValidationError):
            validate_contract_bundle(malformed)


def test_protenix_runtime_attestation_is_native_and_ensemble_bound() -> None:
    fixtures = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")
    validate_contract_bundle(fixtures)

    missing_receipt = copy.deepcopy({
        key: fixtures[key] for key in ("cm_request_v1", "cm_complex_snapshot_v1", "cm_native_artifacts_v1", "cm_ensemble_v1")
    })
    missing_receipt["cm_native_artifacts_v1"]["files"] = [
        item for item in missing_receipt["cm_native_artifacts_v1"]["files"]
        if item["semantic_role"] != "runtime_attestation"
    ]
    with pytest.raises(ContractValidationError, match="runtime attestation"):
        validate_contract_bundle(missing_receipt)

    forged_binding = copy.deepcopy({
        key: fixtures[key] for key in ("cm_request_v1", "cm_complex_snapshot_v1", "cm_native_artifacts_v1", "cm_ensemble_v1")
    })
    forged_binding["cm_ensemble_v1"]["runtime_attestation_sha256"] = "f" * 64
    with pytest.raises(ContractValidationError, match="runtime attestation hash"):
        validate_contract_bundle(forged_binding)


def test_resume_descriptor_rejects_semantically_forged_values() -> None:
    coordinates = [
        {
            "backend": "protenix_v2_ensemble",
            "target_id": "target-a",
            "ordered_seed": seed,
            "sample_index": sample,
        }
        for seed in (101, 202)
        for sample in (0, 1)
    ]
    descriptor = {
        "request_sha256": "a" * 64,
        "source_snapshot_sha256": "b" * 64,
        "complex_snapshot_sha256": "c" * 64,
        "backend": "protenix_v2_ensemble",
        "backend_version": "1",
        "backend_commit": "abc123",
        "runtime_identity": "runtime-1",
        "container_digest": "sha256:" + "d" * 64,
        "model_id": "protenix",
        "checkpoint_sha256": "e" * 64,
        "feature_policy": {"mode": "regenerate_mutated_protein_v1"},
        "feature_policy_sha256": feature_policy_sha256(
            {"mode": "regenerate_mutated_protein_v1"}
        ),
        "ordered_seeds": [101, 202],
        "samples_per_seed": 2,
        "coordinate_plan": coordinates,
        "expected_candidate_cardinality": 4,
        "expected_manifest_schema": "cm_ensemble",
        "expected_manifest_version": 1,
        "required_artifact_roles": [
            "authoritative_cif",
            "confidence_json",
            "full_data_json",
        ],
        "expected_manifest_contract_sha256": "1" * 64,
        "settings_runtime_policy_sha256": "2" * 64,
    }
    ResumeDescriptor.model_validate(descriptor)
    descriptor_with_entity_hashes = copy.deepcopy(descriptor)
    descriptor_with_entity_hashes["feature_policy"]["per_entity_hashes"] = {
        "repeat_prot": "9" * 64
    }
    descriptor_with_entity_hashes["feature_policy_sha256"] = feature_policy_sha256(
        descriptor_with_entity_hashes["feature_policy"]
    )
    assert ResumeDescriptor.model_validate(descriptor_with_entity_hashes).feature_policy == (
        descriptor_with_entity_hashes["feature_policy"]
    )
    explicit_null_hashes = copy.deepcopy(descriptor)
    explicit_null_hashes["feature_policy"]["per_entity_hashes"] = None
    with pytest.raises(ValueError):
        ResumeDescriptor.model_validate(explicit_null_hashes)
    mutations = {
        "duplicate seeds": lambda value: value.update(ordered_seeds=[101, 101]),
        "bad cardinality": lambda value: value.update(expected_candidate_cardinality=999),
        "duplicate roles": lambda value: value.update(
            required_artifact_roles=["authoritative_cif", "authoritative_cif"]
        ),
        "invalid feature mode": lambda value: value["feature_policy"].update(mode="junk"),
        "wrong feature hash": lambda value: value.update(feature_policy_sha256="f" * 64),
    }
    for mutate in mutations.values():
        malformed = copy.deepcopy(descriptor)
        mutate(malformed)
        with pytest.raises(ValidationError):
            ResumeDescriptor.model_validate(malformed)


def test_landscape_slot_semantics_and_nonempty_residues() -> None:
    landscape = copy.deepcopy(
        _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")[
            "cm_frustration_landscape_v1"
        ]
    )
    validate_schema("cm_frustration_landscape_v1", landscape)
    for mutate in (
        lambda slot: slot.update(wt="Y"),
        lambda slot: slot.update(native=False),
        lambda slot: slot.update({"class": "high"}),
        lambda slot: slot.update(scoreable=False),
        lambda slot: slot.update(reason="bogus"),
    ):
        malformed = copy.deepcopy(landscape)
        mutate(malformed["residues"][0]["slots"][0])
        with pytest.raises(ContractValidationError):
            validate_schema("cm_frustration_landscape_v1", malformed)
    empty = copy.deepcopy(landscape)
    empty["residues"] = []
    with pytest.raises(ContractValidationError):
        validate_schema("cm_frustration_landscape_v1", empty)


def test_legacy_v1_landscape_without_container_digest_remains_replayable() -> None:
    fixtures = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")
    assert isinstance(fixtures, dict)
    landscape = copy.deepcopy(fixtures["cm_frustration_landscape_v1"])
    landscape.pop("container_sha256")
    validate_schema("cm_frustration_landscape_v1", landscape)


def test_analysis_count_status_and_handoff_lineage_invariants() -> None:
    fixtures = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")
    analysis = fixtures["cm_analysis_v1"]
    validate_schema("cm_analysis_v1", analysis)
    for change in (
        {"valid_coordinate_count": 3},
        {"status": "robust", "hierarchical_mean": None},
        {"status": "robust", "failure_reason": "not actually robust"},
    ):
        malformed = copy.deepcopy(analysis)
        malformed["results"][0].update(change)
        with pytest.raises(ContractValidationError):
            validate_schema("cm_analysis_v1", malformed)

    handoff = fixtures["cm_mutagenesis_handoff_v1"]
    validate_schema("cm_mutagenesis_handoff_v1", handoff)
    for change in (
        {"substitution": handoff["validated_wt"]},
        {"mutation_set_string": "bogus"},
        {"idempotency_key": "f" * 64},
        {"evidence_row_keys": ["unbound-row"]},
    ):
        malformed = copy.deepcopy(handoff)
        malformed.update(change)
        with pytest.raises(ContractValidationError):
            validate_schema("cm_mutagenesis_handoff_v1", malformed)


def test_complex_bonds_enforce_sequence_bounds_and_known_protein_atoms() -> None:
    case = copy.deepcopy(
        next(
            case
            for case in _json(PHASE_0 / "complex_cases.json")["cases"]
            if case["case_key"] == "P0-VECTOR-COMPLEX-008"
        )
    )
    validate_complex_case(case)
    out_of_bounds = copy.deepcopy(case)
    out_of_bounds["bonds"][0]["left"]["position"] = 9999
    with pytest.raises(ContractValidationError, match="bounds"):
        validate_complex_case(out_of_bounds)
    wrong_atom = copy.deepcopy(case)
    wrong_atom["bonds"][0]["left"]["atom"] = "NZ"
    with pytest.raises(ContractValidationError, match="atom"):
        validate_complex_case(wrong_atom)


def test_structure_map_provenance_status_and_snapshot_binding_are_complete() -> None:
    fixtures = _json(SCHEMA_FIXTURES / "positive" / "all_schemas.json")
    structure_map = fixtures["cm_structure_map_v1"]
    validate_schema("cm_structure_map_v1", structure_map)
    for mutate in (
        lambda value: value.pop("source_bytes"),
        lambda value: value["rows"][0].pop("source_entity_id"),
        lambda value: value["rows"][0].update(reason="mapped but reasoned"),
        lambda value: value["rows"][0].update(status="mapping_failed"),
    ):
        malformed = copy.deepcopy(structure_map)
        mutate(malformed)
        with pytest.raises(ContractValidationError):
            validate_schema("cm_structure_map_v1", malformed)

    wrong_instance = copy.deepcopy(fixtures)
    wrong_instance["cm_structure_map_v1"]["rows"][0]["entity_instance_id"] = "invented"
    with pytest.raises(ContractValidationError, match="authoritative"):
        validate_contract_bundle(wrong_instance)


@pytest.mark.parametrize(
    ("field", "value"),
    [("residue_name", "GLY"), ("label_seq_id", 999999)],
)
def test_structure_map_residue_identity_matches_authoritative_sequence(
    field: str, value: object
) -> None:
    fixtures = copy.deepcopy(_json(SCHEMA_FIXTURES / "positive" / "all_schemas.json"))
    fixtures["cm_structure_map_v1"]["rows"][0][field] = value
    with pytest.raises(ContractValidationError, match="authoritative.*residue|sequence position"):
        validate_structure_map_snapshot_binding(
            fixtures["cm_structure_map_v1"], fixtures["cm_complex_snapshot_v1"]
        )


def test_manifest_paths_are_canonical_and_duplicate_multiplicity_is_preserved() -> None:
    for alias in ("a/./b", "a//b", "a/b/", "./a/b", "a/../b"):
        with pytest.raises(ContractValidationError, match="unsafe"):
            validate_manifest_set_equality([alias], observed=[alias], referenced=[alias])
    with pytest.raises(ContractValidationError, match="duplicate"):
        validate_manifest_set_equality(
            ["a/file.cif", "a/file.cif"],
            observed=["a/file.cif", "a/file.cif"],
            referenced=["a/file.cif", "a/file.cif"],
        )


PHASE0_REGISTRY = _json(
    REPO_ROOT / "docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json"
)


@pytest.mark.parametrize("vector", PHASE0_REGISTRY["vectors"], ids=lambda vector: vector["id"])
def test_phase0_applicable_vectors_have_executable_contract_dispositions(
    vector: dict[str, object],
) -> None:
    for fixture_ref in vector["fixtures"]:
        fixture_path = REPO_ROOT / fixture_ref["path"]
        assert fixture_path.is_file(), fixture_ref["path"]
        assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == fixture_ref["sha256"]
    observed = validate_phase0_contract_vector(vector, repo_root=REPO_ROOT)
    if observed is None:
        assert vector["family"] == "baseline"
    else:
        assert observed == vector["expected_disposition"]
