from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import copy
import json

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import (
    Base,
    FrustraMPNNComparison,
    FrustraMPNNGuidancePlan,
    FrustraMPNNLandscapeRow,
    FrustraMPNNResult,
    Job,
    get_session,
)
from routers.frustrampnn import MultiComparisonCreateRequest, router
import routers.frustrampnn as frustrampnn_router
from services.frustrampnn.analytics import comparison_compatibility_id
from services.frustrampnn.configuration import global_configuration
from services.frustrampnn.persistence import _FRUSTRA_LANDSCAPE_PARQUET_SCHEMA
from services.frustrampnn.settings import default_settings, requested_settings_sha256
from services.scientific_artifacts import publish_table_rows


def _compatibility_basis(
    configuration_sha256: str,
    *,
    checkpoint_sha256: str = "a" * 64,
    policy_sha256: str = "5" * 64,
    high_max: float = -1.0,
    minimal_min: float = 0.58,
) -> dict:
    del configuration_sha256  # execution identity is deliberately outside raw semantics
    return {
        "schema_name": "frustrampnn_comparison_compatibility_basis",
        "schema_version": 2,
        "raw_score_semantics": {
            "model": {
                "checkpoint_id": "megascale.ckpt",
                "checkpoint_sha256": checkpoint_sha256,
            },
            "tool": {"tool_id": "frustrampnn", "tool_version": "MegaScale"},
            "capability": {
                "schema_name": "frustrampnn_capability_inventory",
                "schema_version": 1,
                "content_sha256": "b" * 64,
            },
            "output_schema": {
                "component_id": "frustrampnn",
                "component_contract_version": "2.0",
                "landscape_schema_name": "frustrampnn_landscape",
                "landscape_schema_version": 2,
                "score_field": "score",
            },
            "canonical_amino_acid_order": "ACDEFGHIKLMNPQRSTVWY",
            "normalization": {
                "normalizer_version": "frustrampnn_structure_normalizer_v1",
                "identity_authority": "mmcif_atom_site_v1",
                "identity_domain": "source_authoritative",
                "selected_source_model": 1,
                "altloc_policy": "blank_or_explicit:<blank>",
                "normalization_policy_id": "frustrampnn_structure_normalizer",
                "normalization_policy_version": 1,
            },
        },
        "classification_policy": {
            "policy_id": "frustrampnn_class_v1",
            "policy_sha256": policy_sha256,
            "policy": {
                "mode": "canonical" if (high_max, minimal_min) == (-1.0, 0.58) else "custom",
                "high_max": high_max,
                "minimal_min": minimal_min,
            },
        },
    }


def _landscape(
    *,
    scores: dict[tuple[int, str], float | None] | None = None,
    config_hash: str | None = None,
    compatibility_id: str | None = None,
    compatibility_basis: dict | None = None,
):
    from services.frustrampnn.analysis import score_class

    scores = scores or {}
    residues = []
    for sequence_index, wt in ((1, "A"), (2, "G")):
        slots = []
        for mutation in "AC":
            score = scores.get((sequence_index, mutation), 0.0)
            slots.append({
                "mutation_aa": mutation,
                "score": score,
                "class": score_class(score) if score is not None else None,
                "scoreable": score is not None,
                "status": "ok" if score is not None else "missing",
                "reason": None if score is not None else "provider_missing",
                "native": mutation == wt,
            })
        residues.append({
            "entity_instance_id": "pdb:A",
            "source_entity_id": None,
            "label_asym_id": None,
            "auth_asym_id": "A",
            "label_seq_id": None,
            "auth_seq_id": sequence_index,
            "insertion_code": "",
            "sequence_index": sequence_index,
            "pdb_chain_id": "A",
            "pdb_residue_id": sequence_index,
            "pdb_insertion_code": "",
            "model_position": sequence_index - 1,
            "residue_name": "ALA" if wt == "A" else "GLY",
            "wt": wt,
            "slots": slots,
        })
    config = global_configuration()
    resolved_config_hash = config_hash or config["configuration_sha256"]
    basis = compatibility_basis or _compatibility_basis(resolved_config_hash)
    return {
        "schema_name": "frustrampnn_landscape",
        "schema_version": 1,
        "configuration_id": "frustrampnn_global_v1",
        "configuration_sha256": resolved_config_hash,
        "comparison_compatibility_basis": basis,
        "comparison_compatibility_id": (
            compatibility_id or comparison_compatibility_id(basis)
        ),
        "target_id": "target-1",
        "parent_job_id": "job-1",
        "candidate_id": "candidate-1",
        "structure_map_sha256": "1" * 64,
        "normalized_pdb_sha256": "2" * 64,
        "model_ready_sequence_sha256": "3" * 64,
        "raw_csv_sha256": "4" * 64,
        "threshold_policy": {"id": "frustrampnn_class_v1", "high_max": -1.0, "minimal_min": 0.58},
        "threshold_policy_sha256": "5" * 64,
        "residues": residues,
    }


def test_comparison_joins_residue_identity_and_separates_delta_transition_and_missingness():
    from services.frustrampnn.comparison import compare_landscapes

    reference = _landscape(scores={(1, "A"): 0.0, (1, "C"): -2.0})
    target = _landscape(scores={(1, "A"): 0.7, (1, "C"): None})
    result = compare_landscapes(reference, target)
    from services.frustrampnn.contracts import validate_schema
    validate_schema("frustrampnn_comparison_v1", result)

    assert result["comparability"]["status"] == "comparable"
    rows = {(row["sequence_index"], row["mutation_aa"]): row for row in result["rows"]}
    assert rows[(1, "A")]["raw_score_delta"] == 0.7
    assert rows[(1, "A")]["classification_transition"] == "neutral_to_minimal"
    assert rows[(1, "C")]["raw_score_delta"] is None
    assert rows[(1, "C")]["missingness_state"] == "target_missing"
    assert result["summary"]["missing_target"] == 1


def test_source_and_configuration_hashes_do_not_block_raw_comparison():
    from services.frustrampnn.comparison import compare_landscapes

    reference = _landscape(scores={(1, "A"): 0.0})
    target = _landscape(scores={(1, "A"): 0.7}, config_hash="f" * 64)
    target["source_artifact_sha256"] = "e" * 64
    target["structure_map_sha256"] = "d" * 64
    target["normalized_pdb_sha256"] = "c" * 64

    result = compare_landscapes(reference, target)

    assert result["compatibility_domains"]["raw_score"]["status"] == "compatible"
    assert result["compatibility_domains"]["classification"]["status"] == "compatible"
    row = next(
        row
        for row in result["rows"]
        if row["sequence_index"] == 1 and row["mutation_aa"] == "A"
    )
    assert row["raw_score_delta"] == 0.7


