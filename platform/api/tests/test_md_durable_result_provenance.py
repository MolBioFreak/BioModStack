from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import Base, Job, JobArtifact
from services.md.artifacts import MdArtifactProvenanceError
import services.md.completion as completion_module
from services.md.read_model import md_run_snapshot
from services.md.results import ResolvedMDArtifact
from services.md.state import accept_checkpoint, create_md_run, create_replica_attempt


def _request() -> dict:
    return {
        "schema": "bms.md.job.v2",
        "chemistry": {
            "profile_id": "amber_ff19sb_opc_protein_v1",
            "profile_sha256": "a" * 64,
            "assurance": "curated_profile",
        },
        "engine": "gromacs",
        "replicas": 1,
        "stages": {
            "minimization": {"enabled": True, "steps": 500},
            "nvt": {"enabled": True, "steps": 1_000},
            "npt": {"enabled": True, "steps": 2_000},
            "production": {"enabled": True, "steps": 10_000, "timestep_fs": 2.0},
        },
    }


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'provenance.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_completion_ingests_validated_playback_inventory_idempotently(
    session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = Job(
        id="md-ingest-parent", name="MD", status="completed",
        model_id="molecular_dynamics", mode="simulate", params={},
    )
    child = Job(
        id="md-ingest-replica", name="MD replica 0", status="completed",
        model_id="molecular_dynamics", mode="replica", params={},
        parent_job_id=parent.id, child_stage="md_replica",
    )
    session.add_all([parent, child])
    await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=_request())
    replica, segment = await create_replica_attempt(
        session, job_id=parent.id, replica_index=0, attempt=0, engine="gromacs",
        execution_plan_sha256="b" * 64, compatibility_key="c" * 64,
        child_job_id=child.id,
    )
    run.phase = "completed"
    replica.state, replica.active = "completed", False
    segment.state, segment.end_step, segment.end_time_ps = "completed", 10_000, 20.0
    root = tmp_path / "results"
    files = {
        "replicas/replica_0/system/prepared.gro": b"topology-bytes",
        "replicas/replica_0/production/production.xtc": b"trajectory-bytes",
        "replicas/replica_0/analysis/trajectory-frame-map.json": b"frame-map-bytes",
    }
    roles = {
        "replicas/replica_0/system/prepared.gro": "analysis_topology",
        "replicas/replica_0/production/production.xtc": "analysis_trajectory",
        "replicas/replica_0/analysis/trajectory-frame-map.json": "trajectory_frame_map",
    }
    inventory = []
    trajectory_sha = hashlib.sha256(files["replicas/replica_0/production/production.xtc"]).hexdigest()
    for index, (logical, content) in enumerate(files.items()):
        path = root / logical
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        inventory.append(ResolvedMDArtifact(
            artifact_id=f"opaque-{index}", replica_index=0, name=path.stem, path=path,
            bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
            semantic_role=roles[logical], atom_order_identity="atom-order",
            selection_method=None, source_frame=None, time_ps=None,
            source_trajectory_sha256=(trajectory_sha if roles[logical] == "trajectory_frame_map" else None),
        ))
    monkeypatch.setattr(completion_module, "_load_inventory", lambda _job: (root, {}, inventory))

    await completion_module._ingest_durable_artifacts(parent, session)
    await completion_module._ingest_durable_artifacts(parent, session)
    await session.flush()

    rows = list((await session.scalars(select(JobArtifact))).all())
    assert len(rows) == 3
    snapshot = await md_run_snapshot(session, parent.id)
    assert snapshot is not None
    assert snapshot["artifact_provenance"]["status"] == "bound"
    by_role = {item["semantic_role"]: item for item in snapshot["artifact_provenance"]["artifacts"]}
    assert by_role["trajectory_frame_map"]["sources"] == [{
        "artifact_id": by_role["analysis_trajectory"]["id"],
        "sha256": by_role["analysis_trajectory"]["sha256"],
    }]


