from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingRecord, Job
from services.conformational_mapping.contracts import AA_ORDER, canonical_json_bytes, validate_schema
from services.conformational_mapping.persistence import (
    ConformationalPersistenceError,
    persist_derived_record,
    register_prepared_request,
)
from services.conformational_mapping import state_landscape_analysis
from services.conformational_mapping.state_landscape_analysis import (
    StateLandscapeAnalysisError,
    derive_state_landscape_analysis,
)


def _slot(aa: str, *, wt: str, score: float) -> dict:
    return {
        "wt": wt,
        "mutation_aa": aa,
        "score": score,
        "class": "high" if score <= -1.0 else "minimally_frustrated" if score >= 0.58 else "neutral",
        "scoreable": True,
        "status": "ok",
        "reason": None,
        "native": aa == wt,
    }


def _map(candidate_id: str) -> dict:
    return {
        "schema_name": "cm_structure_map",
        "schema_version": 1,
        "target_id": "target-a",
        "candidate_id": candidate_id,
        "original_cif_sha256": "a" * 64,
        "source_format": "mmcif",
        "source_sha256": "a" * 64,
        "source_bytes": 1,
        "normalized_pdb_sha256": "b" * 64,
        "selected_source_model": 1,
        "altloc_policy": "blank_or_explicit:A",
        "normalizer_version": "cm_structure_normalizer_v1",
        "rows": [{
            "entity_instance_id": "protein-copy-1",
            "source_entity_id": "protein",
            "source_model": 1,
            "label_asym_id": "A",
            "auth_asym_id": "A",
            "label_seq_id": 7,
            "auth_seq_id": 42,
            "insertion_code": "",
            "residue_name": "ALA",
            "sequence_index": 7,
            "pdb_chain_id": "A",
            "pdb_residue_id": 42,
            "pdb_insertion_code": "",
            "backbone_atoms": {"N": "N", "CA": "CA", "C": "C", "O": "O"},
            "selected_altloc": "",
            "model_decision": "selected_model_1",
            "status": "mapped",
            "reason": None,
        }],
    }


def _landscape(candidate_id: str, *, native: float, high_c: float, max_v: float) -> dict:
    scores = {aa: 0.0 for aa in AA_ORDER}
    scores.update({"A": native, "C": high_c, "V": max_v})
    return {
        "schema_name": "cm_frustration_landscape",
        "schema_version": 1,
        "target_id": "target-a",
        "candidate_id": candidate_id,
        "raw_csv_sha256": ("c" if candidate_id == "state-a" else "d") * 64,
        "checkpoint_id": "frustrampnn-test",
        "checkpoint_sha256": "e" * 64,
        "tool_id": "frustrampnn",
        "tool_sha256": "f" * 64,
        "threshold_policy_id": "frustrampnn_class_v1",
        "threshold_policy_sha256": "1" * 64,
        "input_issues": [],
        "residues": [{
            "entity_instance_id": "protein-copy-1",
            "auth_asym_id": "A",
            "auth_seq_id": 42,
            "insertion_code": "",
            "sequence_index": 7,
            "wt": "A",
            "slots": [_slot(aa, wt="A", score=scores[aa]) for aa in AA_ORDER],
        }],
    }


def _sources() -> tuple[dict, list[dict], list[dict]]:
    ensemble = {
        "schema_name": "cm_ensemble", "schema_version": 1, "request_id": "request-a",
        "candidates": [
            {"candidate_id": "state-b", "backend_coordinates": {
                "backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 202, "sample_index": 0,
            }},
            {"candidate_id": "state-a", "backend_coordinates": {
                "backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 101, "sample_index": 0,
            }},
            {"candidate_id": "resampling-mutant", "backend_coordinates": {
                "backend": "protenix_v2_ensemble", "target_id": "target-mutant", "ordered_seed": 101, "sample_index": 0,
            }},
        ],
    }
    maps = [_map("state-a"), _map("state-b")]
    landscapes = [
        _landscape("state-a", native=-0.5, high_c=-1.2, max_v=1.5),
        _landscape("state-b", native=-0.25, high_c=-0.5, max_v=2.75),
    ]
    return ensemble, maps, landscapes


