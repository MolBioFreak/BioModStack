from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from database import (
    Base,
    Design,
    Job,
    RFD3LocalRedesignArtifact,
    RFD3LocalRedesignCandidate,
    RFD3LocalRedesignRequest,
)
from services.result_ingester import _design_lineage_fields
from services.result_state_integrity import finalize_successful_job, job_expects_design_results, repair_result_state
from routers.jobs import reingest_job_results


def test_job_get_handlers_are_read_only() -> None:
    jobs_source = (API_ROOT / "routers" / "jobs.py").read_text(encoding="utf-8")
    tree = ast.parse(jobs_source)
    handlers = {
        node.name: ast.get_source_segment(jobs_source, node) or ""
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "get"
            for decorator in node.decorator_list
        )
    }

    assert {"get_job", "get_stage_gates", "get_children_status"} <= handlers.keys()
    for name, source in handlers.items():
        assert "_repair_job_for_response" not in source, name
        assert "_reconcile_child_jobs_from_history" not in source, name
        assert "session.commit" not in source, name
        assert "session.add" not in source, name
        assert "session.delete" not in source, name
        assert "schedule_viewer_minimum_analyses_for_job" not in source, name


def test_design_get_handlers_and_review_hydration_are_read_only() -> None:
    designs_source = (API_ROOT / "routers" / "designs.py").read_text(encoding="utf-8")
    tree = ast.parse(designs_source)
    handlers = {
        node.name: ast.get_source_segment(designs_source, node) or ""
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "get"
            for decorator in node.decorator_list
        )
    }
    assert {"list_designs", "get_backbone_summary", "get_designs_for_job"} <= handlers.keys()
    for name, source in handlers.items():
        assert "session.commit" not in source, name
        assert "session.add" not in source, name
        assert "session.delete" not in source, name

    hydration = next(node for node in tree.body if getattr(node, "name", None) == "_hydrate_review_job")
    hydration_source = ast.get_source_segment(designs_source, hydration) or ""
    for forbidden in ("session.commit", "session.add", "session.delete", "ensure_stage_review_rows"):
        assert forbidden not in hydration_source