def test_checkpoint_mismatch_is_hard_incompatible_and_override_never_keeps_deltas():
    from services.frustrampnn.comparison import (
        ComparisonCompatibilityError,
        compare_landscapes,
        comparison_compatibility,
    )

    reference = _landscape(scores={(1, "A"): 0.0})
    target_basis = _compatibility_basis(
        "f" * 64,
        checkpoint_sha256="f" * 64,
    )
    target = _landscape(
        scores={(1, "A"): 0.7},
        config_hash="f" * 64,
        compatibility_basis=target_basis,
    )

    with pytest.raises(ComparisonCompatibilityError) as rejected:
        compare_landscapes(reference, target)
    assert rejected.value.metadata["compatibility_status"] == "incompatible"

    metadata = comparison_compatibility(reference, target, allow_incompatible=True)
    assert metadata["left_comparison_compatibility_id"] == reference[
        "comparison_compatibility_id"
    ]
    assert metadata["right_comparison_compatibility_id"] == target[
        "comparison_compatibility_id"
    ]
    assert metadata["override_used"] is True
    assert metadata["compatibility_domains"]["raw_score"]["status"] == (
        "hard_incompatible"
    )
    assert [item["field_path"] for item in metadata["compatibility_differences"]] == [
        "raw_score_semantics.model.checkpoint_sha256"
    ]

    result = compare_landscapes(reference, target, allow_incompatible=True)
    assert result["comparability"]["status"] == "incompatible"
    assert "compatibility_override_used" in result["comparability"]["reasons"]
    rows = {(row["sequence_index"], row["mutation_aa"]): row for row in result["rows"]}
    assert rows[(1, "A")]["raw_score_delta"] is None
    assert rows[(1, "A")]["classification_transition"] is None


def test_policy_difference_keeps_raw_deltas_and_omits_class_transitions():
    from services.frustrampnn.comparison import compare_landscapes

    reference = _landscape(scores={(1, "A"): 0.0})
    target_basis = _compatibility_basis(
        "f" * 64,
        policy_sha256="d" * 64,
        high_max=-0.5,
        minimal_min=0.5,
    )
    target = _landscape(
        scores={(1, "A"): 0.7},
        config_hash="f" * 64,
        compatibility_basis=target_basis,
    )

    result = compare_landscapes(reference, target)

    assert result["compatibility_domains"]["raw_score"]["status"] == "compatible"
    assert result["compatibility_domains"]["classification"]["status"] == (
        "policy_different"
    )
    row = next(
        row
        for row in result["rows"]
        if row["sequence_index"] == 1 and row["mutation_aa"] == "A"
    )
    assert row["raw_score_delta"] == 0.7
    assert row["classification_transition"] is None


def test_comparison_rejects_unknown_identity_without_synthesizing_legacy_compatibility():
    from services.frustrampnn.comparison import ComparisonCompatibilityError, compare_landscapes

    historical = _landscape()
    historical.pop("comparison_compatibility_id")
    historical.pop("comparison_compatibility_basis")

    with pytest.raises(ComparisonCompatibilityError) as rejected:
        compare_landscapes(historical, _landscape())

    assert rejected.value.metadata == {
        "compatibility_status": "unknown",
        "left_comparison_compatibility_id": None,
        "right_comparison_compatibility_id": _landscape()[
            "comparison_compatibility_id"
        ],
        "override_used": False,
        "compatibility_differences": [],
        "compatibility_domains": {
            "raw_score": {
                "status": "unknown",
                "reasons": ["persisted_compatibility_authority_unavailable"],
                "differences": [],
            },
            "classification": {
                "status": "unknown",
                "reasons": ["persisted_compatibility_authority_unavailable"],
                "differences": [],
            },
            "identity_alignment": {
                "status": "exact",
                "reasons": ["exact_source_authoritative_identity_membership"],
                "differences": [],
                "reference_identity_count": 2,
                "target_identity_count": 2,
                "aligned_identity_count": 2,
            },
        },
    }


def test_equal_compatibility_id_cannot_hide_persisted_basis_tamper():
    from services.frustrampnn.comparison import ComparisonCompatibilityError, compare_landscapes

    reference = _landscape()
    target = copy.deepcopy(reference)
    target["comparison_compatibility_basis"]["raw_score_semantics"]["model"][
        "checkpoint_sha256"
    ] = "f" * 64

    with pytest.raises(ComparisonCompatibilityError) as rejected:
        compare_landscapes(reference, target)

    assert rejected.value.metadata["compatibility_status"] == "incompatible"
    assert [
        difference["field_path"]
        for difference in rejected.value.metadata["compatibility_differences"]
    ] == ["raw_score_semantics.model.checkpoint_sha256"]


def test_equal_ids_with_broken_basis_self_binding_are_incompatible_not_unknown():
    from services.frustrampnn.comparison import (
        ComparisonCompatibilityError,
        compare_landscapes,
    )

    reference = _landscape()
    target = copy.deepcopy(reference)
    invalid_id = "f" * 64
    reference["comparison_compatibility_id"] = invalid_id
    target["comparison_compatibility_id"] = invalid_id

    with pytest.raises(ComparisonCompatibilityError) as rejected:
        compare_landscapes(reference, target)

    assert rejected.value.metadata == {
        "compatibility_status": "incompatible",
        "left_comparison_compatibility_id": invalid_id,
        "right_comparison_compatibility_id": invalid_id,
        "override_used": False,
        "compatibility_differences": [],
        "compatibility_domains": {
            "raw_score": {
                "status": "hard_incompatible",
                "reasons": ["compatibility_basis_self_binding_invalid"],
                "differences": [],
            },
            "classification": {
                "status": "unknown",
                "reasons": ["compatibility_basis_self_binding_invalid"],
                "differences": [],
            },
            "identity_alignment": {
                "status": "exact",
                "reasons": ["exact_source_authoritative_identity_membership"],
                "differences": [],
                "reference_identity_count": 2,
                "target_identity_count": 2,
                "aligned_identity_count": 2,
            },
        },
    }


