from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import rfc8785
from jsonschema import Draft202012Validator

from services.frustrampnn import analytics
from services.frustrampnn.configuration import execution_configuration
from services.frustrampnn.contracts import ContractValidationError, canonical_sha256
from services.frustrampnn.settings import (
    FrustraMPNNClassificationPolicy,
    FrustraMPNNRequestedSettings,
    FrustraMPNNResolutionIdentity,
    FrustraMPNNResolvedChainSelection,
    _build_effective_settings,
)

AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"
API_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    API_ROOT.parent.parent
    / "schemas/frustrampnn/frustrampnn_statistics_v1.schema.json"
)
CAPABILITY_PATH = (
    API_ROOT / "config/models/frustrampnn_capability_inventory_v1.json"
)


def _identity(
    *,
    entity: str,
    source_entity: str,
    label_asym: str,
    auth_chain: str,
    auth_seq: int,
    sequence_index: int,
    pdb_chain: str,
    model_position: int,
) -> dict[str, object]:
    return {
        "entity_instance_id": entity,
        "source_entity_id": source_entity,
        "label_asym_id": label_asym,
        "auth_asym_id": auth_chain,
        "auth_seq_id": auth_seq,
        "insertion_code": "",
        "sequence_index": sequence_index,
        "wt": "G",
        "pdb_chain_id": pdb_chain,
        "model_position": model_position,
    }


def _structure_map(
    residues: list[dict[str, object]], *, include_exclusions: bool = False
) -> dict[str, object]:
    rows = []
    for residue in residues:
        rows.append(
            {
                **residue,
                "label_seq_id": residue["sequence_index"],
                "pdb_residue_id": residue["model_position"] + 1,
                "pdb_insertion_code": "",
                "residue_name": "GLY",
                "selected_model": 1,
                "selected_altloc": "",
                "backbone_complete": True,
                "backbone_atoms": {
                    "N": "N",
                    "CA": "CA",
                    "C": "C",
                    "O": "O",
                },
                "status": "mapped",
                "reason": None,
            }
        )
    excluded_records: list[dict[str, object]] = []
    if include_exclusions:
        rows.insert(
            2,
            {
                **_identity(
                    entity="entity-1",
                    source_entity="1",
                    label_asym="AA",
                    auth_chain="X",
                    auth_seq=12,
                    sequence_index=3,
                    pdb_chain="A",
                    model_position=2,
                ),
                "label_seq_id": 3,
                "pdb_residue_id": 999,
                "pdb_insertion_code": "",
                "residue_name": "GLY",
                "selected_model": 1,
                "selected_altloc": "",
                "backbone_complete": False,
                "backbone_atoms": {"N": "N", "CA": "CA", "C": "C", "O": None},
                "status": "missing_backbone",
                "reason": "missing backbone atom O",
            },
        )
        excluded_records.append(
            {
                "source_identity": "water:A:900",
                "reason_code": "non_protein_entity",
                "reason": "water is not a protein residue",
            }
        )
    sequence = "".join(str(row["wt"]) for row in rows if row["status"] == "mapped")
    return {
        "schema_name": "frustrampnn_structure_map",
        "schema_version": 1,
        "target_id": "target-statistics",
        "parent_job_id": "job-statistics",
        "candidate_id": "candidate-statistics",
        "source_format": "mmcif",
        "source_sha256": "1" * 64,
        "source_bytes": 100,
        "identity_authority": "mmcif_atom_site_v1",
        "identity_domain": "source_authoritative",
        "authority_artifact_sha256": "1" * 64,
        "normalized_pdb_sha256": "3" * 64,
        "selected_source_model": 1,
        "altloc_policy": "blank_or_explicit:<blank>",
        "normalizer_version": "frustrampnn_structure_normalizer_v1",
        "model_ready_sequence": sequence,
        "model_ready_sequence_sha256": hashlib.sha256(
            sequence.encode("ascii")
        ).hexdigest(),
        "excluded_records": excluded_records,
        "rows": rows,
    }