async def _session_factory(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'integrity.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False), engine


def _job(job_id: str, **overrides) -> Job:
    values = {
        "id": job_id,
        "name": job_id,
        "model_id": "boltz2",
        "mode": "predict",
        "params": {},
        "status": "running",
        "queue_status": "running",
        "created_at": datetime.utcnow(),
        "awaiting_input": False,
        "awaiting_payload": {},
        "retry_count": 0,
        "max_retries": 2,
    }
    values.update(overrides)
    return Job(**values)


def test_design_result_expectation_is_explicit_or_known_model_only() -> None:
    assert job_expects_design_results(_job("known", model_id="boltz2")) is True
    assert job_expects_design_results(_job("normalized-alias", model_id="Boltz-2")) is True
    assert job_expects_design_results(_job("af2", model_id="af2")) is True
    assert job_expects_design_results(_job("alphafold2", model_id="AlphaFold2")) is True
    assert job_expects_design_results(_job("confornets", model_id="confornets_experimental")) is True
    assert job_expects_design_results(_job("esmfold2", model_id="esmfold2")) is True
    assert job_expects_design_results(_job("esmfold2-alias", model_id="esmfold2_experimental")) is True
    assert job_expects_design_results(_job("frustrampnn-analysis", model_id="frustrampnn", mode="analyze")) is False
    assert job_expects_design_results(_job("substring-collision", model_id="custom_boltz_report")) is False
    assert job_expects_design_results(_job("af2-substring-collision", model_id="custom_af2_report")) is False
    assert job_expects_design_results(_job("confornets-substring-collision", model_id="custom_confornets_experimental_report")) is False
    assert job_expects_design_results(_job("unknown", model_id="custom_file_workflow", mode="run")) is False
    assert job_expects_design_results(_job("native-rfd3", model_id="protein_local_redesign")) is False
    assert job_expects_design_results(
        _job(
            "explicit",
            model_id="custom_file_workflow",
            mode="run",
            params={"result_integrity_requires_designs": True},
        )
    ) is True


@pytest.mark.asyncio
async def test_native_rfd3_completion_uses_typed_candidates_without_generic_designs(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "native-rfd3"
    structure = output_dir / "run" / "rfd3" / "candidate.cif.gz"
    structure.parent.mkdir(parents=True)
    structure.write_bytes(b"native-rfd3-candidate")
    content_sha256 = hashlib.sha256(structure.read_bytes()).hexdigest()

    async with factory() as session:
        job = _job(
            "native-rfd3",
            model_id="protein_local_redesign",
            output_dir=str(output_dir),
        )
        request = RFD3LocalRedesignRequest(
            request_id="native-request",
            job_id=job.id,
            schema_version=1,
            request_sha256="1" * 64,
            profile_id="generic_local_redesign_v1",
            profile_registry_sha256="2" * 64,
            redesign_mode="partial_diffusion",
            sequence_policy="skip",
            status="generated",
            request_json={"schema": "bms.rfd3.local-redesign.request.v1"},
        )
        session.add_all([job, request])
        await session.commit()

        async def ingest(job_id: str, _output_dir: str, ingest_session, **_kwargs) -> int:
            assert job_id == job.id
            ingest_session.add_all(
                [
                    RFD3LocalRedesignCandidate(
                        id="native-candidate-row",
                        request_id=request.request_id,
                        candidate_id="candidate",
                        result_set="rfd3_local_redesign_candidates",
                        stage="backbone",
                        status="generated",
                        artifact_manifest_sha256="3" * 64,
                        metrics_json={},
                        metadata_json={},
                    ),
                    RFD3LocalRedesignArtifact(
                        artifact_id="native-structure-artifact",
                        request_id=request.request_id,
                        candidate_id="candidate",
                        role="structure",
                        relative_path="run/rfd3/candidate.cif.gz",
                        storage_path=str(structure),
                        content_sha256=content_sha256,
                        size_bytes=structure.stat().st_size,
                        media_type="chemical/x-mmcif+gzip",
                        metadata_json={},
                    ),
                ]
            )
            await ingest_session.flush()
            return 1

        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=ingest)
        await session.refresh(job)

        assert result.completed is True
        assert result.design_count == 1
        assert job.status == "completed"
        assert job.provenance["result_integrity"]["result_kind"] == "rfd3_local_redesign_candidate"
        assert (await session.execute(select(Design).where(Design.job_id == job.id))).scalars().all() == []

    await engine.dispose()


def test_lineage_fields_accept_artifact_override_without_duplicate_kwarg_path() -> None:
    fields = _design_lineage_fields(
        {"artifact_class": "backbone_complex", "artifact_schema_version": 1},
        {},
        artifact_class_override="sequence_designed_complex",
    )

    design = Design(
        id="design-1",
        job_id="job-1",
        name="candidate",
        pdb_path="candidate.pdb",
        **fields,
    )
    assert design.artifact_class == "sequence_designed_complex"
    assert design.artifact_schema_version == 1


@pytest.mark.asyncio
async def test_completion_is_committed_only_after_ingestion_and_validation(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "results"
    output_dir.mkdir()
    (output_dir / "d.pdb").write_text("MODEL\nEND\n", encoding="utf-8")

    async with factory() as session:
        job = _job("ordered", output_dir=str(output_dir))
        session.add(job)
        await session.commit()
        observed_statuses: list[tuple[str, str]] = []

        async def ingest(job_id: str, _output_dir: str, ingest_session, **_kwargs) -> int:
            observed_statuses.append((job.status, job.queue_status))
            ingest_session.add(
                Design(id="ordered-design", job_id=job_id, name="d", pdb_path="d.pdb")
            )
            await ingest_session.commit()
            return 1

        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=ingest)
        await session.refresh(job)

        assert observed_statuses == [("running", "running")]
        assert result.design_count == 1
        assert job.status == "completed"
        assert job.queue_status == "completed"
        assert job.provenance["result_integrity"]["state"] == "validated"

    await engine.dispose()


@pytest.mark.asyncio
async def test_cancellation_during_ingestion_remains_terminal_and_skips_completion(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "cancel-race"
    output_dir.mkdir()
    cancelled_at = datetime(2026, 7, 12, 17, 30, 0)

    async with factory() as session:
        job = _job("cancel-race", output_dir=str(output_dir))
        session.add(job)
        await session.commit()

        async def ingest(job_id: str, _output_dir: str, ingest_session, **_kwargs) -> int:
            ingest_session.add(Design(id="cancel-race-design", job_id=job_id, name="d", pdb_path="d.pdb"))
            await ingest_session.commit()
            async with factory() as cancelling_session:
                cancelled = await cancelling_session.get(Job, job_id)
                assert cancelled is not None
                cancelled.status = "cancelled"
                cancelled.queue_status = "cancelled"
                cancelled.completed_at = cancelled_at
                cancelled.error_message = "Cancelled by user"
                await cancelling_session.commit()
            return 1

        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=ingest)
        await session.refresh(job)

        assert result.completed is False
        assert result.integrity_state == "cancelled"
        assert job.status == "cancelled"
        assert job.queue_status == "cancelled"
        assert job.completed_at == cancelled_at
        assert job.error_message == "Cancelled by user"

    await engine.dispose()


@pytest.mark.asyncio
async def test_cancellation_between_ingestion_failure_check_and_publish_wins(tmp_path: Path) -> None:
    """The failure publication must not overwrite cancellation after rollback."""
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "failure-cancel-race"
    output_dir.mkdir()

    async with factory() as session:
        job = _job("failure-cancel-race", output_dir=str(output_dir))
        session.add(job)
        await session.commit()
        original_execute = session.execute
        arm_race = False

        async def execute_with_cancellation(statement, *args, **kwargs):
            nonlocal arm_race
            if arm_race and getattr(statement, "is_update", False):
                arm_race = False
                async with factory() as cancelling_session:
                    cancelled = await cancelling_session.get(Job, str(job.id))
                    assert cancelled is not None
                    cancelled.status = "cancelled"
                    cancelled.queue_status = "cancelled"
                    cancelled.error_message = "Cancelled by user"
                    await cancelling_session.commit()
            return await original_execute(statement, *args, **kwargs)

        session.execute = execute_with_cancellation  # type: ignore[method-assign]

        async def fail_ingest(*_args, **_kwargs) -> int:
            nonlocal arm_race
            arm_race = True
            raise RuntimeError("synthetic ingestion failure")

        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=fail_ingest)
        await session.refresh(job)
        assert result.completed is False
        assert result.integrity_state == "cancelled"
        assert job.status == "cancelled"
        assert job.queue_status == "cancelled"
        assert job.error_message == "Cancelled by user"

    await engine.dispose()