@pytest.mark.asyncio
async def test_run_snapshot_binds_trajectory_checkpoint_and_derived_sources_to_exact_durable_lineage(session) -> None:
    parent = Job(
        id="md-provenance-parent", name="MD", status="completed",
        model_id="molecular_dynamics", mode="simulate", params={},
    )
    child = Job(
        id="md-provenance-replica-0", name="MD replica 0", status="completed",
        model_id="molecular_dynamics", mode="replica", params={},
        parent_job_id=parent.id, child_stage="md_replica",
    )
    session.add_all([parent, child])
    await session.flush()
    run = await create_md_run(session, job=parent, normalized_request=_request())
    replica, segment = await create_replica_attempt(
        session,
        job_id=parent.id,
        replica_index=0,
        attempt=0,
        engine="gromacs",
        execution_plan_sha256="b" * 64,
        compatibility_key="c" * 64,
        child_job_id=child.id,
    )
    run.phase = "completed"
    replica.state = "completed"
    replica.active = False
    segment.state = "completed"
    segment.end_step = 10_000
    segment.end_time_ps = 20.0
    checkpoint = await accept_checkpoint(
        session,
        segment_id=segment.id,
        logical_role="continuation",
        relative_path="replicas/replica_0/state.cpt",
        sha256="d" * 64,
        bytes_=4096,
        step=10_000,
        time_ps=20.0,
        compatibility_key="c" * 64,
    )
    trajectory = JobArtifact(
        id="artifact-trajectory",
        owner_job_id=child.id,
        attempt=0,
        logical_path="replicas/replica_0/production.xtc",
        storage_path="replicas/replica_0/production.xtc",
        sha256="e" * 64,
        bytes=8192,
        media_type="application/octet-stream",
        provenance={
            "schema": "bms.md.artifact-provenance.v1",
            "md_job_id": parent.id,
            "replica_run_id": replica.id,
            "segment_id": segment.id,
            "semantic_role": "analysis_trajectory",
            "sources": [],
        },
    )
    frame_map = JobArtifact(
        id="artifact-frame-map",
        owner_job_id=child.id,
        attempt=0,
        logical_path="replicas/replica_0/trajectory-frame-map.json",
        storage_path="replicas/replica_0/trajectory-frame-map.json",
        sha256="f" * 64,
        bytes=512,
        media_type="application/json",
        provenance={
            "schema": "bms.md.artifact-provenance.v1",
            "md_job_id": parent.id,
            "replica_run_id": replica.id,
            "segment_id": segment.id,
            "semantic_role": "trajectory_frame_map",
            "sources": [{"artifact_id": trajectory.id, "sha256": trajectory.sha256}],
        },
    )
    checkpoint_artifact = JobArtifact(
        id="artifact-checkpoint",
        owner_job_id=child.id,
        attempt=0,
        logical_path=checkpoint.relative_path,
        storage_path=checkpoint.relative_path,
        sha256=checkpoint.sha256,
        bytes=checkpoint.bytes,
        media_type="application/octet-stream",
        provenance={
            "schema": "bms.md.artifact-provenance.v1",
            "md_job_id": parent.id,
            "replica_run_id": replica.id,
            "segment_id": segment.id,
            "checkpoint_id": checkpoint.id,
            "semantic_role": "checkpoint",
            "sources": [],
        },
    )
    session.add_all([trajectory, frame_map, checkpoint_artifact])
    await session.flush()

    snapshot = await md_run_snapshot(session, parent.id)

    assert snapshot is not None
    assert snapshot["replica_count"] == 1
    assert snapshot["requested_time_ps"] == 26.0
    assert snapshot["artifact_provenance"]["status"] == "bound"
    by_id = {item["id"]: item for item in snapshot["artifact_provenance"]["artifacts"]}
    assert by_id[trajectory.id]["replica_run_id"] == replica.id
    assert by_id[trajectory.id]["segment_id"] == segment.id
    assert by_id[frame_map.id]["sources"] == [
        {"artifact_id": trajectory.id, "sha256": trajectory.sha256}
    ]
    assert by_id[checkpoint_artifact.id]["checkpoint_id"] == checkpoint.id
    assert snapshot["checkpoint_available"] is False
    assert "resume_dynamics" not in snapshot["allowed_actions"]
    checkpoint_row = next(item for item in snapshot["checkpoints"] if item["id"] == checkpoint.id)
    assert checkpoint_row["artifact_id"] == checkpoint_artifact.id

    checkpoint_artifact.provenance = {
        **checkpoint_artifact.provenance,
        "semantic_role": "analysis_trajectory",
    }
    await session.flush()
    with pytest.raises(MdArtifactProvenanceError):
        await md_run_snapshot(session, parent.id)


@pytest.mark.asyncio
async def test_run_snapshot_rejects_frame_map_without_exact_trajectory_source(session) -> None:
    parent = Job(
        id="md-unbound-frame-map", name="MD", status="completed",
        model_id="molecular_dynamics", mode="simulate", params={},
    )
    child = Job(
        id="md-unbound-frame-map-replica", name="MD replica 0", status="completed",
        model_id="molecular_dynamics", mode="replica", params={},
        parent_job_id=parent.id, child_stage="md_replica",
    )
    session.add_all([parent, child])
    await session.flush()
    await create_md_run(session, job=parent, normalized_request=_request())
    replica, segment = await create_replica_attempt(
        session,
        job_id=parent.id,
        replica_index=0,
        attempt=0,
        engine="gromacs",
        execution_plan_sha256="b" * 64,
        compatibility_key="c" * 64,
        child_job_id=child.id,
    )
    common = {
        "schema": "bms.md.artifact-provenance.v1",
        "md_job_id": parent.id,
        "replica_run_id": replica.id,
        "segment_id": segment.id,
    }
    session.add_all([
        JobArtifact(
            id="unbound-trajectory",
            owner_job_id=child.id,
            attempt=0,
            logical_path="replicas/replica_0/production.xtc",
            storage_path="replicas/replica_0/production.xtc",
            sha256="e" * 64,
            bytes=8192,
            media_type="application/octet-stream",
            provenance={**common, "semantic_role": "analysis_trajectory", "sources": []},
        ),
        JobArtifact(
            id="unbound-frame-map",
            owner_job_id=child.id,
            attempt=0,
            logical_path="replicas/replica_0/trajectory-frame-map.json",
            storage_path="replicas/replica_0/trajectory-frame-map.json",
            sha256="f" * 64,
            bytes=512,
            media_type="application/json",
            provenance={**common, "semantic_role": "trajectory_frame_map", "sources": []},
        ),
    ])
    await session.flush()

    with pytest.raises(MdArtifactProvenanceError) as exc_info:
        await md_run_snapshot(session, parent.id)

    assert exc_info.value.code == "MD_ARTIFACT_PROVENANCE_INVALID"
    assert "frame map" in str(exc_info.value)