def test_comparison_marks_unmapped_residue_instead_of_position_join():
    from services.frustrampnn.comparison import compare_landscapes

    reference = _landscape()
    target = _landscape()
    target["residues"][0]["auth_seq_id"] = 99
    result = compare_landscapes(reference, target)

    rows = [row for row in result["rows"] if row["reference"]["sequence_index"] == 1]
    assert {row["mapping_state"] for row in rows} == {"unmapped"}
    assert all(row["raw_score_delta"] is None for row in rows)


@pytest.mark.parametrize(
    ("mutation", "expected", "aligned_count"),
    [
        (lambda target: None, "exact", 2),
        (lambda target: target["residues"].pop(), "partial", 1),
        (
            lambda target: [
                row.update({"entity_instance_id": "other"})
                for row in target["residues"]
            ],
            "none",
            0,
        ),
    ],
)
def test_identity_alignment_is_exact_partial_or_none(
    mutation,
    expected: str,
    aligned_count: int,
):
    from services.frustrampnn.comparison import compare_landscapes

    reference = _landscape()
    target = _landscape()
    mutation(target)

    result = compare_landscapes(reference, target)

    alignment = result["compatibility_domains"]["identity_alignment"]
    assert alignment["status"] == expected
    assert alignment["reasons"]
    assert alignment["aligned_identity_count"] == aligned_count


def test_guidance_requires_explicit_direction_and_region_and_ranks_deterministically():
    from services.frustrampnn.guidance import GuidanceValidationError, build_guidance_plan

    landscape = _landscape(scores={(1, "A"): -2.0, (1, "C"): -1.5, (2, "A"): 0.2, (2, "C"): 0.1})
    objective = {
        "objective_type": "score_aggregate",
        "direction": "higher_is_better",
        "aggregation": "mean",
        "target_class": None,
    }
    region = {"region_type": "residue_set", "residues": [{"auth_asym_id": "A", "auth_seq_id": 1, "insertion_code": ""}]}
    plan = build_guidance_plan(
        landscape=landscape,
        region=region,
        objective=objective,
        constraints={"prohibited_mutations": ["A:C"]},
        ranking={"mode": "lexicographic", "tie_break": "sequence_index_then_mutation"},
        rationale="Test target-region hypothesis",
    )
    from services.frustrampnn.contracts import validate_schema
    validate_schema("frustrampnn_guidance_v1", plan)

    assert plan["schema_name"] == "frustrampnn_guidance"
    assert plan["schema_version"] == 1
    assert plan["region"]["resolved_residues"] == [{"auth_asym_id": "A", "auth_seq_id": 1, "insertion_code": ""}]
    assert plan["ranked_slots"][0]["mutation_aa"] == "A"
    assert plan["ranked_slots"][0]["mutation_aa"] != "C"
    assert len(plan["guidance_sha256"]) == 64
    assert plan["source_landscape_sha256"] == "0" * 64 or len(plan["source_landscape_sha256"]) == 64

    invalid = copy.deepcopy(objective)
    invalid.pop("direction")
    with pytest.raises(GuidanceValidationError, match="direction"):
        build_guidance_plan(landscape=landscape, region=region, objective=invalid, constraints={}, ranking={}, rationale="x")


def test_guidance_rejects_legacy_landscapes_without_global_configuration():
    from services.frustrampnn.guidance import GuidanceValidationError, build_guidance_plan

    landscape = _landscape()
    landscape.pop("configuration_id")
    with pytest.raises(GuidanceValidationError, match="global configuration"):
        build_guidance_plan(
            landscape=landscape,
            region={"region_type": "residue_set", "residues": [{"auth_asym_id": "A", "auth_seq_id": 1}]},
            objective={"objective_type": "score_aggregate", "direction": "lower_is_better"},
            constraints={},
            ranking={"mode": "lexicographic"},
            rationale="Legacy landscapes are comparison-only.",
        )


def test_multi_state_comparison_keeps_target_tracks_and_missingness_separate():
    from services.frustrampnn.comparison import compare_landscape_set
    from services.frustrampnn.contracts import validate_schema

    reference = _landscape()
    target_one = copy.deepcopy(reference)
    target_one["landscape_id"] = "target-one"
    target_one["residues"][0]["slots"][0]["score"] = -2.5
    target_two = copy.deepcopy(reference)
    target_two["landscape_id"] = "target-two"
    target_two["residues"][1]["slots"][0]["score"] = None
    target_two["residues"][1]["slots"][0]["scoreable"] = False
    target_two["residues"][1]["slots"][0]["missingness_reason"] = "runtime_missing"

    result = compare_landscape_set(reference, [target_one, target_two], comparison_id="cmp-multi")
    validate_schema("frustrampnn_multistate_comparison_v1", result)

    assert result["comparison_mode"] == "multi_state"
    assert len(result["target_landscape_sha256s"]) == 2
    assert len(result["rows"]) == 4
    missing = [row for row in result["rows"] if "target_missing" in row["missingness_by_target"]]
    assert len(missing) == 1
    assert len(missing[0]["targets"]) == 2


def test_multi_comparison_request_is_closed_bounded_and_cross_validated_before_reads():
    reference = {
        "reference_job_id": "job-reference",
        "reference_invocation_id": "invoke-reference",
    }

    def target(
        job_id: str,
        invocation_id: str,
        *,
        reference_job_id: str = "job-reference",
        reference_invocation_id: str = "invoke-reference",
    ) -> dict[str, str]:
        return {
            "reference_job_id": reference_job_id,
            "reference_invocation_id": reference_invocation_id,
            "target_job_id": job_id,
            "target_invocation_id": invocation_id,
        }

    valid = MultiComparisonCreateRequest.model_validate(
        {
            **reference,
            "targets": [target("job-target", "invoke-target")],
        }
    )
    assert len(valid.targets) == 1

    invalid_targets = (
        [],
        [target(f"job-{index}", f"invoke-{index}") for index in range(9)],
        [target("job-target", "invoke-target")] * 2,
        [target("job-reference", "invoke-reference")],
        [
            target(
                "job-target",
                "invoke-target",
                reference_job_id="other-reference",
            )
        ],
        [
            target(
                "job-target",
                "invoke-target",
                reference_invocation_id="other-invocation",
            )
        ],
    )
    for targets in invalid_targets:
        with pytest.raises(ValidationError):
            MultiComparisonCreateRequest.model_validate(
                {
                    **reference,
                    "targets": targets,
                }
            )