@pytest.mark.asyncio
async def test_interrupted_ingestion_is_not_prematurely_completed_and_can_recover(tmp_path: Path) -> None:
    import asyncio

    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "interrupted"
    output_dir.mkdir()
    (output_dir / "persisted.pdb").write_text("MODEL\nEND\n", encoding="utf-8")

    async with factory() as session:
        job = _job("interrupted", output_dir=str(output_dir))
        session.add(job)
        await session.commit()

        async def interrupted_ingest(job_id: str, _output_dir: str, ingest_session, **_kwargs) -> int:
            ingest_session.add(Design(id="persisted-design", job_id=job_id, name="persisted", pdb_path="persisted.pdb"))
            await ingest_session.commit()
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await finalize_successful_job(job, str(output_dir), session, ingest_fn=interrupted_ingest)

    async with factory() as verify_session:
        persisted = await verify_session.get(Job, "interrupted")
        assert persisted is not None
        assert persisted.status == "running"
        assert persisted.queue_status == "running"
        assert await verify_session.get(Design, "persisted-design") is not None

        async def no_new_results(*_args, **_kwargs) -> int:
            return 0

        recovered = await finalize_successful_job(
            persisted, str(output_dir), verify_session, ingest_fn=no_new_results
        )
        assert recovered.completed is True
        await verify_session.refresh(persisted)
        assert persisted.status == "completed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_ingestion_is_explicit_and_never_cleanly_completed(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "failed"
    output_dir.mkdir()

    async with factory() as session:
        job = _job("failed-ingest", output_dir=str(output_dir))
        session.add(job)
        await session.commit()

        async def ingest(*_args, **_kwargs) -> int:
            raise RuntimeError("broken parser")

        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=ingest)
        await session.refresh(job)

        assert result.completed is False
        assert job.status == "failed"
        assert job.queue_status == "failed"
        assert "broken parser" in job.error_message
        assert job.provenance["result_integrity"] == {
            "state": "ingestion_failed",
            "partial": False,
            "design_count": 0,
            "result_count": 0,
            "result_kind": "design",
            "error": "broken parser",
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_handles_orphan_child_missing_payload_and_empty_completion_idempotently(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    gate_output = tmp_path / "gate-job"
    gates = gate_output / "gates"
    gates.mkdir(parents=True)
    (gates / "gate_post_fampnn.json").write_text(
        '{"stage": "post_fampnn", "candidate_count": 3, "design_ids": ["a", "b", "c"]}',
        encoding="utf-8",
    )

    async with factory() as session:
        session.add_all(
            [
                _job(
                    "orphan-child",
                    parent_job_id="missing-parent",
                    child_stage="boltz2",
                    status="running",
                    queue_status="completed",
                ),
                _job(
                    "awaiting",
                    output_dir=str(gate_output),
                    status="awaiting_input",
                    queue_status="running",
                    awaiting_input=True,
                    awaiting_stage="post_fampnn",
                    awaiting_payload={},
                ),
                _job(
                    "empty-completed",
                    output_dir=str(tmp_path / "empty"),
                    status="completed",
                    queue_status="completed",
                    completed_at=datetime.utcnow(),
                ),
            ]
        )
        await session.commit()

        dry_run = await repair_result_state(session, apply=False)
        assert dry_run.applied is False
        assert {change.code for change in dry_run.changes} >= {
            "orphan_child",
            "missing_awaiting_payload",
            "completed_without_results",
        }
        rows = {row.id: row for row in (await session.execute(select(Job))).scalars()}
        assert rows["orphan-child"].status == "running"
        assert rows["awaiting"].awaiting_payload == {}
        assert rows["empty-completed"].status == "completed"

        applied = await repair_result_state(session, apply=True)
        assert applied.applied is True
        rows = {row.id: row for row in (await session.execute(select(Job))).scalars()}
        assert rows["orphan-child"].status == "failed"
        assert rows["orphan-child"].queue_status == "failed"
        assert rows["awaiting"].status == "awaiting_input"
        assert rows["awaiting"].queue_status == "completed"
        assert rows["awaiting"].awaiting_payload["stage"] == "post_fampnn"
        assert rows["awaiting"].awaiting_payload
        assert rows["empty-completed"].status == "failed"
        assert rows["empty-completed"].provenance["result_integrity"]["state"] == "ingestion_failed"

        second = await repair_result_state(session, apply=True)
        assert second.changes == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_preserves_cancellation_and_normalizes_retry_state(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    async with factory() as session:
        session.add_all(
            [
                _job(
                    "cancelled",
                    status="cancelled",
                    queue_status="queued",
                    completed_at=datetime.utcnow(),
                    retry_count=1,
                    paused=True,
                    assigned_gpu=2,
                    current_stage="run_boltz",
                    stage_progress="1/3",
                    error_message="Cancelled by user",
                ),
                _job(
                    "retry",
                    status="failed",
                    queue_status="queued",
                    completed_at=datetime.utcnow(),
                    error_message="old failure",
                    retry_count=1,
                ),
            ]
        )
        await session.commit()

        report = await repair_result_state(session, apply=True)
        assert {change.code for change in report.changes} >= {
            "cancelled_queue_mismatch",
            "retry_state_mismatch",
        }
        rows = {row.id: row for row in (await session.execute(select(Job))).scalars()}
        assert rows["cancelled"].status == "cancelled"
        assert rows["cancelled"].queue_status == "cancelled"
        assert rows["cancelled"].paused is False
        assert rows["cancelled"].assigned_gpu is None
        assert rows["cancelled"].retry_count == 0
        assert rows["cancelled"].current_stage is None
        assert rows["cancelled"].stage_progress is None
        assert rows["cancelled"].error_message == "Cancelled by user"
        assert rows["retry"].status == "queued"
        assert rows["retry"].queue_status == "queued"
        assert rows["retry"].completed_at is None
        assert rows["retry"].error_message is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_clears_stale_awaiting_state_from_cancelled_job_in_one_pass(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    async with factory() as session:
        session.add(
            _job(
                "cancelled-awaiting",
                status="cancelled",
                queue_status="queued",
                awaiting_input=True,
                awaiting_stage="post_fampnn",
                awaiting_payload={"stage": "post_fampnn", "candidate_count": 3},
            )
        )
        await session.commit()

        report = await repair_result_state(session, apply=True)
        assert [change.code for change in report.changes] == ["cancelled_queue_mismatch"]

        job = await session.get(Job, "cancelled-awaiting")
        assert job is not None
        assert job.status == "cancelled"
        assert job.queue_status == "cancelled"
        assert job.awaiting_input is False
        assert job.awaiting_stage is None
        assert job.awaiting_payload == {}

        second = await repair_result_state(session, apply=False)
        assert second.changes == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_integrity_failure_sets_terminal_timestamp_and_clears_runtime_state(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    created_at = datetime(2026, 6, 1, 12, 0, 0)
    async with factory() as session:
        session.add(
            _job(
                "orphan-running",
                parent_job_id="missing-parent",
                status="running",
                queue_status="running",
                created_at=created_at,
                paused=True,
                assigned_gpu=3,
                current_stage="run_boltz",
                stage_progress="2/5",
            )
        )
        await session.commit()

        report = await repair_result_state(session, apply=True)
        assert [change.code for change in report.changes] == ["orphan_child"]

        job = await session.get(Job, "orphan-running")
        assert job is not None
        assert job.status == "failed"
        assert job.queue_status == "failed"
        assert job.completed_at == created_at
        assert job.paused is False
        assert job.assigned_gpu is None
        assert job.current_stage == "Result Integrity Failed"
        assert job.stage_progress is None
        assert "missing parent" in job.error_message

    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_cli_defaults_to_dry_run_and_supports_report_backup_and_apply(tmp_path: Path) -> None:
    database_path = tmp_path / "cli.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        session.add(
            _job(
                "cli-empty",
                status="completed",
                queue_status="completed",
                completed_at=datetime.utcnow(),
            )
        )
        await session.commit()
    await engine.dispose()

    script = API_ROOT.parents[1] / "scripts" / "repair_result_state.py"
    dry_run = subprocess.run(
        [sys.executable, str(script), "--database", str(database_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    dry_payload = json.loads(dry_run.stdout)
    assert dry_payload["mode"] == "dry-run"
    assert dry_payload["applied"] is False
    assert dry_payload["change_count"] == 1

    backup_path = tmp_path / "backups" / "before.db"
    report_path = tmp_path / "reports" / "apply.json"
    apply_run = subprocess.run(
        [
            sys.executable,
            str(script),
            "--database",
            str(database_path),
            "--apply",
            "--backup",
            str(backup_path),
            "--report",
            str(report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    apply_payload = json.loads(apply_run.stdout)
    assert apply_payload["mode"] == "apply"
    assert apply_payload["applied"] is True
    assert backup_path.exists()
    assert json.loads(report_path.read_text())["change_count"] == 1

    second = subprocess.run(
        [sys.executable, str(script), "--database", str(database_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(second.stdout)["change_count"] == 0


@pytest.mark.asyncio
async def test_zero_ingest_does_not_validate_stale_unusable_design(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "stale"
    output_dir.mkdir()
    async with factory() as session:
        job = _job("stale-design", output_dir=str(output_dir))
        session.add_all([job, Design(id="stale-row", job_id=job.id, name="stale", pdb_path="missing.pdb")])
        await session.commit()

        async def ingest(*_args, **_kwargs) -> int:
            return 0

        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=ingest)
        await session.refresh(job)
        assert result.completed is False
        assert result.integrity_state == "ingestion_failed"
        assert job.status == "failed"
        assert "lack usable, contained PDB artifacts" in job.error_message
    await engine.dispose()


@pytest.mark.asyncio
async def test_zero_ingest_accepts_only_validated_idempotent_prior_design(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "prior"
    output_dir.mkdir()
    (output_dir / "prior.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
    async with factory() as session:
        job = _job("valid-prior", output_dir=str(output_dir))
        session.add_all([job, Design(id="valid-prior-row", job_id=job.id, name="prior", pdb_path="prior.pdb")])
        await session.commit()

        async def ingest(*_args, **_kwargs) -> int:
            return 0

        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=ingest)
        await session.refresh(job)
        assert result.completed is True
        assert result.design_count == 1
        assert job.provenance["result_integrity"]["idempotent_prior_results"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_keeps_cancellation_authoritative_for_compound_orphan_state(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    async with factory() as session:
        session.add(_job("cancelled-orphan", parent_job_id="missing-parent", status="cancelled", queue_status="queued", awaiting_input=True, awaiting_stage="post_fampnn", awaiting_payload={"stage": "post_fampnn"}))
        await session.commit()
        report = await repair_result_state(session, apply=True)
        assert [change.code for change in report.changes] == ["cancelled_queue_mismatch"]
        job = await session.get(Job, "cancelled-orphan")
        assert job is not None
        assert job.status == "cancelled"
        assert job.queue_status == "cancelled"
        assert job.parent_job_id is None
        assert job.awaiting_input is False
        assert job.awaiting_stage is None
        assert job.awaiting_payload == {}
        second = await repair_result_state(session, apply=True)
        assert second.changes == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_apply_does_not_overwrite_concurrent_cancellation(tmp_path: Path) -> None:
    """A conditional repair publication must lose to an operator cancellation."""
    factory, engine = await _session_factory(tmp_path)
    async with factory() as session:
        # A completed design workflow without results is normally repaired to failed.
        job = _job(
            "repair-cancel-race",
            status="completed",
            queue_status="completed",
            completed_at=datetime(2026, 7, 13, 9, 0, 0),
        )
        session.add(job)
        await session.commit()
        original_execute = session.execute
        arm_cancellation = True

        async def execute_with_cancellation(statement, *args, **kwargs):
            nonlocal arm_cancellation
            if arm_cancellation and getattr(statement, "is_update", False):
                arm_cancellation = False
                async with factory() as cancelling_session:
                    cancelled = await cancelling_session.get(Job, job.id)
                    assert cancelled is not None
                    cancelled.status = "cancelled"
                    cancelled.queue_status = "cancelled"
                    cancelled.error_message = "Cancelled by user"
                    await cancelling_session.commit()
            return await original_execute(statement, *args, **kwargs)

        session.execute = execute_with_cancellation  # type: ignore[method-assign]
        report = await repair_result_state(session, apply=True)

        refreshed = await session.get(Job, job.id)
        assert refreshed is not None
        assert refreshed.status == "cancelled"
        assert refreshed.queue_status == "cancelled"
        assert refreshed.error_message == "Cancelled by user"
        assert len(report.changes) == 1
        assert report.changes[0].code == "completed_without_results"
        assert report.changes[0].disposition == "superseded"
        payload = report.to_dict()
        assert payload["apply_requested"] is True
        assert payload["applied"] is False
        assert payload["applied_change_count"] == 0
        assert payload["unresolved_count"] == 0
        assert payload["superseded_count"] == 1
        assert "concurrent authoritative state" in report.changes[0].detail
    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_apply_never_overwrites_other_concurrent_job_state(tmp_path: Path) -> None:
    """A full snapshot guard preserves concurrent operator edits outside terminal flags."""
    factory, engine = await _session_factory(tmp_path)
    async with factory() as session:
        job = _job(
            "repair-unrelated-race",
            status="completed",
            queue_status="running",
            error_message="original error",
            current_stage="original stage",
        )
        session.add(job)
        await session.commit()
        original_execute = session.execute
        arm_edit = True

        async def execute_with_operator_edit(statement, *args, **kwargs):
            nonlocal arm_edit
            if arm_edit and getattr(statement, "is_update", False):
                arm_edit = False
                async with factory() as operator_session:
                    current = await operator_session.get(Job, job.id)
                    assert current is not None
                    current.parent_job_id = "operator-restored-parent"
                    current.error_message = "operator note"
                    current.current_stage = "operator stage"
                    await operator_session.commit()
            return await original_execute(statement, *args, **kwargs)

        session.execute = execute_with_operator_edit  # type: ignore[method-assign]
        report = await repair_result_state(session, apply=True)

        refreshed = await session.get(Job, job.id)
        assert refreshed is not None
        assert refreshed.queue_status == "running"
        assert refreshed.parent_job_id == "operator-restored-parent"
        assert refreshed.error_message == "operator note"
        assert refreshed.current_stage == "operator stage"
        assert report.applied is False
        assert report.changes[0].disposition == "unresolved"
        payload = report.to_dict()
        assert payload["apply_requested"] is True
        assert payload["applied"] is False
        assert payload["applied_change_count"] == 0
        assert payload["unresolved_count"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_orphan_design_is_truthful_report_only_finding_on_every_run(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    async with factory() as session:
        session.add(Design(id="orphan-design", job_id="missing-job", name="orphan", pdb_path="orphan.pdb"))
        await session.commit()
        for apply in (False, True, True):
            report = await repair_result_state(session, apply=apply)
            assert len(report.changes) == 1
            finding = report.changes[0]
            assert finding.code == "orphan_design"
            assert finding.disposition == "unresolved"
            assert finding.before == finding.after == {"present": True}
            payload = report.to_dict()
            assert payload["unresolved_count"] == 1
            assert payload["applied_change_count"] == 0
            assert await session.get(Design, "orphan-design") is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_completed_at_uses_stable_existing_timestamp_and_is_idempotent(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    created_at = datetime(2026, 1, 2, 3, 4, 5)
    async with factory() as session:
        session.add(_job("missing-completed-at", model_id="custom_file_workflow", mode="run", status="completed", queue_status="running", created_at=created_at, completed_at=None))
        await session.commit()
        report = await repair_result_state(session, apply=True)
        assert [change.code for change in report.changes] == ["completed_without_timestamp"]
        job = await session.get(Job, "missing-completed-at")
        assert job is not None
        assert job.completed_at == created_at
        assert job.queue_status == "completed"
        second = await repair_result_state(session, apply=True)
        assert second.changes == []
        await session.refresh(job)
        assert job.completed_at == created_at
    await engine.dispose()


@pytest.mark.parametrize("apply", [False, True])
def test_repair_cli_rejects_missing_explicit_database_without_creating_it(
    tmp_path: Path, apply: bool
) -> None:
    database_path = tmp_path / ("missing-apply.db" if apply else "missing-dry-run.db")
    script = API_ROOT.parents[1] / "scripts" / "repair_result_state.py"
    command = [sys.executable, str(script), "--database", str(database_path)]
    if apply:
        command.append("--apply")

    result = subprocess.run(command, capture_output=True, text=True)

    assert result.returncode != 0
    assert "database does not exist" in result.stderr
    assert not database_path.exists()


@pytest.mark.asyncio
async def test_positive_ingest_with_missing_pdb_is_not_validated(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "missing-pdb"
    output_dir.mkdir()
    async with factory() as session:
        job = _job("positive-missing-pdb", output_dir=str(output_dir))
        session.add(job)
        await session.commit()

        async def ingest(job_id: str, _output_dir: str, ingest_session, **_kwargs) -> int:
            ingest_session.add(Design(id="missing-pdb-row", job_id=job_id, name="missing", pdb_path="missing.pdb"))
            await ingest_session.commit()
            return 1

        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=ingest)
        await session.refresh(job)
        assert result.completed is False
        assert result.integrity_state == "ingestion_failed"
        assert job.status == "failed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_finalization_preserves_awaiting_input_gate(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    output_dir = tmp_path / "gate"
    output_dir.mkdir()
    async with factory() as session:
        job = _job(
            "review-gate",
            output_dir=str(output_dir),
            status="awaiting_input",
            queue_status="completed",
            awaiting_input=True,
            awaiting_stage="post_fampnn",
            awaiting_payload={"stage": "post_fampnn"},
        )
        session.add(job)
        await session.commit()
        result = await finalize_successful_job(job, str(output_dir), session, ingest_fn=lambda *_args, **_kwargs: 0)
        await session.refresh(job)
        assert result.completed is False
        assert result.integrity_state == "awaiting_input"
        assert job.status == "awaiting_input"
        assert job.awaiting_input is True
        assert job.awaiting_payload == {"stage": "post_fampnn"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_parent_finalization_accepts_usable_immediate_child_result(tmp_path: Path) -> None:
    """Parent completion validates a child artifact against the child's own root."""
    factory, engine = await _session_factory(tmp_path)
    parent_root = tmp_path / "parent-output"
    child_root = tmp_path / "child-output"
    parent_root.mkdir()
    child_root.mkdir()
    (child_root / "child.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
    async with factory() as session:
        parent = _job("finalize-parent", output_dir=str(parent_root))
        child = _job(
            "finalize-child",
            parent_job_id=parent.id,
            output_dir=str(parent_root),
            child_output_dir=str(child_root),
        )
        session.add_all([
            parent,
            child,
            Design(id="finalize-child-design", job_id=child.id, name="child-result", pdb_path="child.pdb"),
        ])
        await session.commit()

        async def ingest(*_args, **_kwargs) -> int:
            return 0

        result = await finalize_successful_job(parent, str(parent_root), session, ingest_fn=ingest)
        await session.refresh(parent)
        assert result.completed is True
        assert result.design_count == 1
        assert parent.status == "completed"
        assert parent.provenance["result_integrity"]["idempotent_prior_results"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_repair_counts_immediate_child_designs_as_parent_results(tmp_path: Path) -> None:
    factory, engine = await _session_factory(tmp_path)
    completed_at = datetime(2026, 7, 13, 8, 0, 0)
    child_output = tmp_path / "child-output"
    child_output.mkdir()
    (child_output / "child.pdb").write_text("MODEL\nEND\n", encoding="utf-8")
    async with factory() as session:
        parent = _job("parent", status="completed", queue_status="completed", completed_at=completed_at)
        child = _job("child", parent_job_id="parent", output_dir=str(child_output), status="completed", queue_status="completed", completed_at=completed_at)
        session.add_all([parent, child, Design(id="child-design", job_id="child", name="child-result", pdb_path="child.pdb")])
        await session.commit()
        report = await repair_result_state(session, apply=True)
        await session.refresh(parent)
        assert all(change.record_id != "parent" for change in report.changes)
        assert parent.status == "completed"
        assert parent.queue_status == "completed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_reingest_children_uses_child_specific_output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory, engine = await _session_factory(tmp_path)
    parent_root = str(tmp_path / "parent")
    child_root = str(tmp_path / "child")
    captured: list[tuple[str, str]] = []

    async def capture_ingestion(job_id: str, output_dir: str, _session: AsyncSession, *, commit: bool = True) -> int:
        assert commit is False
        captured.append((job_id, output_dir))
        return 0

    monkeypatch.setattr("services.result_ingester.ingest_job_results", capture_ingestion)
    async with factory() as session:
        parent = _job("reingest-parent", output_dir=parent_root)
        child = _job(
            "reingest-child", parent_job_id=parent.id, output_dir=parent_root,
            child_output_dir=child_root,
        )
        session.add_all([parent, child])
        await session.commit()

        parent_id = parent.id
        child_id = child.id
        response = await reingest_job_results(parent_id, include_children=True, session=session)

        assert response["designs_created"] == 0
        assert captured == [(parent_id, parent_root), (child_id, child_root)]
    await engine.dispose()


@pytest.mark.asyncio
async def test_reingest_zero_results_rolls_back_existing_design_deletion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    factory, engine = await _session_factory(tmp_path)

    async def empty_ingestion(_job_id: str, _output_dir: str, _session: AsyncSession, *, commit: bool = True) -> int:
        assert commit is False
        return 0

    monkeypatch.setattr("services.result_ingester.ingest_job_results", empty_ingestion)
    async with factory() as session:
        job = _job("reingest-preserve", output_dir=str(tmp_path / "output"))
        session.add(job)
        await session.flush()
        session.add(Design(
            id="reingest-preserve-design",
            job_id=job.id,
            name="keep-me",
            pdb_path=str(tmp_path / "keep-me.pdb"),
        ))
        await session.commit()
        job_id = job.id

        response = await reingest_job_results(job_id, include_children=False, session=session)

        assert response["designs_deleted"] == 0
        persisted = (await session.execute(select(Design).where(Design.job_id == job_id))).scalars().all()
        assert [design.name for design in persisted] == ["keep-me"]
    await engine.dispose()