def _pairwise() -> dict:
    return {
        "mode": "pairwise",
        "comparison_target_id": "target-a",
        "comparison_scope": "all_within_target",
        "reference_backend_coordinates": None,
        "reference_candidate_id": None,
        "resolved_pairs": [
            {"pair_id": "state-a__state-b", "candidate_a_id": "state-a", "candidate_b_id": "state-b"},
        ],
    }


def test_cm_state_000_explicit_pairwise_authority_ignores_resampling_shaped_cross_target_candidates() -> None:
    ensemble, _, _ = _sources()
    resolver = getattr(state_landscape_analysis, "resolve_state_landscape_comparison", None)

    assert callable(resolver), "explicit state-landscape comparison resolver is required"
    assert resolver(ensemble, {
        "mode": "pairwise", "target_id": "target-a", "scope": "all_within_target",
    }) == {
        "mode": "pairwise",
        "comparison_target_id": "target-a",
        "comparison_scope": "all_within_target",
        "reference_backend_coordinates": None,
        "reference_candidate_id": None,
        "resolved_pairs": [{
            "pair_id": "state-a__state-b", "candidate_a_id": "state-a", "candidate_b_id": "state-b",
        }],
    }


def test_cm_state_000b_explicit_reference_authority_uses_full_coordinate_selector_and_stable_order() -> None:
    ensemble, _, _ = _sources()
    ensemble["candidates"].append({
        "candidate_id": "state-c", "backend_coordinates": {
            "backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 303, "sample_index": 0,
        },
    })
    resolver = getattr(state_landscape_analysis, "resolve_state_landscape_comparison", None)

    assert callable(resolver), "explicit state-landscape comparison resolver is required"
    assert resolver(ensemble, {
        "mode": "reference", "target_id": "target-a", "scope": "all_other_within_target",
        "reference_backend_coordinates": {
            "backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 101, "sample_index": 0,
        },
    }) == {
        "mode": "reference",
        "comparison_target_id": "target-a",
        "comparison_scope": "all_other_within_target",
        "reference_backend_coordinates": {
            "backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 101, "sample_index": 0,
        },
        "reference_candidate_id": "state-a",
        "resolved_pairs": [
            {"pair_id": "state-a__state-b", "candidate_a_id": "state-a", "candidate_b_id": "state-b"},
            {"pair_id": "state-a__state-c", "candidate_a_id": "state-a", "candidate_b_id": "state-c"},
        ],
    }


