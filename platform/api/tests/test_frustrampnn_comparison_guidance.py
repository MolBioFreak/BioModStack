from __future__ import annotations

from datetime import datetime

import copy

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, FrustraMPNNLandscapeRow, FrustraMPNNResult, Job, get_session
from routers.frustrampnn import router
import routers.frustrampnn as frustrampnn_router
from services.frustrampnn.configuration import global_configuration


def _landscape(*, scores: dict[tuple[int, str], float | None] | None = None, config_hash: str | None = None):
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
    return {
        "schema_name": "frustrampnn_landscape",
        "schema_version": 1,
        "configuration_id": "frustrampnn_global_v1",
        "configuration_sha256": config_hash or config["configuration_sha256"],
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


def test_comparison_marks_configuration_mismatch_incompatible_without_delta():
    from services.frustrampnn.comparison import compare_landscapes

    reference = _landscape()
    target = _landscape(config_hash="f" * 64)
    result = compare_landscapes(reference, target)

    assert result["comparability"]["status"] == "incompatible"
    assert "configuration_sha256" in result["comparability"]["reasons"]
    assert all(row["raw_score_delta"] is None for row in result["rows"])


def test_comparison_marks_unmapped_residue_instead_of_position_join():
    from services.frustrampnn.comparison import compare_landscapes

    reference = _landscape()
    target = _landscape()
    target["residues"][0]["auth_seq_id"] = 99
    result = compare_landscapes(reference, target)

    rows = [row for row in result["rows"] if row["reference"]["sequence_index"] == 1]
    assert {row["mapping_state"] for row in rows} == {"unmapped"}
    assert all(row["raw_score_delta"] is None for row in rows)


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
async def test_external_candidate_handoff_api_binds_parent_and_producer_metadata(derived_session, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    captured = {}

    async def fake_create_child(session, *, selections, source_parent, trigger, **_kwargs):
        captured["selection"] = selections[0]
        captured["source_parent"] = source_parent.id
        captured["trigger"] = trigger
        return source_parent

    async def fake_receipt(session, child):
        return {"child_job_id": "child-handoff", "status": "queued"}

    monkeypatch.setattr(frustrampnn_router, "create_child_job", fake_create_child)
    monkeypatch.setattr(frustrampnn_router, "_child_job_receipt", fake_receipt)

    async def override_session():
        yield derived_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/frustrampnn/candidates/handoff",
            data={
                "candidate_id": "variant-api",
                "producer_id": "external-redesign",
                "parent_job_id": "job-derived",
                "parent_invocation_id": "invoke-derived",
                "parent_landscape_sha256": "a" * 64,
                "guidance_id": "guidance-api",
                "nucleotide_edit_set": '[{"position":17,"operation":"insert","base":"A"}]',
                "protein_sequence_sha256": "b" * 64,
            },
            files={"structure_file": ("variant-api.pdb", b"ATOM\n", "chemical/x-pdb")},
        )
    assert response.status_code == 202, response.text
    assert captured["source_parent"] == "job-derived"
    assert captured["trigger"] == "external_candidate_handoff"
    assert captured["selection"].producer_coordinates["candidate_id"] == "variant-api"
    assert captured["selection"].producer_coordinates["guidance_id"] == "guidance-api"


@pytest_asyncio.fixture
async def derived_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'derived.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        landscape = _landscape()
        session.add(Job(id="job-derived", name="derived", status="completed", model_id="frustrampnn", mode="analyze", params={}))
        session.add(FrustraMPNNResult(
            parent_job_id="job-derived", invocation_id="invoke-derived", parent_workflow_id="structure_prediction",
            candidate_id="candidate-derived", design_id=None, requiredness="required", request_sha256="1" * 64,
            source_artifact_id=None, source_artifact_sha256="2" * 64, manifest_sha256="3" * 64,
            manifest_json={}, summary_sha256="4" * 64,
            summary_json={
                "configuration_id": landscape["configuration_id"],
                "configuration_sha256": landscape["configuration_sha256"],
                "threshold_policy": landscape["threshold_policy"],
                "threshold_policy_sha256": landscape["threshold_policy_sha256"],
            },
            runtime_identity_json={}, assigned_gpu_json={}, terminal_result_json={"status": "succeeded"},
            parent_metadata_json={}, created_at=datetime(2026, 8, 2),
        ))
        for residue in landscape["residues"]:
            residue_json = {key: value for key, value in residue.items() if key != "slots"}
            for slot in residue["slots"]:
                session.add(FrustraMPNNLandscapeRow(
                    id=f"row-{residue['sequence_index']}-{slot['mutation_aa']}",
                    parent_job_id="job-derived", invocation_id="invoke-derived", target_id=landscape["target_id"],
                    entity_instance_id=residue["entity_instance_id"], auth_asym_id=residue["auth_asym_id"],
                    auth_seq_id=str(residue["auth_seq_id"]), insertion_code=residue["insertion_code"],
                    sequence_index=residue["sequence_index"], wt=residue["wt"], mutation_aa=slot["mutation_aa"],
                    score=slot["score"], score_class=slot["class"] or "neutral", scoreable=slot["scoreable"],
                    status=slot["status"], reason=slot["reason"], row_json={"residue": residue_json, "slot": slot},
                    provenance_json={"landscape_sha256": "5" * 64, "structure_map_sha256": landscape["structure_map_sha256"],
                                     "normalized_pdb_sha256": landscape["normalized_pdb_sha256"],
                                     "raw_csv_sha256": landscape["raw_csv_sha256"],
                                     "threshold_policy": landscape["threshold_policy"],
                                     "threshold_policy_sha256": landscape["threshold_policy_sha256"]},
                ))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_persisted_comparison_and_guidance_are_immutable_and_retrievable(derived_session):
    from services.frustrampnn.comparison import compare_landscapes
    from services.frustrampnn.derived import load_persisted_landscape, persist_comparison, persist_guidance_plan
    from services.frustrampnn.guidance import build_guidance_plan

    result = await derived_session.get(FrustraMPNNResult, ("job-derived", "invoke-derived"))
    landscape = await load_persisted_landscape(derived_session, result)
    assert len(landscape["residues"]) == 2
    comparison = compare_landscapes(landscape, landscape, comparison_id="cmp-derived")
    stored = await persist_comparison(derived_session, comparison, reference_result=result, target_result=result)
    assert stored.comparison_id == "cmp-derived"
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
        comparison_id = comparison["comparison_id"]
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