def test_multi_state_compatibility_aggregates_all_targets_and_labels_differences():
    from services.frustrampnn.comparison import (
        ComparisonCompatibilityError,
        compare_landscape_set,
    )

    reference = _landscape(scores={(1, "A"): 0.0})
    incompatible_basis = _compatibility_basis(
        "f" * 64,
        checkpoint_sha256="f" * 64,
    )
    incompatible = _landscape(
        scores={(1, "A"): 0.7},
        config_hash="f" * 64,
        compatibility_basis=incompatible_basis,
    )
    incompatible["target_id"] = "target-incompatible"
    unknown = _landscape(scores={(1, "A"): 0.3})
    unknown["target_id"] = "target-unknown"
    unknown.pop("comparison_compatibility_basis")

    with pytest.raises(ComparisonCompatibilityError) as rejected:
        compare_landscape_set(reference, [unknown, incompatible])

    assert rejected.value.metadata["compatibility_status"] == "incompatible"
    assert rejected.value.metadata["override_used"] is False
    assert [
        item["field_path"]
        for item in rejected.value.metadata["compatibility_differences"]
    ] == [
        "target-incompatible:raw_score_semantics.model.checkpoint_sha256"
    ]

    overridden = compare_landscape_set(
        reference,
        [unknown, incompatible],
        allow_incompatible=True,
    )
    assert overridden["comparability"]["compatibility_status"] == "incompatible"
    assert overridden["comparability"]["override_used"] is True
    assert overridden["summary"]["biologically_scored"] == 0
    assert all(
        delta is None
        for row in overridden["rows"]
        for delta in row["raw_score_deltas"]
    )


def test_guidance_rejects_ambiguous_optimize_frustration_objective():
    from services.frustrampnn.guidance import GuidanceValidationError, build_guidance_plan

    with pytest.raises(GuidanceValidationError, match="direction|hypothesis"):
        build_guidance_plan(
            landscape=_landscape(),
            region={"region_type": "residue_set", "residues": []},
            objective={"objective_type": "optimize_frustration"},
            constraints={},
            ranking={},
            rationale="",
        )


def test_external_candidate_handoff_preserves_producer_and_parent_lineage():
    from services.frustrampnn.jobs import handoff_selection

    selection = handoff_selection(
        candidate_id="variant-1",
        producer_id="external-redesign",
        payload=b"ATOM\n",
        filename="variant-1.pdb",
        parent_job_id="parent-job",
        parent_invocation_id="parent-invocation",
        parent_landscape_sha256="a" * 64,
        guidance_id="guidance-1",
        nucleotide_edit_set=[{"position": 17, "operation": "insert", "base": "A"}],
        protein_sequence_sha256="b" * 64,
    )
    assert selection.design_id is None
    assert selection.producer_coordinates["candidate_id"] == "variant-1"
    assert selection.producer_coordinates["producer_id"] == "external-redesign"
    assert selection.producer_coordinates["parent_landscape_sha256"] == "a" * 64
    assert selection.producer_coordinates["guidance_id"] == "guidance-1"
    assert selection.producer_coordinates["nucleotide_edit_set"][0]["operation"] == "insert"


@pytest.mark.asyncio
@pytest.mark.parametrize("submitted_guidance_id", ["guidance-api", None])
async def test_external_candidate_handoff_api_binds_parent_and_producer_metadata(
    derived_session,
    monkeypatch,
    submitted_guidance_id,
):
    app = FastAPI()
    app.include_router(router)
    captured = {}

    if submitted_guidance_id is not None:
        derived_session.add(
            FrustraMPNNGuidancePlan(
                guidance_id=submitted_guidance_id,
                source_landscape_sha256="5" * 64,
                source_parent_job_id="job-derived",
                source_invocation_id="invoke-derived",
                guidance_sha256="c" * 64,
                payload_json={"guidance_id": submitted_guidance_id},
            )
        )
        await derived_session.commit()

    async def fake_create_child(session, *, selections, source_parent, trigger, **_kwargs):
        captured["selection"] = selections[0]
        captured["source_parent"] = source_parent.id
        captured["trigger"] = trigger
        return source_parent

    async def fake_receipt(session, child):
        requested = default_settings()
        return {
            "job_id": "child-handoff",
            "child_job_id": "child-handoff",
            "result_job_id": "child-handoff",
            "name": "child-handoff",
            "parent_job_id": "job-derived",
            "source_parent_job_id": "job-derived",
            "trigger": "external_candidate_handoff",
            "status": "queued",
            "created_at": None,
            "started_at": None,
            "completed_at": None,
            "settings_value_origin": "bms_default",
            "requested_settings": requested.model_dump(mode="json"),
            "requested_settings_sha256": requested_settings_sha256(requested),
            "candidates": [],
            "results": [],
        }

    monkeypatch.setattr(frustrampnn_router, "create_child_job", fake_create_child)
    monkeypatch.setattr(frustrampnn_router, "_child_job_receipt", fake_receipt)

    async def override_session():
        yield derived_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        data = {
            "candidate_id": "variant-api",
            "producer_id": "external-redesign",
            "parent_job_id": "job-derived",
            "parent_invocation_id": "invoke-derived",
            "parent_landscape_sha256": "5" * 64,
            "nucleotide_edit_set": '[{"position":17,"operation":"insert","base":"A"}]',
            "protein_sequence_sha256": "b" * 64,
        }
        if submitted_guidance_id is not None:
            data["guidance_id"] = submitted_guidance_id
        response = await client.post(
            "/api/frustrampnn/candidates/handoff",
            data=data,
            files={"structure_file": ("variant-api.pdb", b"ATOM\n", "chemical/x-pdb")},
        )
    assert response.status_code == 202, response.text
    assert response.json()["handoff"] == {
        "parent_landscape_sha256": "5" * 64,
        "parent_candidate_id": "candidate-derived",
        "guidance_id": submitted_guidance_id,
        "producer_id": "external-redesign",
    }
    assert captured["source_parent"] == "job-derived"
    assert captured["trigger"] == "external_candidate_handoff"
    assert captured["selection"].producer_coordinates["candidate_id"] == "variant-api"
    assert captured["selection"].producer_coordinates["guidance_id"] == submitted_guidance_id
    assert captured["selection"].producer_coordinates["parent_landscape_sha256"] == "5" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "guidance_id",
        "source_parent_job_id",
        "source_invocation_id",
        "source_landscape_sha256",
    ),
    [
        ("guidance-nonexistent", None, None, None),
        ("guidance-wrong-parent", "other-job", "invoke-derived", "5" * 64),
        ("guidance-wrong-invocation", "job-derived", "other-invocation", "5" * 64),
        ("guidance-wrong-landscape", "job-derived", "invoke-derived", "6" * 64),
    ],
)
async def test_external_candidate_handoff_rejects_unverified_guidance_before_child_creation(
    derived_session,
    monkeypatch,
    guidance_id,
    source_parent_job_id,
    source_invocation_id,
    source_landscape_sha256,
):
    if source_parent_job_id is not None:
        derived_session.add(
            FrustraMPNNGuidancePlan(
                guidance_id=guidance_id,
                source_landscape_sha256=source_landscape_sha256,
                source_parent_job_id=source_parent_job_id,
                source_invocation_id=source_invocation_id,
                guidance_sha256="d" * 64,
                payload_json={"guidance_id": guidance_id},
            )
        )
        await derived_session.commit()

    app = FastAPI()
    app.include_router(router)
    child_creation_calls = 0

    async def observed_create_child(
        _session, *, selections, source_parent, trigger, **_kwargs
    ):
        nonlocal child_creation_calls
        child_creation_calls += 1
        return source_parent

    async def fake_receipt(_session, _child):
        requested = default_settings()
        return {
            "job_id": "child-unverified-guidance",
            "child_job_id": "child-unverified-guidance",
            "result_job_id": "child-unverified-guidance",
            "name": "child-unverified-guidance",
            "parent_job_id": "job-derived",
            "source_parent_job_id": "job-derived",
            "trigger": "external_candidate_handoff",
            "status": "queued",
            "created_at": None,
            "started_at": None,
            "completed_at": None,
            "settings_value_origin": "bms_default",
            "requested_settings": requested.model_dump(mode="json"),
            "requested_settings_sha256": requested_settings_sha256(requested),
            "candidates": [],
            "results": [],
        }

    monkeypatch.setattr(frustrampnn_router, "create_child_job", observed_create_child)
    monkeypatch.setattr(frustrampnn_router, "_child_job_receipt", fake_receipt)

    async def override_session():
        yield derived_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/frustrampnn/candidates/handoff",
            data={
                "candidate_id": "variant-unverified-guidance",
                "producer_id": "external-redesign",
                "parent_job_id": "job-derived",
                "parent_invocation_id": "invoke-derived",
                "parent_landscape_sha256": "5" * 64,
                "guidance_id": guidance_id,
                "nucleotide_edit_set": "[]",
            },
            files={
                "structure_file": (
                    "variant-unverified-guidance.pdb",
                    b"ATOM\n",
                    "chemical/x-pdb",
                )
            },
        )

    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": "handoff guidance does not match persisted parent authority"
    }
    assert child_creation_calls == 0


