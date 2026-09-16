"""Software ingress/SQLite/API evidence, not a neural or Rosetta execution test.

The real maturation producer has no bound native scalar iPTM. A positive rank
is covered by test_core_protein_rank's canonical-envelope fixtures; these tests
must not manufacture a production scalar-source binding to claim closure.
"""
import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from routers.designs import get_design, _design_to_response
from services.result_ingester import ingest_maturation_data


STRUCTURE = (
    "ATOM      1  CA  ALA H   1       0.000   0.000   0.000  1.00 10.00           C  \n"
    "ATOM      2  CA  GLY A   1       5.000   0.000   0.000  1.00 10.00           C  \n"
    "TER\nEND\n"
).encode()


def score_fixture():
    # Exact existing score_maturation output fields, with explicitly synthetic
    # physical measurements. No invocation or claim of model/runtime acceptance.
    return {
        "core_protein_scientific_contract": 1,
        "candidate_sha256": hashlib.sha256(STRUCTURE).hexdigest(),
        "objective_formula_version": "biomodstack_ppiflow_maturation_v1",
        "objective_mode": "selected_interface", "objective_score": 2.0,
        "rosetta_interface_score": -12.0, "rosetta_interface_dg": -12.0,
        "rosetta_interface_id": "H_A", "rosetta_interface_score_unit": "REU",
        "rosetta_interface_score_direction": "more_negative_is_better",
        "rosetta_interface_analyzer_used": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case,reason,rosetta_available", [
    ("native_rosetta_only", "missing_iptm_evidence", True),
    ("zero", "missing_iptm_evidence", True),
    ("ptm_only", "missing_iptm_evidence", True),
    ("pairwise_only", "missing_iptm_evidence", True),
    ("backend_only", "missing_iptm_evidence", True),
    ("unbound_scalar", "missing_iptm_evidence", True),
    ("foreign_structure", "invalid_rosetta_structure_binding", False),
    ("alias_conflict", "conflicting_rosetta_aliases", False),
    ("no_rosetta", "missing_iptm_evidence", False),
])
async def test_actual_ingress_sqlite_reopen_api(tmp_path, case, reason, rosetta_available):
    structure = tmp_path / "candidate-A.pdb"
    structure.write_bytes(STRUCTURE)
    score = score_fixture()
    confidence: dict[str, Any] = {"metric_completeness": {"ppiflow": {"paper_rank_available": True}}}
    if case == "zero":
        score.update(rosetta_interface_score=0, rosetta_interface_dg=0)
    elif case == "foreign_structure":
        score["candidate_sha256"] = hashlib.sha256(b"other candidate").hexdigest()
    elif case == "alias_conflict":
        score["rosetta_interface_dg"] = 100
    elif case == "no_rosetta":
        score.pop("rosetta_interface_score")
        score.pop("rosetta_interface_dg")
    elif case == "pairwise_only":
        confidence["pair_chains_iptm"] = [[0, 0.8], [0.8, 0]]
    elif case == "backend_only":
        confidence["validator_backend"] = "boltz2"
    elif case == "unbound_scalar":
        confidence["iptm"] = 0.8
        # A raw scalar in the scorer is equally insufficient without a producer
        # mapping to this exact structure/interface.
        score["iptm"] = 0.8
    score_path = tmp_path / "candidate-A_maturation_score.json"
    snapshot = json.dumps(score, allow_nan=False).encode()
    score_path.write_bytes(snapshot)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rank.sqlite'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            job = Job(id="marked", name="marked", model_id="maturation_child",
                      mode="maturation", status="completed", params={},
                      provenance={"core_protein_scientific_contract": 1})
            design = Design(id="design-A", job_id=job.id, name="candidate-A",
                            pdb_path=str(structure), confidence_metrics=confidence,
                            ptm=0.8 if case == "ptm_only" else None,
                            stage_family="ppiflow", stage_mode="maturation",
                            review_profile_id="ppiflow_maturation_v1")
            session.add_all([job, design])
            await session.commit()
            # Flush replaces the live value even with expire_on_commit=False.
            # Ingress must resolve this closed reference before merging rank data.
            assert design.confidence_metrics['schema'] == 'bms.scientific-artifact-row-reference.v1'
            assert await ingest_maturation_data(job.id, tmp_path, session) == 1
        async with sessions() as session:
            persisted = await session.get(Design, "design-A")
            original = deepcopy(persisted.confidence_metrics)
            assert 'schema' not in original
            for key, value in confidence.items():
                assert original[key] == value
            inputs = original["ppiflow_rank_inputs"]
            assert "validator_iptm" not in inputs
            if case != "no_rosetta":
                assert inputs["rosetta_interface_score"]["source_sha256"] == hashlib.sha256(snapshot).hexdigest()
                assert inputs["rosetta_interface_score"]["document_id"] == "primary"
            assert original["ppiflow_rank_metric"]["reason_code"] == reason
            response = await get_design("design-A", session)
            rank = next(item for item in response.metric_provenance["metrics"]
                        if item["metric_key"] == "ppiflow_paper_rank_score")
            completeness = response.metric_completeness["ppiflow"]
            assert rank["value"] is None
            assert rank["reason_code"] == reason
            assert completeness["paper_rank_available"] is False
            assert completeness["paper_rank_reason_code"] == reason
            assert completeness["rosetta_interface_score_available"] is rosetta_available
            assert response.core_protein_scientific_contract == 1
            assert persisted.confidence_metrics == original
            assert not session.dirty
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_marked_ingress_never_updates_historical_parent(tmp_path):
    structure = tmp_path / "candidate-A.pdb"
    structure.write_bytes(STRUCTURE)
    (tmp_path / "candidate-A_maturation_score.json").write_text(json.dumps(score_fixture()))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.sqlite'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            old = Job(id="old", name="old", model_id="maturation_child", mode="maturation",
                      status="completed", params={}, provenance={})
            child = Job(id="new", name="new", model_id="maturation_child", mode="maturation",
                        status="completed", parent_job_id=old.id, params={},
                        provenance={"core_protein_scientific_contract": 1})
            historical = Design(id="historical", job_id=old.id, name="candidate-A",
                                pdb_path=str(structure), confidence_metrics={"keep": "unchanged"})
            session.add_all([old, child, historical])
            await session.commit()
            assert await ingest_maturation_data(child.id, tmp_path, session) == 0
        async with sessions() as session:
            historical = await session.get(Design, "historical")
            assert historical.confidence_metrics == {"keep": "unchanged"}
            assert historical.ppiflow_objective_score is None
            child = await session.get(Job, "new")
            with pytest.raises(ValueError, match="owning Job"):
                _design_to_response(historical, job=child)
    finally:
        await engine.dispose()
