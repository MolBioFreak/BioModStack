from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job, ShapeCadSource, ShapeDesignGeometry, ShapeDesignRequest
from services.result_state_integrity import finalize_successful_job


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _session_factory(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shape-results.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


async def _register(session: AsyncSession, job_id: str, output: Path) -> Job:
    request_id = "request_shape_0000000000000000000000000001"
    geometry_id = "geom_" + "1" * 32
    geometry_sha = "3" * 64
    point_sha = "4" * 64
    sdf_sha = "7" * 64
    request_spec = {
        "request_id": request_id,
        "geometry_id": geometry_id,
        "geometry_sha256": geometry_sha,
        "point_pool_sha256": point_sha,
        "sdf_sha256": sdf_sha,
        "sdf_sign": "positive_inside",
    }
    request_sha = _sha(json.dumps(request_spec, sort_keys=True, separators=(",", ":")).encode())
    session.add(
        ShapeCadSource(
            source_id="cad_" + "5" * 32,
            source_sha256="5" * 64,
            size_bytes=1,
            original_filename="shape.obj",
            relative_path="shape/source.obj",
        )
    )
    session.add(
        ShapeDesignGeometry(
            geometry_id=geometry_id,
            source_id="cad_" + "5" * 32,
            geometry_sha256=geometry_sha,
            conversion_sha256="6" * 64,
            angstrom_per_unit=1.0,
            vertex_count=4,
            face_count=4,
            point_count=4,
            manifest={
                "point_pool_sha256": point_sha,
                "sdf_sha256": sdf_sha,
                "sdf_sign": "positive_inside",
            },
            artifacts={},
        )
    )
    job = Job(
        id=job_id,
        name="shape",
        model_id="protein_modification_experimental",
        mode="shape_blueprint",
        params={
            "shape_request_id": request_id,
            "shape_request_sha256": request_sha,
            "shape_geometry_id": geometry_id,
            "shape_geometry_sha256": geometry_sha,
            "shape_point_pool_sha256": point_sha,
            "result_integrity_requires_designs": True,
        },
        status="running",
        queue_status="running",
        output_dir=str(output),
        created_at=datetime.utcnow(),
        awaiting_input=False,
        awaiting_payload={},
        retry_count=0,
        max_retries=0,
    )
    session.add(job)
    session.add(
        ShapeDesignRequest(
            request_id=request_id,
            geometry_id=geometry_id,
            request_sha256=request_sha,
            request_spec={**request_spec, "request_sha256": request_sha},
            stage_relative_path="shape/stage",
            job_id=job_id,
        )
    )
    await session.commit()
    return job


def _manifest(job_id: str, *, outcome: str, candidates: list[dict], reason=None) -> dict:
    request_spec = {
        "request_id": "request_shape_0000000000000000000000000001",
        "geometry_id": "geom_" + "1" * 32,
        "geometry_sha256": "3" * 64,
        "point_pool_sha256": "4" * 64,
        "sdf_sha256": "7" * 64,
        "sdf_sign": "positive_inside",
    }
    request_sha = _sha(json.dumps(request_spec, sort_keys=True, separators=(",", ":")).encode())
    return {
        "schema": "bms_shape_result_v1",
        "outcome": outcome,
        "job_id": job_id,
        **request_spec,
        "request_sha256": request_sha,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "reason": reason,
    }


@pytest.mark.asyncio
async def test_shape_candidate_manifest_creates_hash_bound_design(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output = tmp_path / "output"
    candidate_dir = output / "results" / "shape_candidates"
    candidate_dir.mkdir(parents=True)
    structure = candidate_dir / "shape_candidate_0001.cif"
    metrics = candidate_dir / "shape_candidate_0001.metrics.json"
    structure.write_bytes(b"data_shape\n")
    metrics.write_text(json.dumps({
        "schema": "bms_shape_candidate_metrics_v1",
        "candidate_id": "shape_candidate_0001",
        "geometry_sha256": "3" * 64,
        "point_pool_sha256": "4" * 64,
        "sdf_sha256": "7" * 64,
        "shape_total": 1.5,
    }))
    candidate = {
        "candidate_id": "shape_candidate_0001",
        "name": "shape_candidate_0001",
        "structure": {
            "relative_path": "results/shape_candidates/shape_candidate_0001.cif",
            "format": "cif",
            "sha256": _sha(structure.read_bytes()),
            "bytes": structure.stat().st_size,
        },
        "metrics": {
            "relative_path": "results/shape_candidates/shape_candidate_0001.metrics.json",
            "sha256": _sha(metrics.read_bytes()),
            "bytes": metrics.stat().st_size,
        },
        "provenance": {"sequence_engine": "proteinmpnn", "predictor": "esmfold2"},
    }
    manifest_path = output / "results" / "shape_result_manifest.json"
    manifest_path.write_text(json.dumps(_manifest("job-shape-candidates", outcome="candidates", candidates=[candidate])))
    async with factory() as session:
        job = await _register(session, "job-shape-candidates", output)
        result = await finalize_successful_job(job, str(output), session)
        assert result.completed is True
        designs = (await session.execute(select(Design).where(Design.job_id == job.id))).scalars().all()
        assert len(designs) == 1
        assert designs[0].pdb_path == str(structure.resolve())
        assert designs[0].json_path == str(metrics.resolve())
        assert designs[0].stage_family == "shape_blueprint"
        assert designs[0].artifact_class == "shape_candidate"
        assert designs[0].provenance["geometry_sha256"] == "3" * 64
    await engine.dispose()


@pytest.mark.asyncio
async def test_shape_no_candidates_preserves_zero_yield_terminal_truth(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output = tmp_path / "output"
    manifest_path = output / "results" / "shape_result_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            _manifest(
                "job-shape-empty",
                outcome="no_candidates",
                candidates=[],
                reason={"code": "all_candidates_rejected", "message": "all refolded candidates failed declared metrics"},
            )
        )
    )
    async with factory() as session:
        job = await _register(session, "job-shape-empty", output)
        result = await finalize_successful_job(job, str(output), session)
        await session.refresh(job)
        assert result.completed is False
        assert result.integrity_state == "no_candidates"
        assert job.status == "failed"
        assert job.provenance["result_integrity"]["state"] == "no_candidates"
        assert job.provenance["result_integrity"]["reason"]["code"] == "all_candidates_rejected"
        assert (await session.execute(select(Design).where(Design.job_id == job.id))).scalars().all() == []
    await engine.dispose()
