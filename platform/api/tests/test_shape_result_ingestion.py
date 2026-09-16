from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job, ShapeCadSource, ShapeDesignGeometry, ShapeDesignRequest
from services.result_ingester import _ingest_shape_result_manifest
from services.result_state_integrity import finalize_successful_job


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _session_factory(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shape-results.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


def _request_spec() -> dict:
    return {
        "schema": "bms_shape_design_request_v2",
        "request_id": "request_shape_0000000000000000000000000001",
        "geometry_id": "geom_" + "1" * 32,
        "geometry_sha256": "3" * 64,
        "point_pool_sha256": "4" * 64,
        "sdf_sha256": "7" * 64,
        "sdf_sign": "positive_inside",
        "num_backbones": 1,
        "length_policy": {"mode": "fixed", "min": 40, "max": 40},
        "seed": 0,
        "sequence_policy": "auto",
        "sequence_engine": "proteinmpnn",
        "sequences_per_backbone": 1,
        "validator_suite": ["esmfold2"],
    }


async def _register(session: AsyncSession, job_id: str, output: Path) -> Job:
    request_id = "request_shape_0000000000000000000000000001"
    geometry_id = "geom_" + "1" * 32
    geometry_sha = "3" * 64
    point_sha = "4" * 64
    sdf_sha = "7" * 64
    request_spec = _request_spec()
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


def _manifest(job_id: str, output: Path, *, outcome: str, candidates: list[dict], reason=None) -> dict:
    # Generated test evidence uses the current native plan/aggregate authorities;
    # the old fixture omitted the entire terminal closure and validator inventory.
    from scripts.shape_blueprint.plan_rfd3_batches import plan_batches
    from scripts.shape_blueprint.build_rfd3_aggregate import build_aggregate
    from scripts.shape_blueprint.build_shape_result import _artifact

    request_spec = _request_spec()
    request_sha = _sha(json.dumps(request_spec, sort_keys=True, separators=(",", ":")).encode())
    plan = plan_batches({**request_spec, "request_sha256": request_sha}, gpu_memory_gib=32)
    backbone_id = plan["batches"][0]["candidates"][0]["candidate_id"]
    plan_path = output / "run/shape_batches/rfd3_batch_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan))
    aggregate = build_aggregate(plan=plan, admission_records=[{
        "candidate_id": backbone_id,
        "status": "accepted" if candidates else "rejected",
        "reason": reason,
    }])
    aggregate_path = output / "results/rfd3_aggregate_manifest.json"
    aggregate_path.write_text(json.dumps(aggregate))
    for candidate in candidates:
        sequence_name = f"{backbone_id}__proteinmpnn__001"
        candidate["provenance"]["sequence_name"] = sequence_name
        native = candidate["structure"]
        native_path = Path(native["relative_path"]).name
        suite_path = output / "results/shape_candidates" / f"{candidate['candidate_id']}.validators.json"
        suite_path.write_text(json.dumps({
            "schema": "bms_shape_validator_suite_v1", "sequence_name": sequence_name,
            "validators": request_spec["validator_suite"],
            "records": {"esmfold2": {"artifacts": [{
                "filename": native_path, "sha256": native["sha256"], "bytes": native["bytes"],
            }]}},
        }))
        candidate["validator_evidence"] = _artifact(suite_path, output, "json")
        candidate["validator_artifacts"] = [{**native, "validator": "esmfold2", "native_path": native_path}]
    return {
        "schema": "bms_shape_result_v1",
        "outcome": outcome,
        "job_id": job_id,
        **{key: request_spec[key] for key in (
            "request_id", "geometry_id", "geometry_sha256", "point_pool_sha256", "sdf_sha256", "sdf_sign",
        )},
        "request_sha256": request_sha,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "rejected_count": 0,
        "rejections": [],
        "rfd3_aggregate": _artifact(aggregate_path, output),
        "rfd3_aggregate_status": aggregate["status"],
        "reason": reason,
    }


@pytest.mark.asyncio
async def test_shape_candidate_manifest_creates_hash_bound_design(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output = tmp_path / "output"
    candidate_dir = output / "results" / "shape_candidates"
    candidate_dir.mkdir(parents=True)
    structure = candidate_dir / "shape_candidate_0001.cif"
    source = candidate_dir / "shape_candidate_0001.source.pdb"
    metrics = candidate_dir / "shape_candidate_0001.metrics.json"
    structure.write_bytes(b"data_shape\n")
    source.write_bytes(b"ATOM source\n")
    metrics.write_text(json.dumps({
        "schema": "bms_shape_candidate_metrics_v1",
        "candidate_id": "shape_candidate_0001",
        "geometry_sha256": "3" * 64,
        "point_pool_sha256": "4" * 64,
        "sdf_sha256": "7" * 64,
        "source_backbone_sha256": _sha(source.read_bytes()),
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
        "source_backbone": {
            "relative_path": "results/shape_candidates/shape_candidate_0001.source.pdb",
            "format": "pdb",
            "sha256": _sha(source.read_bytes()),
            "bytes": source.stat().st_size,
        },
        "metrics": {
            "relative_path": "results/shape_candidates/shape_candidate_0001.metrics.json",
            "sha256": _sha(metrics.read_bytes()),
            "bytes": metrics.stat().st_size,
        },
        "provenance": {"sequence_engine": "proteinmpnn", "predictor": "esmfold2"},
    }
    manifest_path = output / "results" / "shape_result_manifest.json"
    manifest_path.write_text(json.dumps(_manifest("job-shape-candidates", output, outcome="candidates", candidates=[candidate])))
    async with factory() as session:
        job = await _register(session, "job-shape-candidates", output)
        declared = json.loads(manifest_path.read_text())
        for missing, error in (("rfd3_aggregate", "requires native RFD3 aggregate"),
                               ("validator_evidence", "validator suite")):
            damaged = json.loads(json.dumps(declared))
            if missing == "rfd3_aggregate":
                damaged.pop(missing)
            else:
                damaged["candidates"][0].pop(missing)
            manifest_path.write_text(json.dumps(damaged))
            with pytest.raises(RuntimeError, match=error):
                await _ingest_shape_result_manifest(job, output, session, commit=False)
            assert not session.new
        manifest_path.write_text(json.dumps(declared))
        result = await finalize_successful_job(job, str(output), session)
        assert result.completed is True, job.error_message
        designs = (await session.execute(select(Design).where(Design.job_id == job.id))).scalars().all()
        assert len(designs) == 1
        assert designs[0].pdb_path == str(structure.resolve())
        assert designs[0].source_pdb_path == str(source.resolve())
        assert designs[0].json_path == str(metrics.resolve())
        assert designs[0].stage_family == "shape_blueprint"
        assert designs[0].artifact_class == "shape_candidate"
        assert designs[0].provenance["geometry_sha256"] == "3" * 64
        assert designs[0].review_artifact_manifest["schema"] == "bms.review-artifacts.v1"
        assert await _ingest_shape_result_manifest(job, output, session, commit=False) == 0
        normalized_manifest = designs[0].review_artifact_manifest
        malformed_manifest = {
            "schema": "bms.review-artifacts.v1",
            "artifacts": {
                "structure": dict(candidate["structure"]),
                "source_backbone": dict(candidate["source_backbone"]),
                "metrics": dict(candidate["metrics"]),
            },
        }
        await session.execute(
            update(Design)
            .where(Design.id == designs[0].id)
            .values(review_artifact_manifest=malformed_manifest)
        )
        await session.flush()
        session.expire(designs[0], ["review_artifact_manifest"])
        with pytest.raises(RuntimeError, match="conflicts with an existing Design"):
            await _ingest_shape_result_manifest(job, output, session, commit=False)
        await session.execute(
            update(Design)
            .where(Design.id == designs[0].id)
            .values(review_artifact_manifest=normalized_manifest)
        )
        await session.flush()
        session.expire(designs[0], ["review_artifact_manifest"])
        substituted_metrics = json.loads(metrics.read_text())
        substituted_metrics["source_backbone_sha256"] = "9" * 64
        metrics.write_text(json.dumps(substituted_metrics))
        candidate["metrics"]["sha256"] = _sha(metrics.read_bytes())
        candidate["metrics"]["bytes"] = metrics.stat().st_size
        manifest_path.write_text(
            json.dumps(_manifest("job-shape-candidates", output, outcome="candidates", candidates=[candidate]))
        )
        with pytest.raises(RuntimeError, match="Shape terminal metrics identity mismatch"):
            await _ingest_shape_result_manifest(job, output, session, commit=False)
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
                "job-shape-empty", output,
                outcome="no_candidates",
                candidates=[],
                reason={"code": "no_refolded_candidates", "message": "all upstream scientific stages completed but produced no terminal candidates"},
            )
        )
    )
    async with factory() as session:
        job = await _register(session, "job-shape-empty", output)
        # Stand in for the caller's already-written generation custody. Validated
        # zero-yield must commit it together with the terminal scientific failure.
        await session.execute(update(Job).where(Job.id == job.id).values(name="generation received"))
        result = await finalize_successful_job(job, str(output), session)
        await session.refresh(job)
        assert result.completed is False
        assert result.integrity_state == "no_candidates", job.error_message
        assert job.status == "failed"
        assert job.provenance["result_integrity"]["state"] == "no_candidates"
        assert job.provenance["result_integrity"]["reason"]["code"] == "no_refolded_candidates"
        assert job.provenance["shape_result_publication"]["candidate_ids"] == []
        assert (await session.execute(select(Design).where(Design.job_id == job.id))).scalars().all() == []
    async with factory() as observer:
        persisted = await observer.get(Job, "job-shape-empty")
        assert persisted.name == "generation received"
        assert persisted.status == "failed"
        assert persisted.provenance["shape_result_publication"]["rfd3_aggregate"]
    await engine.dispose()