def _fixture(*, include_exclusions: bool = False) -> dict[str, object]:
    residues = [
        _identity(
            entity="entity-1",
            source_entity="1",
            label_asym="AA",
            auth_chain="X",
            auth_seq=10,
            sequence_index=1,
            pdb_chain="A",
            model_position=0,
        ),
        _identity(
            entity="entity-1",
            source_entity="1",
            label_asym="AA",
            auth_chain="X",
            auth_seq=11,
            sequence_index=2,
            pdb_chain="A",
            model_position=1,
        ),
        _identity(
            entity="entity-1",
            source_entity="1",
            label_asym="AA",
            auth_chain="X",
            auth_seq=20,
            sequence_index=4,
            pdb_chain="A",
            model_position=2,
        ),
        _identity(
            entity="entity-2",
            source_entity="2",
            label_asym="BB",
            auth_chain="Y",
            auth_seq=1,
            sequence_index=1,
            pdb_chain="B",
            model_position=0,
        ),
    ]
    structure_map = _structure_map(residues, include_exclusions=include_exclusions)
    map_sha256 = canonical_sha256(structure_map)
    requested = FrustraMPNNRequestedSettings(
        classification_policy=FrustraMPNNClassificationPolicy(
            mode="custom", high_max=-0.5, minimal_min=0.5
        )
    )
    chains = []
    for entity, source_entity, label_asym, auth_chain, pdb_chain in (
        ("entity-1", "1", "AA", "X", "A"),
        ("entity-2", "2", "BB", "Y", "B"),
    ):
        chain_residues = [
            row for row in residues if row["entity_instance_id"] == entity
        ]
        chains.append(
            FrustraMPNNResolvedChainSelection.model_validate(
                {
                    "entity": {
                        "entity_instance_id": entity,
                        "source_entity_id": source_entity,
                        "label_asym_id": label_asym,
                        "auth_asym_id": auth_chain,
                    },
                    "pdb_chain_id": pdb_chain,
                    "residues": chain_residues,
                }
            )
        )
    effective = _build_effective_settings(
        requested,
        resolved_chains=tuple(chains),
        resolution_identity=FrustraMPNNResolutionIdentity(
            source_artifact_sha256="1" * 64,
            structure_map_sha256=map_sha256,
            normalized_pdb_sha256="3" * 64,
        ),
    )
    configuration = execution_configuration(effective)
    native_scores = (-1.0, -1.0, 0.0, 1.0)
    landscape_residues = []
    for residue, native_score in zip(residues, native_scores, strict=True):
        slots = []
        for mutation_aa in AA_ORDER:
            score = (
                native_score
                if mutation_aa == "G"
                else 2.0
                if mutation_aa == "A"
                else -2.0
                if mutation_aa == "C"
                else 0.0
            )
            score_class = (
                "high" if score <= -0.5 else "minimal" if score >= 0.5 else "neutral"
            )
            slots.append(
                {
                    "mutation_aa": mutation_aa,
                    "score": score,
                    "class": score_class,
                    "scoreable": True,
                    "status": "ok",
                    "reason": None,
                    "native": mutation_aa == "G",
                }
            )
        landscape_residues.append({**residue, "slots": slots})
    landscape = {
        "schema_name": "frustrampnn_landscape",
        "schema_version": 2,
        "execution_configuration_id": configuration.configuration_id,
        "execution_configuration_sha256": configuration.configuration_sha256,
        "requested_settings_sha256": effective.settings_sha256,
        "effective_settings_sha256": effective.effective_settings_sha256,
        "runtime_identity_sha256": configuration.runtime_identity_sha256,
        "target_id": "target-statistics",
        "parent_job_id": "job-statistics",
        "candidate_id": "candidate-statistics",
        "source_artifact_sha256": "1" * 64,
        "structure_map_sha256": map_sha256,
        "normalized_pdb_sha256": "3" * 64,
        "raw_csv_sha256": "4" * 64,
        "threshold_policy_id": effective.threshold_policy_id,
        "threshold_policy": requested.classification_policy.model_dump(mode="json"),
        "threshold_policy_sha256": effective.threshold_policy_sha256,
        "residues": landscape_residues,
    }
    landscape_sha256 = canonical_sha256(landscape)
    plan_entries = [
        {
            "ordinal": 0,
            "chains": None,
            "positions": None,
            "shard_relative_path": "raw_frustrampnn_shard_0000.csv",
        }
    ]
    plan = {
        "entries": plan_entries,
        "plan_sha256": canonical_sha256({"entries": plan_entries}),
    }
    argv = [
        "apptainer",
        "exec",
        "image.sif",
        "frustrampnn",
        "predict",
        "--pdb",
        "normalized_input.pdb",
        "--output",
        "raw_frustrampnn_shard_0000.csv",
    ]
    execution_receipt = {
        "schema_name": "frustrampnn_execution_receipt",
        "schema_version": 2,
        "invocation_id": "invoke-statistics",
        "execution_configuration_sha256": configuration.configuration_sha256,
        "requested_settings_sha256": effective.settings_sha256,
        "effective_settings_sha256": effective.effective_settings_sha256,
        "runtime_identity_sha256": configuration.runtime_identity_sha256,
        "source_artifact_sha256": "1" * 64,
        "structure_map_sha256": map_sha256,
        "normalized_pdb_sha256": "3" * 64,
        "command_plan": plan,
        "command_count": 1,
        "commands": [
            {
                **plan_entries[0],
                "argv": argv,
                "argv_sha256": canonical_sha256(argv),
                "status": "succeeded",
                "exit_code": 0,
                "shard_sha256": "4" * 64,
                "shard_row_count": 80,
                "started_at": "2026-08-08T20:00:00Z",
                "ended_at": "2026-08-08T20:00:01Z",
                "duration_seconds": 1.0,
            }
        ],
        "merged_raw_csv_sha256": "4" * 64,
        "landscape_sha256": landscape_sha256,
        "summary_sha256": "5" * 64,
        "assigned_physical_gpu_id": "0",
        "task_visible_device_index": 0,
        "stdout_artifact": "frustrampnn_stdout.log",
        "stderr_artifact": "frustrampnn_stderr.log",
        "started_at": "2026-08-08T20:00:00Z",
        "ended_at": "2026-08-08T20:00:01Z",
        "duration_seconds": 1.0,
    }
    request = {
        "schema_name": "workflow_component_request",
        "schema_version": 2,
        "component_id": "frustrampnn",
        "component_contract_version": "2.0",
        "invocation_id": "invoke-statistics",
        "parent_job_id": "job-statistics",
        "parent_workflow_id": "structure_prediction",
        "candidate_id": "candidate-statistics",
        "source_artifact": {
            "relative_path": "inputs/candidate.cif",
            "sha256": "1" * 64,
            "media_type": "chemical/x-mmcif",
            "producer_stage": "prediction",
            "artifact_id": None,
        },
        "requiredness": "required",
        "identity_authority": "mmcif_atom_site",
        "settings_value_origin": requested.settings_value_origin,
        "requested_settings": requested.model_dump(mode="json"),
        "requested_settings_sha256": effective.settings_sha256,
        "effective_settings": effective.model_dump(mode="json"),
        "effective_settings_sha256": effective.effective_settings_sha256,
        "classification_policy_sha256": effective.threshold_policy_sha256,
        "capability_inventory_byte_sha256": effective.capability_inventory_byte_sha256,
        "runtime_identity_sha256": configuration.runtime_identity_sha256,
        "structure_map_sha256": map_sha256,
        "normalized_pdb_sha256": "3" * 64,
        "execution_configuration": configuration.model_dump(mode="json"),
        "execution_configuration_sha256": configuration.configuration_sha256,
        "requested_outputs": [
            "structure_map",
            "raw_csv",
            "landscape",
            "summary",
            "execution_receipt",
        ],
    }
    capability_bytes = CAPABILITY_PATH.read_bytes()
    capability_inventory = json.loads(capability_bytes)
    return {
        "request": request,
        "execution_receipt": execution_receipt,
        "landscape": landscape,
        "structure_map": structure_map,
        "capability_inventory": capability_inventory,
        "capability_inventory_bytes": capability_bytes,
    }