@pytest.mark.parametrize("authority, message", [
    ({"mode": "reference", "target_id": "target-a", "scope": "all_other_within_target", "reference_backend_coordinates": {"backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 999, "sample_index": 0}}, "absent"),
    ({"mode": "reference", "target_id": "target-a", "scope": "all_other_within_target", "reference_backend_coordinates": {"backend": "protenix_v2_ensemble", "target_id": "target-a", "ordered_seed": 101, "sample_index": 0}}, "ambiguous"),
])
def test_cm_state_000c_reference_authority_fails_closed_without_one_resolved_reference(authority: dict, message: str) -> None:
    ensemble, _, _ = _sources()
    if message == "ambiguous":
        ensemble["candidates"].append({
            "candidate_id": "duplicate-reference", "backend_coordinates": dict(authority["reference_backend_coordinates"]),
        })
    resolver = getattr(state_landscape_analysis, "resolve_state_landscape_comparison", None)

    assert callable(resolver), "explicit state-landscape comparison resolver is required"
    with pytest.raises(StateLandscapeAnalysisError, match=message):
        resolver(ensemble, authority)


def test_cm_state_001_explicit_pair_derives_deterministic_metrics() -> None:
    ensemble, maps, landscapes = _sources()

    artifact = derive_state_landscape_analysis(
        ensemble, landscapes, maps, comparison=_pairwise(),
    )

    validate_schema("cm_state_landscape_analysis_v1", artifact)
    row = artifact["rows"][0]
    assert row["pair_id"] == "state-a__state-b"
    assert row["identity"] == {
        "target_id": "target-a", "entity_instance_id": "protein-copy-1",
        "auth_asym_id": "A", "auth_seq_id": 42, "insertion_code": "",
        "sequence_index": 7, "validated_wt": "A",
    }
    assert row["metrics"]["native_score"] == {
        "a": -0.5, "b": -0.25, "delta_b_minus_a": 0.25, "status": "ok", "reason": None,
    }
    assert row["metrics"]["high_non_native_highly_frustrated_fraction"] == {
        "a": pytest.approx(1 / 19), "b": 0.0, "delta_b_minus_a": pytest.approx(-1 / 19),
        "status": "ok", "reason": None,
    }
    assert row["metrics"]["maximum_non_native_substitution_delta_relative_to_native"] == {
        "a": 2.0, "b": 3.0, "delta_b_minus_a": 1.0, "status": "ok", "reason": None,
    }
    assert row["metrics"]["native_class"] == {
        "a": "neutral", "b": "neutral", "transition": "neutral_to_neutral", "status": "ok", "reason": None,
    }
    assert artifact == derive_state_landscape_analysis(
        ensemble, list(reversed(landscapes)), list(reversed(maps)), comparison=_pairwise(),
    )
    assert canonical_json_bytes(artifact) == canonical_json_bytes(
        derive_state_landscape_analysis(ensemble, landscapes, maps, comparison=_pairwise())
    )


def test_cm_state_001a_pairwise_artifact_retains_authority_and_resolved_pair_ledger_with_no_rows() -> None:
    ensemble, maps, landscapes = _sources()

    artifact = derive_state_landscape_analysis(
        ensemble, [landscapes[0]], maps, comparison=_pairwise(),
    )

    assert artifact["comparison_target_id"] == "target-a"
    assert artifact["comparison_scope"] == "all_within_target"
    assert artifact["reference_backend_coordinates"] is None
    assert artifact["resolved_pairs"] == _pairwise()["resolved_pairs"]
    assert artifact["rows"] == []
    assert artifact["support_ledger"][0]["pair_id"] == artifact["resolved_pairs"][0]["pair_id"]


def test_cm_state_001b_reference_artifact_retains_full_selector_and_resolved_pair_ledger() -> None:
    ensemble, maps, landscapes = _sources()
    selector = {
        "backend": "protenix_v2_ensemble",
        "target_id": "target-a",
        "ordered_seed": 101,
        "sample_index": 0,
    }
    comparison = {
        "mode": "reference",
        "comparison_target_id": "target-a",
        "comparison_scope": "all_other_within_target",
        "reference_backend_coordinates": selector,
        "reference_candidate_id": "state-a",
        "resolved_pairs": [
            {"pair_id": "state-a__state-b", "candidate_a_id": "state-a", "candidate_b_id": "state-b"},
        ],
    }

    artifact = derive_state_landscape_analysis(ensemble, landscapes, maps, comparison=comparison)

    assert artifact["comparison_mode"] == "reference"
    assert artifact["comparison_target_id"] == "target-a"
    assert artifact["comparison_scope"] == "all_other_within_target"
    assert artifact["reference_backend_coordinates"] == selector
    assert artifact["resolved_pairs"] == comparison["resolved_pairs"]
    assert artifact["analysis_id"] != derive_state_landscape_analysis(
        ensemble,
        landscapes,
        maps,
        comparison=_pairwise(),
    )["analysis_id"]


def test_cm_state_001c_request_gate_requires_dedicated_authority_and_ignores_resampling_shape() -> None:
    ensemble, maps, landscapes = _sources()
    gate = getattr(state_landscape_analysis, "derive_state_landscape_analysis_for_request", None)

    assert callable(gate), "request gate must be a pure state-analysis boundary"
    assert gate({"resampling_settings": {"ordered_seeds": [101]}}, ensemble, landscapes, maps) is None
    artifact = gate(
        {
            "state_landscape_comparison": {
                "mode": "pairwise",
                "target_id": "target-a",
                "scope": "all_within_target",
            }
        },
        ensemble,
        landscapes,
        maps,
    )
    assert artifact is not None
    assert artifact["resolved_pairs"] == _pairwise()["resolved_pairs"]


@pytest.mark.parametrize("mutation", ["wt", "provenance"])
def test_cm_state_002_mismatch_records_exclusion_without_fabricated_delta(mutation: str) -> None:
    ensemble, maps, landscapes = _sources()
    altered = copy.deepcopy(landscapes)
    if mutation == "wt":
        altered[1]["residues"][0]["wt"] = "C"
        for slot in altered[1]["residues"][0]["slots"]:
            slot["wt"] = "C"
            slot["native"] = slot["mutation_aa"] == "C"
    else:
        altered[1]["tool_sha256"] = "9" * 64

    artifact = derive_state_landscape_analysis(ensemble, altered, maps, comparison=_pairwise())

    row = artifact["rows"][0]
    assert row["metrics"]["native_score"]["delta_b_minus_a"] is None
    assert row["metrics"]["native_score"]["status"] == "unavailable"
    assert {entry["reason"] for entry in artifact["exclusion_ledger"]} == {
        "wt_mismatch" if mutation == "wt" else "provenance_mismatch"
    }


def test_cm_state_003_missing_candidate_analysis_is_explicit_and_not_a_reference_guess() -> None:
    ensemble, maps, landscapes = _sources()

    artifact = derive_state_landscape_analysis(
        ensemble, [landscapes[0]], maps, comparison=_pairwise(),
    )

    assert artifact["rows"] == []
    assert artifact["exclusion_ledger"] == [{
        "pair_id": "state-a__state-b", "candidate_a_id": "state-a", "candidate_b_id": "state-b",
        "identity": None, "reason": "candidate_analysis_unavailable",
        "detail": "candidate has no canonical landscape",
    }]
    with pytest.raises(StateLandscapeAnalysisError, match="comparison target"):
        derive_state_landscape_analysis(
            ensemble, landscapes, maps, comparison={"mode": "reference", "candidate_ids": ["state-a", "state-b"]},
        )


async def _session(tmp_path: Path) -> tuple[AsyncSession, object]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cm-state.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)(), engine


@pytest.mark.asyncio
async def test_cm_state_004_persistence_validates_and_records_immutable_artifact(tmp_path: Path) -> None:
    session, engine = await _session(tmp_path)
    try:
        request = {"request_id": "request-state", "request_sha256": "a" * 64, "backend": "protenix_v2_ensemble"}
        plan = {"coordinate_plan_sha256": "b" * 64, "expected_cardinality": 1, "coordinates": [{"target_id": "target-a"}]}
        record = await register_prepared_request(
            session,
            job=Job(id="job-state", name="cm-state", model_id="conformational_mapping", mode="map", status="queued", params={}, created_at=datetime.utcnow()),
            principal_id="alice", request=request, coordinate_plan=plan, resume_key="0" * 64, capability_sha256="c" * 64,
        )
        ensemble, maps, landscapes = _sources()
        artifact = derive_state_landscape_analysis(ensemble, landscapes, maps, comparison=_pairwise())
        await persist_derived_record(
            session, record.request_id, record_type="state_landscape_analysis", record_key=artifact["analysis_id"], payload=artifact,
        )
        malformed = copy.deepcopy(artifact)
        malformed["source_landscape_sha256"] = "not-a-sha256"
        with pytest.raises(ConformationalPersistenceError):
            await persist_derived_record(
                session, record.request_id, record_type="state_landscape_analysis", record_key="invalid", payload=malformed,
            )
        await session.commit()
        stored = await session.scalar(select(ConformationalMappingRecord).where(ConformationalMappingRecord.record_type == "state_landscape_analysis"))
        assert stored is not None and stored.payload_json == artifact
    finally:
        await session.close()
        await engine.dispose()
