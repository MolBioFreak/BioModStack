from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, FrustraMPNNLandscapeRow, FrustraMPNNResult, Job, get_session
from routers.frustrampnn import router

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


@pytest_asyncio.fixture
async def analytics_api(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'multidimensional.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        for index in range(250):
            job_id = f"dataset-{index:03d}"
            design_id = f"design-{index:03d}"
            invocation_id = f"invocation-{index:03d}"
            session.add(Job(id=job_id, name=f"nanobody-batch-{index:03d}", status="completed", queue_status="completed", model_id="protein_design", mode="design", params={"workflow_family": "de_novo_nanobody"}, output_dir=str(tmp_path)))
            session.add(Design(id=design_id, job_id=job_id, name=f"Nb-{index:03d}", pdb_path=str(tmp_path / f"{design_id}.pdb")))
            session.add(FrustraMPNNResult(
                parent_job_id=job_id,
                invocation_id=invocation_id,
                parent_workflow_id="protein_design",
                candidate_id=f"candidate-{index:03d}",
                design_id=design_id,
                requiredness="required",
                request_sha256=f"{index:064x}",
                source_artifact_id=design_id,
                source_artifact_sha256=f"{index + 1:064x}",
                manifest_sha256=f"{index + 2:064x}",
                manifest_json={"contract_version": "frustrampnn_manifest_v1"},
                summary_sha256=f"{index + 3:064x}",
                summary_json={"threshold_policy": {"policy_id": "frustrampnn_class_v1", "high_max": -1.0, "minimal_min": 0.58}},
                runtime_identity_json={"checkpoint_sha256": f"{index + 4:064x}"},
                assigned_gpu_json={"physical_device_id": index % 4},
                terminal_result_json={"status": "succeeded"},
                parent_metadata_json={"workflow_family": "de_novo_nanobody", "dataset_label": f"batch-{index // 50}"},
                created_at=datetime(2026, 8, 2),
            ))
            for mutation_index, mutation in enumerate(AMINO_ACIDS):
                score = -2.0 + mutation_index * 0.2 + index * 0.001
                score_class = "high" if score <= -1.0 else "minimal" if score >= 0.58 else "neutral"
                session.add(FrustraMPNNLandscapeRow(
                    id=f"row-{index:03d}-{mutation}", parent_job_id=job_id, invocation_id=invocation_id,
                    target_id="nanobody", entity_instance_id="heavy-chain", auth_asym_id="H", auth_seq_id="27",
                    insertion_code="A", sequence_index=26, wt="A", mutation_aa=mutation, score=score,
                    score_class=score_class, scoreable=True, status="ok", reason=None,
                    row_json={"score": score, "class": score_class}, provenance_json={"source": "fixture"},
                ))
        await session.commit()

    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with sessions() as session:
            async def forbidden_commit():
                raise AssertionError("GET endpoint attempted a database commit")
            session.commit = forbidden_commit  # type: ignore[method-assign]
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await engine.dispose()


@pytest.mark.asyncio
async def test_result_level_points_are_bounded_traceable_and_machine_described(analytics_api):
    response = await analytics_api.get("/api/frustrampnn/analytics/points", params={"level": "result", "limit": 200})
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "frustrampnn_multidimensional_v1"
    assert body["level"] == "result"
    assert body["total"] == 250
    assert len(body["items"]) == 200
    assert body["next_offset"] == 200
    assert {dimension["id"] for dimension in body["dimensions"]} >= {"mean_score", "native_score", "high_fraction", "minimal_fraction", "scoreable_fraction"}
    first = body["items"][0]
    assert first["point_id"] == "dataset-000:invocation-000"
    assert first["dataset_id"] == "dataset-000"
    assert first["workflow_family"] == "de_novo_nanobody"
    assert first["job_id"] == "dataset-000"
    assert first["design_id"] == "design-000"
    assert first["candidate_id"] == "candidate-000"
    assert first["source_artifact_sha256"] == f"{1:064x}"
    assert first["checkpoint_sha256"] == f"{4:064x}"
    assert first["metrics"]["slot_count"] == 20
    assert first["metrics"]["residue_count"] == 1
    assert first["metrics"]["native_score"] == -2.0


@pytest.mark.asyncio
async def test_dataset_filter_and_monotonic_page_do_not_require_per_result_requests(analytics_api):
    response = await analytics_api.get("/api/frustrampnn/analytics/points", params={"level": "result", "dataset_ids": "dataset-100,dataset-101,dataset-102", "limit": 2, "offset": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["dataset_id"] for item in body["items"]] == ["dataset-101", "dataset-102"]
    assert body["next_offset"] is None


@pytest.mark.asyncio
async def test_mutation_and_residue_levels_preserve_exact_multidimensional_identity(analytics_api):
    mutation = await analytics_api.get("/api/frustrampnn/analytics/points", params={"level": "mutation", "dataset_ids": "dataset-007", "limit": 100})
    assert mutation.status_code == 200
    mutation_body = mutation.json()
    assert mutation_body["total"] == 20
    assert {item["mutation_aa"] for item in mutation_body["items"]} == set(AMINO_ACIDS)
    assert all(item["auth_asym_id"] == "H" and item["auth_seq_id"] == "27" and item["insertion_code"] == "A" for item in mutation_body["items"])

    residue = await analytics_api.get("/api/frustrampnn/analytics/points", params={"level": "residue", "dataset_ids": "dataset-007", "limit": 100})
    assert residue.status_code == 200
    point = residue.json()["items"][0]
    assert point["point_id"] == "dataset-007:invocation-007:nanobody:heavy-chain:H:27:A:26"
    assert point["metrics"]["alternative_count"] == 19
    assert point["metrics"]["native_score"] is not None
    assert point["metrics"]["best_alternative_delta"] > 0
    assert point["metrics"]["worst_alternative_delta"] > 0


@pytest.mark.asyncio
async def test_multidimensional_api_rejects_unbounded_or_ambiguous_requests(analytics_api):
    oversized = await analytics_api.get("/api/frustrampnn/analytics/points", params={"level": "result", "limit": 5001})
    assert oversized.status_code == 422
    residue_without_dataset = await analytics_api.get("/api/frustrampnn/analytics/points", params={"level": "residue"})
    assert residue_without_dataset.status_code == 422
    too_many_datasets = await analytics_api.get("/api/frustrampnn/analytics/points", params={"dataset_ids": ",".join(f"dataset-{index:03d}" for index in range(21))})
    assert too_many_datasets.status_code == 422