def _build(fixture: dict[str, object] | None = None) -> dict[str, object]:
    data = fixture or _fixture()
    return analytics.build_statistics_receipt(
        request=data["request"],
        execution_receipt=data["execution_receipt"],
        landscape=data["landscape"],
        structure_map=data["structure_map"],
        capability_inventory=data["capability_inventory"],
        capability_inventory_bytes=data["capability_inventory_bytes"],
    )


def _assert_closed_objects(node: object, path: str = "$") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, path
        for key, value in node.items():
            _assert_closed_objects(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _assert_closed_objects(value, f"{path}/{index}")


def test_distribution_uses_type7_quartiles_and_sample_sd_for_odd_even_and_small_counts() -> None:
    empty = analytics.statistics_distribution([])
    assert {key: empty[key] for key in (
        "count", "mean", "median", "sample_sd", "min", "max", "q1", "q3", "iqr"
    )} == {
        "count": 0,
        "mean": None,
        "median": None,
        "sample_sd": None,
        "min": None,
        "max": None,
        "q1": None,
        "q3": None,
        "iqr": None,
    }
    singleton = analytics.statistics_distribution([7.0])
    assert {key: singleton[key] for key in (
        "count", "mean", "median", "sample_sd", "min", "max", "q1", "q3", "iqr"
    )} == {
        "count": 1,
        "mean": 7.0,
        "median": 7.0,
        "sample_sd": None,
        "min": 7.0,
        "max": 7.0,
        "q1": 7.0,
        "q3": 7.0,
        "iqr": 0.0,
    }
    assert singleton["denominators"]["sample_sd"] == {
        "kind": "sample_degrees_of_freedom_n_minus_1",
        "count": 0,
    }
    assert singleton["missingness_reasons"]["sample_sd"] == "insufficient_support_n_lt_2"
    even = analytics.statistics_distribution([3.0, 0.0, 2.0, 1.0])
    assert even["sample_sd"] == pytest.approx(1.2909944487358056)
    assert even["denominators"]["sample_sd"]["count"] == 3
    assert even["missingness_reasons"]["sample_sd"] is None
    odd = analytics.statistics_distribution([4.0, 1.0, 3.0, 2.0, 0.0])
    assert odd["median"] == 2.0
    assert odd["q1"] == 1.0
    assert odd["q3"] == 3.0
    assert odd["sample_sd"] == pytest.approx(1.5811388300841898)


def test_distribution_metadata_names_every_scalar_denominator_and_missingness_reason() -> None:
    distribution = analytics.statistics_distribution([1.0, 2.0, 3.0])
    scalar_names = {"count", "mean", "median", "sample_sd", "min", "max", "q1", "q3", "iqr"}
    assert set(distribution["denominators"]) == scalar_names
    assert set(distribution["missingness_reasons"]) == scalar_names
    assert all(distribution["missingness_reasons"][name] is None for name in scalar_names)


def test_statistics_receipt_is_closed_schema_valid_and_rfc8785_self_hashed() -> None:
    receipt = _build()
    assert receipt["schema_name"] == "frustrampnn_statistics"
    assert receipt["schema_version"] == 1
    unhashed = {key: value for key, value in receipt.items() if key != "statistics_sha256"}
    assert receipt["statistics_sha256"] == hashlib.sha256(
        rfc8785.dumps(unhashed)
    ).hexdigest()
    analytics.validate_statistics_receipt(receipt)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    _assert_closed_objects(schema)
    forbidden_keys = {"path", "relative_path", "timestamp", "created_at", "updated_at"}

    def assert_no_ephemeral_keys(value) -> None:
        if isinstance(value, dict):
            assert not (set(value) & forbidden_keys)
            for child in value.values():
                assert_no_ephemeral_keys(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_ephemeral_keys(child)

    assert_no_ephemeral_keys(receipt)


def test_statistics_receipt_is_deterministic_for_input_mapping_and_numeric_row_permutations() -> None:
    first = _fixture()
    second = copy.deepcopy(first)
    second["request"] = dict(reversed(list(second["request"].items())))
    second["landscape"] = dict(reversed(list(second["landscape"].items())))
    assert _build(first) == _build(second)
    values = [2.0, -2.0, 0.0, 2.0, -2.0]
    assert analytics.statistics_distribution(values) == analytics.statistics_distribution(
        list(reversed(values))
    )


def test_support_distributions_native_alternative_math_and_grouping_are_exact() -> None:
    receipt = _build()
    assert receipt["support"] == {
        "selected_residue_count": 4,
        "source_residue_count": 4,
        "observed_residue_count": 4,
        "scoreable_residue_count": 4,
        "excluded_residue_count": 0,
        "missing_residue_count": 0,
        "mapping_missing_residue_count": 0,
        "selected_missing_residue_count": 0,
        "fully_scoreable_residue_count": 4,
        "partially_scoreable_residue_count": 0,
        "expected_slot_count": 80,
        "observed_slot_count": 80,
        "scoreable_slot_count": 80,
        "excluded_slot_count": 0,
        "mapping_missing_slot_count": 0,
        "missing_slot_count": 0,
        "residue_fractions": {
            "selected": {"value": 1.0, "denominator": {"kind": "structure_map_source_residues", "count": 4}, "missingness_reason": None},
            "observed": {"value": 1.0, "denominator": {"kind": "structure_map_source_residues", "count": 4}, "missingness_reason": None},
            "scoreable": {"value": 1.0, "denominator": {"kind": "structure_map_source_residues", "count": 4}, "missingness_reason": None},
            "excluded": {"value": 0.0, "denominator": {"kind": "structure_map_source_residues", "count": 4}, "missingness_reason": None},
            "missing": {"value": 0.0, "denominator": {"kind": "structure_map_source_residues", "count": 4}, "missingness_reason": None},
            "selected_missing": {"value": 0.0, "denominator": {"kind": "effective_selected_residues", "count": 4}, "missingness_reason": None},
        },
        "slot_fractions": {
            "observed": {"value": 1.0, "denominator": {"kind": "expected_selected_slots", "count": 80}, "missingness_reason": None},
            "scoreable": {"value": 1.0, "denominator": {"kind": "expected_selected_slots", "count": 80}, "missingness_reason": None},
            "excluded": {"value": 0.0, "denominator": {"kind": "source_residue_slots", "count": 80}, "missingness_reason": None},
            "missing": {"value": 0.0, "denominator": {"kind": "source_residue_slots", "count": 80}, "missingness_reason": None},
            "selected_missing": {"value": 0.0, "denominator": {"kind": "expected_selected_slots", "count": 80}, "missingness_reason": None},
        },
        "exclusion_reasons": [],
        "missing_reasons": [],
    }
    assert receipt["distributions"]["overall"]["count"] == 80
    assert receipt["distributions"]["native"]["mean"] == -0.25
    assert receipt["distributions"]["non_native"]["count"] == 76
    assert receipt["distributions"]["non_native"]["mean"] == 0.0
    comparison = receipt["native_vs_alternative"]
    assert comparison["native_mean"] == -0.25
    assert comparison["alternative_mean"] == 0.0
    assert comparison["alternative_minus_native"]["count"] == 76
    assert comparison["alternative_minus_native"]["mean"] == 0.25
    assert [row["auth_asym_id"] for row in receipt["per_chain"]] == ["X", "Y"]
    assert [row["entity_instance_id"] for row in receipt["per_entity"]] == [
        "entity-1",
        "entity-2",
    ]
    assert receipt["per_chain"][0]["support"]["selected_residue_count"] == 3
    assert receipt["per_chain"][0]["non_native"]["count"] == 57
    assert receipt["per_entity"][1]["native"]["count"] == 1


def test_residue_mutation_regions_rankings_ties_and_class_burdens_are_exact() -> None:
    receipt = _build()
    per_residue = receipt["per_residue"]
    assert [row["native_class"] for row in per_residue] == [
        "high",
        "high",
        "neutral",
        "minimal",
    ]
    assert all(row["all"]["count"] == 20 for row in per_residue)
    assert all(row["non_native"]["count"] == 19 for row in per_residue)
    assert per_residue[0]["alternative_class_burden"]["counts"] == {
        "high": 1,
        "neutral": 17,
        "minimal": 1,
    }
    per_aa = receipt["per_mutation_amino_acid"]
    assert [row["mutation_aa"] for row in per_aa] == list(AA_ORDER)
    assert per_aa[AA_ORDER.index("G")]["distribution"]["count"] == 0
    assert per_aa[AA_ORDER.index("G")]["class_composition"]["support_count"] == 0
    assert per_aa[AA_ORDER.index("A")]["class_composition"]["counts"] == {
        "high": 0,
        "neutral": 0,
        "minimal": 4,
    }
    regions = receipt["contiguous_native_class_regions"]
    assert [(row["auth_asym_id"], row["native_class"], row["length"]) for row in regions] == [
        ("X", "high", 2),
        ("X", "neutral", 1),
        ("Y", "minimal", 1),
    ]
    best = receipt["ranked_non_native_alternatives"]["best_to_worst"]
    worst = receipt["ranked_non_native_alternatives"]["worst_to_best"]
    assert [(row["mutation_aa"], row["sequence_index"]) for row in best[:4]] == [
        ("A", 1),
        ("A", 2),
        ("A", 4),
        ("A", 1),
    ]
    assert [(row["mutation_aa"], row["sequence_index"]) for row in worst[:4]] == [
        ("C", 1),
        ("C", 2),
        ("C", 4),
        ("C", 1),
    ]
    burden = receipt["class_burden"]
    assert burden["all"]["counts"] == {"high": 6, "neutral": 69, "minimal": 5}
    assert burden["native"]["counts"] == {"high": 2, "neutral": 1, "minimal": 1}
    assert burden["non_native"]["counts"] == {
        "high": 4,
        "neutral": 68,
        "minimal": 4,
    }
    assert burden["all"]["fractions"]["neutral"] == 69 / 80


def test_every_distribution_and_class_composition_names_its_denominator_and_missingness() -> None:
    receipt = _build()
    distributions = list(receipt["distributions"].values())
    distributions.extend(
        distribution
        for row in receipt["per_residue"]
        for distribution in (row["all"], row["non_native"])
    )
    distributions.extend(row["distribution"] for row in receipt["per_mutation_amino_acid"])
    distributions.extend(
        distribution
        for level in ("per_chain", "per_entity")
        for row in receipt[level]
        for distribution in (row["all"], row["native"], row["non_native"])
    )
    distributions.append(receipt["native_vs_alternative"]["alternative_minus_native"])
    scalar_names = {"count", "mean", "median", "sample_sd", "min", "max", "q1", "q3", "iqr"}
    assert distributions
    assert all(set(item["denominators"]) == scalar_names for item in distributions)
    assert all(set(item["missingness_reasons"]) == scalar_names for item in distributions)

    burdens = list(receipt["class_burden"].values())
    burdens.extend(row["alternative_class_burden"] for row in receipt["per_residue"])
    burdens.extend(row["class_composition"] for row in receipt["per_mutation_amino_acid"])
    assert all(set(item["denominator"]) == {"kind", "count"} for item in burdens)
    assert all("missingness_reason" in item for item in burdens)
    assert set(receipt["native_vs_alternative"]["denominators"]) == {
        "native_mean",
        "alternative_mean",
    }
    assert set(receipt["native_vs_alternative"]["missingness_reasons"]) == {
        "native_mean",
        "alternative_mean",
    }


def test_excluded_and_missing_structure_map_authority_is_counted_with_exact_reasons() -> None:
    receipt = _build(_fixture(include_exclusions=True))
    support = receipt["support"]
    assert support["source_residue_count"] == 6
    assert support["selected_residue_count"] == 4
    assert support["observed_residue_count"] == 4
    assert support["scoreable_residue_count"] == 4
    assert support["excluded_residue_count"] == 1
    assert support["missing_residue_count"] == 1
    assert support["mapping_missing_residue_count"] == 1
    assert support["selected_missing_residue_count"] == 0
    assert support["excluded_slot_count"] == 20
    assert support["mapping_missing_slot_count"] == 20
    assert support["missing_slot_count"] == 0
    assert support["residue_fractions"]["excluded"]["value"] == pytest.approx(1 / 6)
    assert support["residue_fractions"]["missing"]["value"] == pytest.approx(1 / 6)
    assert support["exclusion_reasons"] == [
        {
            "authority": "excluded_record",
            "status": "excluded",
            "reason_code": "non_protein_entity",
            "reason": "water is not a protein residue",
            "count": 1,
        },
    ]
    assert support["missing_reasons"] == [
        {
            "authority": "structure_map_row",
            "status": "missing_backbone",
            "reason_code": "missing_backbone",
            "reason": "missing backbone atom O",
            "count": 1,
        },
    ]
    assert receipt["distributions"]["overall"]["denominators"]["count"] == {
        "kind": "selected_substitution_slots",
        "count": 80,
    }
    assert receipt["ranked_non_native_alternatives"]["omitted_count"] == 38


def test_comparison_basis_is_closed_to_raw_semantics_and_separate_classification_policy() -> None:
    receipt = _build()
    basis = receipt["comparison_compatibility_basis"]
    assert receipt["comparison_compatibility_id"] == analytics.comparison_compatibility_id(
        basis
    )
    assert set(basis) == {
        "schema_name",
        "schema_version",
        "raw_score_semantics",
        "classification_policy",
    }
    assert basis["schema_version"] == 2
    raw = basis["raw_score_semantics"]
    assert raw["canonical_amino_acid_order"] == AA_ORDER
    assert set(raw) == {
        "model",
        "tool",
        "capability",
        "output_schema",
        "canonical_amino_acid_order",
        "normalization",
    }
    serialized = json.dumps(basis, sort_keys=True)
    for forbidden in (
        "parent_job_id",
        "candidate_id",
        "invocation_id",
        "source_artifact_sha256",
        "normalized_pdb_sha256",
        "structure_map_sha256",
        "configuration_sha256",
        "selection_identity",
        "requested_settings",
        "effective_settings",
    ):
        assert forbidden not in serialized

    raw_mutations = [
        lambda value: value["model"].update({"checkpoint_sha256": "0" * 64}),
        lambda value: value["tool"].update({"tool_version": "other"}),
        lambda value: value["capability"].update({"content_sha256": "0" * 64}),
        lambda value: value["output_schema"].update({"landscape_schema_version": 3}),
        lambda value: value["normalization"].update({"normalizer_version": "other"}),
    ]
    for mutate in raw_mutations:
        changed = copy.deepcopy(basis)
        mutate(changed["raw_score_semantics"])
        assert analytics.comparison_compatibility_id(changed) != receipt[
            "comparison_compatibility_id"
        ]

    policy_changed = copy.deepcopy(basis)
    policy_changed["classification_policy"]["policy_sha256"] = "0" * 64
    assert analytics.comparison_compatibility_id(policy_changed) != receipt[
        "comparison_compatibility_id"
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data["landscape"]["residues"][0]["slots"].pop(),
            "20|slot|landscape",
        ),
        (
            lambda data: data["landscape"]["residues"][0]["slots"][1].update(
                {"mutation_aa": "A"}
            ),
            "canonical|unique|order",
        ),
        (
            lambda data: data["landscape"]["residues"][0]["slots"][0].update(
                {"mutation_aa": "B"}
            ),
            "landscape|pattern|canonical",
        ),
        (
            lambda data: data["landscape"]["residues"][0]["slots"][0].update(
                {"score": float("nan")}
            ),
            "finite|canonical",
        ),
        (
            lambda data: data["landscape"]["residues"].reverse(),
            "canonical order",
        ),
        (
            lambda data: data["landscape"]["residues"][0].update(
                {"auth_seq_id": 999}
            ),
            "identity|binding|landscape",
        ),
        (
            lambda data: data["request"].update({"identity_authority": "pdb_coordinates"}),
            "identity authority|identity.*binding",
        ),
        (
            lambda data: data["request"].pop("effective_settings"),
            "effective_settings|required|rejected",
        ),
        (
            lambda data: data["capability_inventory"].update(
                {"content_sha256": "0" * 64}
            ),
            "capability.*content",
        ),
    ],
)
def test_builder_rejects_incomplete_malformed_nonfinite_noncanonical_or_mismatched_authority(
    mutation, message: str
) -> None:
    fixture = _fixture()
    mutation(fixture)
    with pytest.raises(ContractValidationError, match=message):
        _build(fixture)


def test_builder_rejects_capability_byte_mismatch_and_statistics_hash_tampering() -> None:
    fixture = _fixture()
    fixture["capability_inventory_bytes"] += b"\n"
    with pytest.raises(ContractValidationError, match="capability.*byte"):
        _build(fixture)

    receipt = _build()
    receipt["support"]["selected_residue_count"] = 5
    with pytest.raises(ContractValidationError, match="statistics SHA-256"):
        analytics.validate_statistics_receipt(receipt)