@pytest.mark.asyncio
async def test_external_candidate_handoff_rejects_parent_landscape_mismatch_before_child_creation(
    derived_session,
    monkeypatch,
):
    app = FastAPI()
    app.include_router(router)
    child_creation_calls = 0

    async def forbidden_create_child(*_args, **_kwargs):
        nonlocal child_creation_calls
        child_creation_calls += 1
        raise AssertionError("mismatched handoff reached child creation")

    monkeypatch.setattr(frustrampnn_router, "create_child_job", forbidden_create_child)

    async def override_session():
        yield derived_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/frustrampnn/candidates/handoff",
            data={
                "candidate_id": "variant-mismatch",
                "producer_id": "external-redesign",
                "parent_job_id": "job-derived",
                "parent_invocation_id": "invoke-derived",
                "parent_landscape_sha256": "a" * 64,
                "nucleotide_edit_set": "[]",
            },
            files={
                "structure_file": (
                    "variant-mismatch.pdb",
                    b"ATOM\n",
                    "chemical/x-pdb",
                )
            },
        )

    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": "handoff parent landscape SHA-256 does not match persisted parent authority"
    }
    assert child_creation_calls == 0


@pytest_asyncio.fixture
async def derived_session(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BMS_DATA", str(tmp_path / "bms-data"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'derived.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(Job(id="job-derived", name="derived", status="completed", model_id="frustrampnn", mode="analyze", params={}))
        variants = (
            ("invoke-derived", "candidate-derived", _landscape(), True),
            ("invoke-derived-2", "candidate-derived-2", _landscape(), True),
            ("invoke-derived-3", "candidate-derived-3", _landscape(), True),
            (
                "invoke-incompatible",
                "candidate-incompatible",
                _landscape(
                    config_hash="f" * 64,
                    compatibility_basis=_compatibility_basis(
                        "f" * 64,
                        checkpoint_sha256="f" * 64,
                    ),
                ),
                True,
            ),
            ("invoke-historical", "candidate-historical", _landscape(), False),
        )
        for invocation_id, candidate_id, landscape, has_v2_authority in variants:
            basis = landscape["comparison_compatibility_basis"]
            compatibility_id = landscape["comparison_compatibility_id"]
            statistics = (
                {
                    "comparison_compatibility_basis": basis,
                    "comparison_compatibility_id": compatibility_id,
                }
                if has_v2_authority
                else None
            )
            summary = (
                {
                    "schema_name": "frustrampnn_summary",
                    "schema_version": 2,
                    "execution_configuration_id": (
                        "frustrampnn_execution_configuration_v2"
                    ),
                    "execution_configuration_sha256": landscape[
                        "configuration_sha256"
                    ],
                    "requested_settings_sha256": "6" * 64,
                    "effective_settings_sha256": "7" * 64,
                    "runtime_identity_sha256": "a" * 64,
                    "source_artifact_sha256": "2" * 64,
                    "structure_map_sha256": landscape["structure_map_sha256"],
                    "normalized_pdb_sha256": landscape["normalized_pdb_sha256"],
                    "landscape_sha256": "5" * 64,
                    "threshold_policy_id": "frustrampnn_class_v1",
                    "threshold_policy": landscape["threshold_policy"],
                    "threshold_policy_sha256": landscape[
                        "threshold_policy_sha256"
                    ],
                }
                if has_v2_authority
                else {
                    "configuration_id": landscape["configuration_id"],
                    "configuration_sha256": landscape["configuration_sha256"],
                    "threshold_policy": landscape["threshold_policy"],
                    "threshold_policy_sha256": landscape[
                        "threshold_policy_sha256"
                    ],
                }
            )
            session.add(FrustraMPNNResult(
                parent_job_id="job-derived", invocation_id=invocation_id,
                parent_workflow_id="structure_prediction", candidate_id=candidate_id,
                design_id=None, requiredness="required", request_sha256="1" * 64,
                source_artifact_id=None, source_artifact_sha256="2" * 64,
                manifest_sha256="3" * 64, manifest_json={}, summary_sha256="4" * 64,
                summary_json=summary,
                runtime_identity_json={}, assigned_gpu_json={},
                terminal_result_json={
                    "status": "succeeded",
                    "component_contract_version": (
                        "2.0" if has_v2_authority else "1.0"
                    ),
                },
                parent_metadata_json={},
                settings_sha256="6" * 64 if has_v2_authority else None,
                effective_settings_sha256="7" * 64 if has_v2_authority else None,
                effective_settings_json={"fixture": True} if has_v2_authority else None,
                capability_inventory_sha256="8" * 64 if has_v2_authority else None,
                statistics_sha256="9" * 64 if has_v2_authority else None,
                statistics_json=statistics,
                comparison_compatibility_id=(
                    compatibility_id if has_v2_authority else None
                ),
                created_at=datetime(2026, 8, 2),
            ))
            artifact_rows = []
            for residue in landscape["residues"]:
                residue_json = {key: value for key, value in residue.items() if key != "slots"}
                for slot in residue["slots"]:
                    provenance = {
                        "landscape_sha256": "5" * 64,
                        "structure_map_sha256": landscape["structure_map_sha256"],
                        "normalized_pdb_sha256": landscape["normalized_pdb_sha256"],
                        "raw_csv_sha256": landscape["raw_csv_sha256"],
                        "threshold_policy": landscape["threshold_policy"],
                        "threshold_policy_sha256": landscape["threshold_policy_sha256"],
                    }
                    artifact_rows.append({
                        "id": f"row-{invocation_id}-{residue['sequence_index']}-{slot['mutation_aa']}",
                        "target_id": landscape["target_id"],
                        "entity_instance_id": residue["entity_instance_id"],
                        "auth_asym_id": residue["auth_asym_id"],
                        "auth_seq_id": str(residue["auth_seq_id"]),
                        "insertion_code": residue["insertion_code"],
                        "sequence_index": residue["sequence_index"],
                        "wt": residue["wt"],
                        "mutation_aa": slot["mutation_aa"],
                        "score": slot["score"],
                        "score_class": slot["class"] or "neutral",
                        "scoreable": slot["scoreable"],
                        "status": slot["status"],
                        "reason": slot["reason"],
                        "row_json": json.dumps(
                            {"residue": residue_json, "slot": slot},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        "provenance_json": json.dumps(
                            provenance,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    })
            await publish_table_rows(
                session,
                owner_kind="frustrampnn_result",
                owner_id=f"job-derived:{invocation_id}",
                role="landscape",
                schema_id="bms.frustrampnn-landscape.v1",
                source_sha256="5" * 64,
                rows=artifact_rows,
                schema=_FRUSTRA_LANDSCAPE_PARQUET_SCHEMA,
            )
        await session.commit()
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_v2_persisted_landscape_preserves_generation_identity_and_guidance(
    derived_session,
) -> None:
    from services.frustrampnn.derived import load_persisted_landscape
    from services.frustrampnn.guidance import build_guidance_plan

    result = await derived_session.get(
        FrustraMPNNResult,
        ("job-derived", "invoke-derived"),
    )
    landscape = await load_persisted_landscape(derived_session, result)

    assert landscape["schema_name"] == "frustrampnn_landscape"
    assert landscape["schema_version"] == 2
    assert landscape["execution_configuration_id"] == (
        "frustrampnn_execution_configuration_v2"
    )
    assert landscape["execution_configuration_sha256"] == global_configuration()[
        "configuration_sha256"
    ]
    assert landscape["requested_settings_sha256"] == "6" * 64
    assert landscape["effective_settings_sha256"] == "7" * 64
    assert landscape["runtime_identity_sha256"] == "a" * 64
    assert landscape["landscape_sha256"] == "5" * 64
    assert "configuration_id" not in landscape
    assert "configuration_sha256" not in landscape

    guidance = build_guidance_plan(
        landscape=landscape,
        region={
            "region_type": "residue_set",
            "residues": [
                {"auth_asym_id": "A", "auth_seq_id": 1, "insertion_code": ""}
            ],
        },
        objective={
            "objective_type": "score_aggregate",
            "direction": "higher_is_better",
            "aggregation": "mean",
        },
        constraints={},
        ranking={"mode": "lexicographic"},
        rationale="v2 persisted landscape guidance regression",
    )
    assert guidance["source_landscape_sha256"] == "5" * 64
    assert guidance["configuration_id"] == (
        "frustrampnn_execution_configuration_v2"
    )
    assert guidance["configuration_sha256"] == global_configuration()[
        "configuration_sha256"
    ]


@pytest.mark.asyncio
async def test_persisted_comparison_and_guidance_are_immutable_and_retrievable(derived_session):
    from services.frustrampnn.comparison import compare_landscapes
    from services.frustrampnn.derived import load_persisted_landscape, persist_comparison, persist_guidance_plan
    from services.frustrampnn.guidance import build_guidance_plan

    result = await derived_session.get(FrustraMPNNResult, ("job-derived", "invoke-derived"))
    landscape = await load_persisted_landscape(derived_session, result)
    landscape["comparison_compatibility_id"] = result.comparison_compatibility_id
    landscape["comparison_compatibility_basis"] = result.statistics_json[
        "comparison_compatibility_basis"
    ]
    assert len(landscape["residues"]) == 2
    comparison = compare_landscapes(landscape, landscape, comparison_id="cmp-derived")
    stored = await persist_comparison(derived_session, comparison, reference_result=result, target_result=result)
    assert stored.comparison_id == "cmp-derived"
    await derived_session.flush()
    raw_payload = json.loads(
        (
            await derived_session.execute(
                text(
                    "SELECT payload_json FROM frustrampnn_comparisons "
                    "WHERE comparison_id = :comparison_id"
                ),
                {"comparison_id": "cmp-derived"},
            )
        ).scalar_one()
    )
    assert raw_payload["schema"] == "bms.scientific-artifact-reference.v1"
    assert "rows" not in raw_payload
    guidance = build_guidance_plan(
        landscape=landscape,
        region={"region_type": "residue_set", "residues": [{"auth_asym_id": "A", "auth_seq_id": 1, "insertion_code": ""}]},
        objective={"objective_type": "score_aggregate", "direction": "higher_is_better", "aggregation": "mean"},
        constraints={}, ranking={"mode": "lexicographic"}, rationale="derived persistence test", guidance_id="guidance-derived",
    )
    stored_guidance = await persist_guidance_plan(derived_session, guidance, source_result=result)
    await derived_session.commit()
    assert stored_guidance.guidance_sha256 == guidance["guidance_sha256"]

    with pytest.raises(ValueError, match="immutable|conflict"):
        changed = copy.deepcopy(comparison)
        changed["summary"]["total_rows"] = 99
        await persist_comparison(derived_session, changed, reference_result=result, target_result=result)


@pytest.mark.asyncio
async def test_comparison_and_guidance_api_is_persisted_and_retrievable(derived_session):
    app = FastAPI()
    app.include_router(router)

    async def override_session():
        yield derived_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        comparison_response = await client.post(
            "/api/frustrampnn/comparisons",
            json={
                "reference_job_id": "job-derived",
                "reference_invocation_id": "invoke-derived",
                "target_job_id": "job-derived",
                "target_invocation_id": "invoke-derived",
            },
        )
        assert comparison_response.status_code == 201, comparison_response.text
        comparison = comparison_response.json()
        assert comparison["persisted"] is True
        assert comparison["compatibility_status"] == "compatible"
        assert comparison["override_used"] is False
        assert comparison["compatibility_differences"] == []
        assert (
            comparison["left_comparison_compatibility_id"]
            == comparison["right_comparison_compatibility_id"]
        )
        comparison_id = comparison["comparison_id"]
        multi_response = await client.post(
            "/api/frustrampnn/comparisons/multi",
            json={
                "reference_job_id": "job-derived",
                "reference_invocation_id": "invoke-derived",
                "targets": [
                    {"reference_job_id": "job-derived", "reference_invocation_id": "invoke-derived", "target_job_id": "job-derived", "target_invocation_id": "invoke-derived-2"},
                    {"reference_job_id": "job-derived", "reference_invocation_id": "invoke-derived", "target_job_id": "job-derived", "target_invocation_id": "invoke-derived-3"},
                ],
            },
        )
        assert multi_response.status_code == 201, multi_response.text
        multi = multi_response.json()
        assert multi["comparison_mode"] == "multi_state"
        assert len(multi["target_landscape_sha256s"]) == 2
        assert multi["target_labels"] == ["target-0001", "target-0002"]
        assert multi["source_result_references"] == [
            {
                "role": "reference",
                "target_label": None,
                "parent_job_id": "job-derived",
                "invocation_id": "invoke-derived",
                "landscape_sha256": "5" * 64,
                "configuration_sha256": global_configuration()["configuration_sha256"],
            },
            {
                "role": "target",
                "target_label": "target-0001",
                "parent_job_id": "job-derived",
                "invocation_id": "invoke-derived-2",
                "landscape_sha256": "5" * 64,
                "configuration_sha256": global_configuration()["configuration_sha256"],
            },
            {
                "role": "target",
                "target_label": "target-0002",
                "parent_job_id": "job-derived",
                "invocation_id": "invoke-derived-3",
                "landscape_sha256": "5" * 64,
                "configuration_sha256": global_configuration()["configuration_sha256"],
            },
        ]
        assert [item["target_label"] for item in multi["pair_compatibility"]] == [
            "target-0001",
            "target-0002",
        ]
        fetched_multi = await client.get(
            f"/api/frustrampnn/comparisons/{multi['comparison_id']}"
        )
        assert fetched_multi.status_code == 200
        assert fetched_multi.json()["source_result_references"] == multi[
            "source_result_references"
        ]
        assert fetched_multi.json()["pair_compatibility"] == multi[
            "pair_compatibility"
        ]
        multi_rows = await client.get(f"/api/frustrampnn/comparisons/{multi['comparison_id']}/rows")
        assert multi_rows.status_code == 200
        assert multi_rows.json()["total"] == 4
        rows_response = await client.get(f"/api/frustrampnn/comparisons/{comparison_id}/rows", params={"limit": 2})
        assert rows_response.status_code == 200
        assert rows_response.json()["total"] == 4
        guidance_response = await client.post(
            "/api/frustrampnn/guidance",
            json={
                "source_job_id": "job-derived",
                "source_invocation_id": "invoke-derived",
                "region": {"region_type": "residue_set", "residues": [{"auth_asym_id": "A", "auth_seq_id": 1, "insertion_code": ""}]},
                "objective": {"objective_type": "score_aggregate", "direction": "higher_is_better", "aggregation": "mean"},
                "constraints": {},
                "ranking": {"mode": "lexicographic"},
                "rationale": "API guidance hypothesis",
            },
        )
        assert guidance_response.status_code == 201, guidance_response.text
        guidance = guidance_response.json()
        assert guidance["decision_support_only"] is True
        fetched = await client.get(f"/api/frustrampnn/guidance/{guidance['guidance_id']}")
        assert fetched.status_code == 200
        assert fetched.json()["guidance_sha256"] == guidance["guidance_sha256"]
        invalid = await client.post(
            "/api/frustrampnn/guidance",
            json={
                "source_job_id": "job-derived", "source_invocation_id": "invoke-derived",
                "region": {"region_type": "residue_set", "residues": [{"auth_asym_id": "A", "auth_seq_id": 1, "insertion_code": ""}]},
                "objective": {"objective_type": "score_aggregate"},
                "rationale": "missing direction",
            },
        )
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_comparison_api_fails_closed_and_override_is_explicit_without_source_mutation(
    derived_session,
):
    app = FastAPI()
    app.include_router(router)

    async def override_session():
        yield derived_session

    app.dependency_overrides[get_session] = override_session
    reference = await derived_session.get(
        FrustraMPNNResult, ("job-derived", "invoke-derived")
    )
    incompatible = await derived_session.get(
        FrustraMPNNResult, ("job-derived", "invoke-incompatible")
    )
    before_statistics = copy.deepcopy(reference.statistics_json)
    before_rows = (
        await derived_session.execute(
            select(FrustraMPNNLandscapeRow.row_json)
            .where(
                FrustraMPNNLandscapeRow.parent_job_id == "job-derived",
                FrustraMPNNLandscapeRow.invocation_id == "invoke-derived",
            )
            .order_by(FrustraMPNNLandscapeRow.id)
        )
    ).scalars().all()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post(
            "/api/frustrampnn/comparisons",
            json={
                "reference_job_id": "job-derived",
                "reference_invocation_id": "invoke-derived",
                "target_job_id": "job-derived",
                "target_invocation_id": "invoke-incompatible",
            },
        )
        assert rejected.status_code == 409, rejected.text
        detail = rejected.json()["detail"]
        assert detail["compatibility_status"] == "incompatible"
        assert detail["left_comparison_compatibility_id"] == (
            reference.comparison_compatibility_id
        )
        assert detail["right_comparison_compatibility_id"] == (
            incompatible.comparison_compatibility_id
        )
        assert detail["override_used"] is False
        assert detail["compatibility_domains"]["raw_score"]["status"] == (
            "hard_incompatible"
        )
        assert detail["compatibility_differences"] == [
            {
                "field_path": "raw_score_semantics.model.checkpoint_sha256",
                "left": reference.statistics_json["comparison_compatibility_basis"][
                    "raw_score_semantics"
                ]["model"]["checkpoint_sha256"],
                "right": incompatible.statistics_json["comparison_compatibility_basis"][
                    "raw_score_semantics"
                ]["model"]["checkpoint_sha256"],
            }
        ]

        unknown = await client.post(
            "/api/frustrampnn/comparisons",
            json={
                "reference_job_id": "job-derived",
                "reference_invocation_id": "invoke-derived",
                "target_job_id": "job-derived",
                "target_invocation_id": "invoke-historical",
            },
        )
        assert unknown.status_code == 409, unknown.text
        assert unknown.json()["detail"]["compatibility_status"] == "unknown"
        assert unknown.json()["detail"]["right_comparison_compatibility_id"] is None
        assert int((await derived_session.execute(
            select(func.count()).select_from(FrustraMPNNComparison)
        )).scalar_one()) == 0

        overridden = await client.post(
            "/api/frustrampnn/comparisons",
            json={
                "reference_job_id": "job-derived",
                "reference_invocation_id": "invoke-derived",
                "target_job_id": "job-derived",
                "target_invocation_id": "invoke-incompatible",
                "allow_incompatible": True,
            },
        )
        assert overridden.status_code == 201, overridden.text
        body = overridden.json()
        assert body["compatibility_status"] == "incompatible"
        assert body["left_comparison_compatibility_id"] == (
            reference.comparison_compatibility_id
        )
        assert body["right_comparison_compatibility_id"] == (
            incompatible.comparison_compatibility_id
        )
        assert body["override_used"] is True
        assert body["compatibility_differences"] == rejected.json()["detail"][
            "compatibility_differences"
        ]
        assert body["summary"]["total_rows"] == 4
        assert body["summary"]["biologically_scored"] == 0
        assert all(row["raw_score_delta"] is None for row in body["rows"])
        assert all(row["classification_transition"] is None for row in body["rows"])

    await derived_session.refresh(reference)
    after_rows = (
        await derived_session.execute(
            select(FrustraMPNNLandscapeRow.row_json)
            .where(
                FrustraMPNNLandscapeRow.parent_job_id == "job-derived",
                FrustraMPNNLandscapeRow.invocation_id == "invoke-derived",
            )
            .order_by(FrustraMPNNLandscapeRow.id)
        )
    ).scalars().all()
    assert reference.statistics_json == before_statistics
    assert after_rows == before_rows


def _persistable_multistate_payload() -> tuple[dict, SimpleNamespace, SimpleNamespace]:
    from services.frustrampnn.comparison import compare_landscape_set
    from services.frustrampnn.contracts import canonical_sha256

    reference = _landscape()
    targets = [copy.deepcopy(reference), copy.deepcopy(reference)]
    targets[0]["landscape_id"] = "target-one"
    targets[1]["landscape_id"] = "target-two"
    payload = compare_landscape_set(reference, targets, comparison_id="cmp-tamper")
    payload["source_result_references"] = [
        {
            "role": "reference",
            "target_label": None,
            "parent_job_id": "job-reference",
            "invocation_id": "invoke-reference",
            "landscape_sha256": payload["reference_landscape_sha256"],
            "configuration_sha256": payload["reference_configuration_sha256"],
        },
        *[
            {
                "role": "target",
                "target_label": label,
                "parent_job_id": f"job-target-{index}",
                "invocation_id": f"invoke-target-{index}",
                "landscape_sha256": landscape_sha256,
                "configuration_sha256": configuration_sha256,
            }
            for index, (label, landscape_sha256, configuration_sha256) in enumerate(
                zip(
                    payload["target_labels"],
                    payload["target_landscape_sha256s"],
                    payload["target_configuration_sha256s"],
                ),
                start=1,
            )
        ],
    ]
    payload["comparison_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "comparison_sha256"}
    )
    return (
        payload,
        SimpleNamespace(parent_job_id="job-reference", invocation_id="invoke-reference"),
        SimpleNamespace(parent_job_id="job-target-1", invocation_id="invoke-target-1"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        lambda value: value["target_configuration_sha256s"].pop(),
        lambda value: value["rows"][0]["targets"].pop(),
        lambda value: value["pair_compatibility"].reverse(),
        lambda value: value["source_result_references"].__setitem__(
            2, copy.deepcopy(value["source_result_references"][1])
        ),
        lambda value: value["comparability"].__setitem__("target_count", 9),
    ],
)
async def test_multistate_persistence_rejects_cardinality_order_and_identity_tamper_before_store(
    tamper,
) -> None:
    from services.frustrampnn.contracts import canonical_sha256
    from services.frustrampnn.derived import DerivedPersistenceError, persist_comparison

    payload, reference_result, target_result = _persistable_multistate_payload()
    tamper(payload)
    payload["comparison_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "comparison_sha256"}
    )

    class NoStoreSession:
        async def get(self, *_args, **_kwargs):
            raise AssertionError("invalid multistate payload reached persistence lookup")

    with pytest.raises(DerivedPersistenceError, match="multistate|target|cardinality|order|duplicate"):
        await persist_comparison(
            NoStoreSession(),
            payload,
            reference_result=reference_result,
            target_result=target_result,
        )


def test_multistate_schema_bounds_every_target_parallel_collection() -> None:
    from services.frustrampnn.contracts import load_schema

    schema = load_schema("frustrampnn_multistate_comparison_v1")
    for field in (
        "target_landscape_sha256s",
        "target_labels",
        "target_configuration_sha256s",
        "pair_compatibility",
    ):
        assert schema["properties"][field]["maxItems"] == 8
    assert schema["properties"]["source_result_references"]["maxItems"] == 9
    assert schema["$defs"]["multiComparability"]["properties"]["pair_compatibility"][
        "maxItems"
    ] == 8
    for field in (
        "missingness_by_target",
        "targets",
        "raw_score_deltas",
        "classification_transitions",
    ):
        assert schema["$defs"]["multiRow"]["properties"][field]["maxItems"] == 8
