"""Offline durable remote lifecycle interleavings (independent DB sessions)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import asyncio
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base, Job, ExecutionTarget
from component_runtime import NativeInvocation, SourceIdentity
from services.remote_execution import executor as ex
from services.remote_execution.contracts import RemoteAttemptStatus


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ex, "async_session", factory)
    monkeypatch.setattr(ex, "get_data_root", lambda: tmp_path)
    async with factory() as s:
        s.add(Job(id="job", name="job", model_id="boltz2", mode="predict", params={},
                  status="running", queue_status="running", execution_target_id="target",
                  execution_source_revision="a" * 40, execution_source_tree="b" * 40,
                  execution_bundle_sha256="c" * 64,
                  nextflow_run_id="remote:attempt", remote_attempt_id="attempt", remote_state="running"))
        s.add(ExecutionTarget(id="target", provider="vast", provider_instance_id="1",
                             leased_job_id="job", lease_acquired_at=datetime.utcnow()))
        await s.commit()
    yield factory
    await engine.dispose()


def lifecycle_invocation(command):
    """Typed handoff for mocked lifecycle commands, not a science compiler probe."""
    return NativeInvocation(
        model_id="boltz2", mode="predict", command=tuple(command),
        requested_json=b"{}", effective_json=b"{}", native_parameters_json=b"{}",
        source_identity=SourceIdentity("a" * 40, "b" * 40),
    )


async def preparing(store):
    async with store() as s:
        job = await s.get(Job, "job")
        job.status, job.queue_status = "queued", "preparing"
        job.remote_attempt_id = job.nextflow_run_id = None
        job.remote_state = "preparing"
        job.provenance = {"remote_execution_assignment": {"claimed_at": datetime.utcnow().isoformat() + "Z"}}
        await s.commit()


@pytest.mark.asyncio
async def test_prebundle_failure_terminalizes_once_and_releases(store, monkeypatch):
    await preparing(store)
    async def unavailable(*_):
        raise ex.ExecutionTargetError("deterministic preflight failure")
    monkeypatch.setattr(ex, "get_ready_target", unavailable)
    async with store() as s:
        with pytest.raises(ex.RemoteExecutionError, match="deterministic preflight failure"):
            await ex.launch_remote_job(s, await s.get(Job, "job"), command=["false"],
                                       native_invocation=lifecycle_invocation(["false"]))
    async with store() as s:
        job = await s.get(Job, "job")
        assert (job.status, job.queue_status) == ("failed", "failed")
        stamp = job.completed_at
        assert (await s.get(ExecutionTarget, "target")).leased_job_id is None
        for _ in range(3):
            await ex.reconcile_remote_job(s, job)
        await s.refresh(job)
        assert job.status == "failed" and job.completed_at == stamp


@pytest.mark.asyncio
@pytest.mark.parametrize("successor", ["cancelled", "retry"])
async def test_delayed_preflight_failure_cannot_touch_new_authority(store, monkeypatch, successor):
    await preparing(store)
    async def unavailable(*_):
        async with store() as other:
            job = await other.get(Job, "job")
            if successor == "cancelled":
                job.status, job.queue_status = "cancelled", "cancelled"
            else:
                job.remote_attempt_id = "successor"
                job.nextflow_run_id = "remote:successor"
                job.provenance = {"remote_execution_assignment": {"claimed_at": "successor"}}
            await other.commit()
        raise ex.ExecutionTargetError("old launch failed")
    monkeypatch.setattr(ex, "get_ready_target", unavailable)
    async with store() as s:
        with pytest.raises(ex.RemoteExecutionError, match="old launch failed"):
            await ex.launch_remote_job(s, await s.get(Job, "job"), command=["false"],
                                       native_invocation=lifecycle_invocation(["false"]))
    async with store() as s:
        job = await s.get(Job, "job")
        assert job.error_message is None
        assert job.status == ("cancelled" if successor == "cancelled" else "queued")
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"


@pytest.mark.asyncio
@pytest.mark.parametrize("competing", [None, "cancelled", "retry", "callback", "uncertain", "lease_lost"])
async def test_start_receipt_publication_is_claim_fenced(store, monkeypatch, competing):
    await preparing(store)
    from services import stage_reporting
    token, digest = stage_reporting.issue_stage_report_token()
    async with store() as s:
        job = await s.get(Job, "job")
        job.provenance = dict(job.provenance, **{stage_reporting.PROVENANCE_DIGEST_KEY: digest})
        await s.commit()
    async def ready(s, *_):
        return await s.get(ExecutionTarget, "target")
    async def noop(*_, **__):
        pass
    monkeypatch.setattr(ex, "get_ready_target", ready)
    monkeypatch.setattr(ex.RemoteConnection, "from_target", lambda *_: None)
    monkeypatch.setattr(ex, "_verify_remote_runner", noop)
    monkeypatch.setattr(ex, "_stage_bundle", noop)
    monkeypatch.setattr(ex, "_stage_secret_environment", noop)
    monkeypatch.setattr(ex, "_archive_envelope", lambda *_: None)
    monkeypatch.setattr(ex, "_cleanup_local_bundle", lambda *_: None)
    monkeypatch.setattr(ex, "_worker_argv", lambda _, command, *__: [command])
    monkeypatch.setattr(ex, "_remote_receipt", lambda b, t, **kw: {"attempt_id": b.attempt_id, "state": kw["state"]})
    bundle = SimpleNamespace(attempt_id="attempt", envelope_sha256="hash", remote_attempt_dir="/attempt",
                             envelope=SimpleNamespace(source_revision="rev", source_tree="tree", environment={'BMS_TARGET_RESOURCES': '{"required":{"cpus":1,"memory_bytes":1,"scratch_bytes":0},"gpu_ids":[0]}' }))
    monkeypatch.setattr(ex, "prepare_remote_bundle", lambda **_: bundle)
    # Resource admission is independently covered; exercise the publication fence.
    from services.remote_execution import targets, bundle as bundle_module
    monkeypatch.setattr(bundle_module, 'bind_resource_admission', lambda value, admission: value)
    async def admitted(target, **requirements):
        return {'schema': 'bms.target-resource-admission.v1', 'execution_target_id': target.id,
                'required': {'cpus': 1, 'memory_bytes': 1, 'scratch_bytes': 0},
                'available': {'cpus': 1, 'memory_bytes': 1, 'scratch_bytes': 0},
                'devices': [{'gpu_index': 0, 'gpu_uuid': 'fixture-gpu'}]}
    monkeypatch.setattr(targets, 'admit_target_resources', admitted)
    run_entered = []
    async def run(_, command, **__):
        if command == ["run"]:
            run_entered.append(command)
            async with store() as other:
                job = await other.get(Job, "job")
                assert (job.status, job.queue_status, job.started_at) == ("queued", "preparing", None)
                if competing == "cancelled":
                    job.status, job.queue_status = "cancelled", "cancelled"
                elif competing == "retry":
                    job.remote_attempt_id = "successor"
                    job.nextflow_run_id = "remote:successor"
                elif competing == "lease_lost":
                    (await other.get(ExecutionTarget, "target")).leased_job_id = "other"
                await other.commit()
            if competing == "callback":
                from routers.jobs import _publish_generic_stage_start, _publish_generic_stage_terminal
                async with store() as callback_session:
                    await _publish_generic_stage_start(session=callback_session, job_id="job", stage="fold", token=token)
                    await _publish_generic_stage_terminal(session=callback_session, job_id="job", stage="fold",
                                                          status="complete", outputs=["result.pdb"], token=token)
            if competing == "uncertain":
                raise ex.RemoteTransportError("receipt lost after worker spawn")
            return SimpleNamespace(stdout=receipt("running").model_dump_json())
        return SimpleNamespace(stdout="{}")
    monkeypatch.setattr(ex, "run_remote", run)
    async with store() as s:
        try:
            await ex.launch_remote_job(s, await s.get(Job, "job"), command=["true"],
                                       native_invocation=lifecycle_invocation(["true"]))
        except ex.RemoteExecutionError:
            assert competing is not None
    assert run_entered == [["run"]]
    async with store() as s:
        job = await s.get(Job, "job")
        assert job.status == ("running" if competing in {None, "callback"} else "cancelled" if competing == "cancelled" else "queued")
        if competing in {None, "callback"}:
            assert job.started_at is not None and job.remote_state == "running"
        if competing == "callback":
            assert job.completed_stages == ["fold"]
            assert job.provenance["stage_terminal_states"]["fold"]["status"] == "complete"
        if competing == "uncertain":
            assert job.remote_state == "launch_uncertain" and job.remote_attempt_id == "attempt"
            assert job.started_at is None
            assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"
        if competing == "retry":
            assert job.remote_attempt_id == "successor"


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [False, True])
async def test_aged_preparation_recovers_without_resetting_clock(store, monkeypatch, identity):
    await preparing(store)
    async with store() as s:
        job = await s.get(Job, "job")
        job.provenance = {"remote_execution_assignment": {"claimed_at": (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"}}
        if identity:
            job.remote_attempt_id, job.nextflow_run_id, job.remote_state = "attempt", "remote:attempt", "staging"
        await s.commit()
    monkeypatch.setattr(ex, "_connection_for_attempt", lambda *_: (None, "/attempt"))
    monkeypatch.setattr(ex, "_worker_argv", lambda *_: [])
    async def unavailable(*_, **__):
        raise ex.RemoteTransportError("staging incomplete")
    monkeypatch.setattr(ex, "run_remote", unavailable)
    async with store() as s:
        job = await s.get(Job, "job")
        await ex.reconcile_remote_job(s, job)
        assert job.status == "failed"
        stamp = job.completed_at
        await ex.reconcile_remote_job(s, job)
        assert job.status == "failed" and job.completed_at == stamp
        assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
async def test_prepared_resume_cancellation_during_run_wins(store, monkeypatch):
    await preparing(store)
    async with store() as s:
        job = await s.get(Job, "job")
        job.remote_attempt_id, job.nextflow_run_id, job.remote_state = "attempt", "remote:attempt", "staging"
        await s.commit()
    async def status(*_):
        return receipt("prepared")
    async def noop(*_):
        pass
    async def run(*_, **__):
        async with store() as other:
            job = await other.get(Job, "job")
            assert job.remote_state == "launch_requested"
            job.status, job.queue_status = "cancelled", "cancelled"
            await other.commit()
        return SimpleNamespace(stdout=receipt("running").model_dump_json())
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "_verify_remote_runner", noop)
    monkeypatch.setattr(ex, "_connection_for_attempt", lambda *_: (None, "/attempt"))
    monkeypatch.setattr(ex, "_worker_argv", lambda *_: [])
    monkeypatch.setattr(ex, "run_remote", run)
    async with store() as s:
        await ex.reconcile_remote_job(s, await s.get(Job, "job"))
    async with store() as s:
        assert (await s.get(Job, "job")).status == "cancelled"


@pytest.mark.asyncio
async def test_production_poller_retains_terminal_lease_without_quiescence(store, monkeypatch):
    from services.gpu_orchestrator import GPUOrchestrator
    async with store() as s:
        job = await s.get(Job, "job")
        job.status, job.queue_status = "completed", "completed"
        await s.commit()
    async def forbidden(*_):
        raise AssertionError("terminal recovery must not contact the provider")
    monkeypatch.setattr(ex, "remote_status", forbidden)
    poller = GPUOrchestrator(store, lambda: [], lambda **kwargs: None)
    await poller.check_job_completions()
    await asyncio.gather(*poller._remote_reconciliation_tasks.values())
    await poller.stop()
    async with store() as s:
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"
        assert (await s.get(Job, "job")).status == "completed"


@pytest.mark.asyncio
async def test_production_poller_includes_preparing_claims(store, monkeypatch):
    from services.gpu_orchestrator import GPUOrchestrator
    await preparing(store)
    seen = []
    async def reconcile(s, job):
        seen.append(job.id)
        return False
    monkeypatch.setattr(ex, "reconcile_remote_job", reconcile)
    poller = GPUOrchestrator(store, lambda: [], lambda **kwargs: None)
    await poller.check_job_completions()
    await asyncio.gather(*poller._remote_reconciliation_tasks.values())
    await poller.stop()
    assert seen == ["job"]


@pytest.mark.asyncio
async def test_real_ingestion_failure_releases_lease_in_terminal_commit(store):
    from services.result_state_integrity import finalize_successful_job
    async def fail(*_, **__):
        raise ValueError("unusable scientific results")
    async with store() as s:
        result = await finalize_successful_job(await s.get(Job, "job"), "/nonexistent", s, ingest_fn=fail)
        assert not result.completed
    async with store() as s:
        assert (await s.get(Job, "job")).status == "failed"
        assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, "compile", "preflight"])
async def test_nextflow_handoff_preserves_preparing_and_terminalizes_compile_failure(store, monkeypatch, failure):
    import database
    from services import nextflow as nf
    await preparing(store)
    monkeypatch.setattr(database, "async_session", store)
    monkeypatch.setattr(nf, "assert_workflow_launch_allowed", lambda *_: None)
    monkeypatch.setattr(nf, "transient_workflow_runner_mode", lambda: False)
    monkeypatch.setattr(nf, "configured_lane", lambda **_: None)
    async def preflight(params):
        if failure == "preflight":
            raise ValueError("invalid preflight parameters")
        return params, []
    monkeypatch.setattr(nf, "prepare_boltzgen_params_for_launch", preflight)
    async def launch(s, job, **_):
        assert (job.status, job.queue_status, job.started_at) == ("queued", "preparing", None)
        return "remote:attempt"
    monkeypatch.setattr(ex, "launch_remote_job", launch)
    def command(*_, **__):
        if failure:
            raise ValueError("invalid immutable command")
        return lifecycle_invocation(["true"])
    monkeypatch.setattr(nf, "compile_job_nextflow_invocation", command)
    await nf.launch_nextflow_job("job", "boltz2", "predict", {}, "/unused")
    async with store() as s:
        job = await s.get(Job, "job")
        assert job.status == ("failed" if failure else "queued")
        assert job.started_at is None
        if failure:
            assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("competing", [None, "cancelled", "retry"])
async def test_real_success_finalizer_is_attempt_fenced_and_releases_atomically(store, competing):
    from services.result_state_integrity import finalize_successful_job
    async with store() as s:
        job = await s.get(Job, "job")
        job.model_id = "custom_file_workflow"
        await s.commit()
    async def ingest(_id, _root, s, **__):
        await s.commit()  # Real supported ingester commit boundary.
        if competing:
            async with store() as other:
                job = await other.get(Job, "job")
                if competing == "cancelled":
                    job.status, job.queue_status = "cancelled", "cancelled"
                else:
                    job.remote_attempt_id, job.nextflow_run_id = "successor", "remote:successor"
                await other.commit()
        return 0
    async with store() as s:
        result = await finalize_successful_job(await s.get(Job, "job"), "/unused", s, ingest_fn=ingest)
    async with store() as s:
        job = await s.get(Job, "job")
        assert job.status == ("completed" if competing is None else "cancelled" if competing == "cancelled" else "running")
        assert result.completed == (competing is None)
        if competing is None:
            assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("competing", ["cancelled", "retry"])
async def test_remote_reconciler_never_writes_after_finalizer_loses_authority(store, monkeypatch, tmp_path, competing):
    import hashlib
    import json
    from services import result_state_integrity as integrity
    async with store() as s:
        job = await s.get(Job, "job")
        job.model_id = "custom_file_workflow"
        job.provenance = {"remote_execution_receipt": {"expected_result_contract_sha256": hashlib.sha256(json.dumps(ex.resolve_job_result_contract(job), sort_keys=True, separators=(",", ":")).encode()).hexdigest()}}
        await s.commit()
    async def status(*_):
        return receipt().model_copy(update={"result_manifest_sha256": "a" * 64})
    async def collect(*_):
        return SimpleNamespace(artifacts=[], job_id="job", attempt_id="attempt", exit_code=0), tmp_path
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "collect_remote_results", collect)
    monkeypatch.setattr(ex, "_publish_result_generation", lambda *_: (tmp_path, None))
    async def ingest(_id, _root, session, **_):
        await session.commit()
        async with store() as other:
            job = await other.get(Job, "job")
            if competing == "cancelled":
                job.status, job.queue_status = "cancelled", "cancelled"
            else:
                job.remote_attempt_id, job.nextflow_run_id = "successor", "remote:successor"
            job.remote_state = "operator_authority"
            job.params = {"operator_receipt": "preserved"}
            job.error_message = "operator reason"
            await other.commit()
        return 0
    original = integrity.finalize_successful_job
    async def finalize(job, root, session):
        return await original(job, root, session, ingest_fn=ingest)
    monkeypatch.setattr(integrity, "finalize_successful_job", finalize)
    async with store() as s:
        await ex.reconcile_remote_job(s, await s.get(Job, "job"))
        assert (await s.get(Job, "job")).remote_state == "results_available"
    from fastapi import BackgroundTasks
    from services import remote_stage_receipts
    async def proof(*_, **__):
        pass
    monkeypatch.setattr(ex, "_prove_pull_endpoint", proof)
    monkeypatch.setattr(remote_stage_receipts, "apply_remote_stage_receipts", proof)
    background = BackgroundTasks()
    async with store() as s:
        await ex.request_remote_result_pull(s, await s.get(Job, "job"), background)
    await background()
    async with store() as s:
        job = await s.get(Job, "job")
        assert job.remote_state == "operator_authority"
        assert job.params == {"operator_receipt": "preserved"}
        assert job.error_message == "operator reason"
        if competing == "retry":
            assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_state", ["preparing", "cancelling"])
async def test_queue_exposes_remote_preparing_and_cancelling(store, monkeypatch, queue_state):
    from routers import queue
    await preparing(store)
    async with store() as s:
        job = await s.get(Job, "job")
        job.queue_status = queue_state
        job.vram_estimate_mb = 0
        await s.commit()
    monkeypatch.setattr(queue, "_get_queue_enrichment", lambda *_: {})
    async with store() as s:
        rows = await queue.list_queue(session=s)
        assert [(row.id, row.queue_status) for row in rows] == [("job", queue_state)]
        stats = await queue.get_queue_stats(session=s)
        assert stats.total == 1 and stats.running == 0
        assert getattr(stats, queue_state) == 1


@pytest.mark.asyncio
async def test_preparing_claim_can_be_cancelled_without_worker_or_lease_leak(store):
    from services.job_control import cancel_job_lineage
    await preparing(store)
    async with store() as s:
        await cancel_job_lineage("job", s)
    async with store() as s:
        assert (await s.get(Job, "job")).status == "cancelled"
        assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
async def test_uncertain_start_is_observed_not_automatically_replayed(store, monkeypatch):
    await preparing(store)
    async with store() as s:
        job = await s.get(Job, "job")
        job.remote_attempt_id, job.nextflow_run_id, job.remote_state = "attempt", "remote:attempt", "launch_uncertain"
        await s.commit()
    async def status(*_):
        return receipt("prepared")
    async def forbidden(*_, **__):
        raise AssertionError("uncertain start must not be replayed")
    monkeypatch.setattr(ex, "remote_status", status)
    monkeypatch.setattr(ex, "_connection_for_attempt", lambda *_: (None, "/attempt"))
    monkeypatch.setattr(ex, "_verify_remote_runner", forbidden)
    async with store() as s:
        assert not await ex.reconcile_remote_job(s, await s.get(Job, "job"))
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"


@pytest.mark.asyncio
async def test_cancellation_terminal_publication_cannot_clobber_successor_at_sql_boundary(store, monkeypatch):
    async with store() as s:
        job = await s.get(Job, "job")
        job.queue_status = "cancelling"
        await s.commit()
    async def status(*_):
        return receipt("cancelled")
    monkeypatch.setattr(ex, "remote_status", status)
    async with store() as s:
        original = s.execute
        raced = False
        async def execute(statement, *args, **kwargs):
            nonlocal raced
            if not raced and getattr(statement, "is_update", False):
                raced = True
                async with store() as other:
                    job = await other.get(Job, "job")
                    job.status, job.queue_status = "queued", "preparing"
                    job.remote_attempt_id, job.nextflow_run_id = "successor", "remote:successor"
                    job.remote_state = "successor"
                    await other.commit()
            return await original(statement, *args, **kwargs)
        monkeypatch.setattr(s, "execute", execute)
        await ex.reconcile_remote_job(s, await s.get(Job, "job"))
        assert raced
    async with store() as s:
        job = await s.get(Job, "job")
        assert (job.status, job.queue_status, job.remote_state) == ("queued", "preparing", "successor")
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelling", [False, True])
async def test_nonterminal_observation_cannot_write_into_successor(store, monkeypatch, cancelling):
    async with store() as s:
        job = await s.get(Job, "job")
        job.remote_state = "old_state"
        if cancelling:
            job.queue_status = "cancelling"
        await s.commit()
    async def status(*_):
        return receipt("running")
    monkeypatch.setattr(ex, "remote_status", status)
    async with store() as s:
        original_execute, original_commit = s.execute, s.commit
        raced = False
        async def race():
            nonlocal raced
            if raced:
                return
            raced = True
            async with store() as other:
                job = await other.get(Job, "job")
                job.status, job.queue_status = "queued", "preparing"
                job.remote_attempt_id, job.nextflow_run_id = "successor", "remote:successor"
                job.remote_state = "successor"
                await other.commit()
        async def execute(statement, *args, **kwargs):
            if getattr(statement, "is_update", False):
                await race()
            return await original_execute(statement, *args, **kwargs)
        async def commit():
            await race()
            await original_commit()
        monkeypatch.setattr(s, "execute", execute)
        monkeypatch.setattr(s, "commit", commit)
        await ex.reconcile_remote_job(s, await s.get(Job, "job"))
        assert raced
    async with store() as s:
        assert (await s.get(Job, "job")).remote_state == "successor"


@pytest.mark.asyncio
async def test_prestart_cancellation_intent_recovers_after_controller_crash(store):
    await preparing(store)
    async with store() as s:
        job = await s.get(Job, "job")
        job.queue_status = "cancelling"
        job.params = {"cancellation_receipt": {"state": "requested"}}
        await s.commit()
    async with store() as s:
        await ex.reconcile_remote_job(s, await s.get(Job, "job"))
    async with store() as s:
        assert (await s.get(Job, "job")).status == "cancelled"
        assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
async def test_uncertain_attempt_cannot_reenter_launch_or_prestart_failure(store, monkeypatch):
    await preparing(store)
    async with store() as s:
        job = await s.get(Job, "job")
        job.remote_attempt_id, job.nextflow_run_id, job.remote_state = "attempt", "remote:attempt", "launch_uncertain"
        await s.commit()
    calls = []
    async def target(*_):
        calls.append("preflight")
        raise ex.ExecutionTargetError("must not stage a successor")
    monkeypatch.setattr(ex, "get_ready_target", target)
    async with store() as s:
        with pytest.raises(ex.RemoteExecutionError, match="fresh durable preparing claim"):
            await ex.launch_remote_job(s, await s.get(Job, "job"), command=["true"],
                                       native_invocation=lifecycle_invocation(["true"]))
    assert calls == []
    async with store() as s:
        job = await s.get(Job, "job")
        assert not await ex.fail_remote_prestart(s, job, "delayed deterministic failure")
        assert job.status == "queued" and job.remote_state == "launch_uncertain"
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [None, "source", "lease", "device"])
async def test_fresh_admission_and_start_share_one_publication(store, monkeypatch, conflict):
    import json
    from datetime import timedelta
    from types import SimpleNamespace
    from services.remote_execution import targets, bundle as bundle_module

    await preparing(store)
    async def ready(session, *_):
        return await session.get(ExecutionTarget, "target")
    async def noop(*_, **__):
        pass
    monkeypatch.setattr(ex, "get_ready_target", ready)
    monkeypatch.setattr(ex.RemoteConnection, "from_target", lambda *_: None)
    monkeypatch.setattr(ex, "_verify_remote_runner", noop)
    monkeypatch.setattr(ex, "_stage_bundle", noop)
    monkeypatch.setattr(ex, "_archive_envelope", lambda *_: None)
    monkeypatch.setattr(ex, "_cleanup_local_bundle", lambda *_: None)
    monkeypatch.setattr(ex, "_worker_argv", lambda connection, command, *_: [command])
    bundle = SimpleNamespace(attempt_id="attempt", envelope_sha256="c" * 64,
        remote_attempt_dir="/attempt", envelope=SimpleNamespace(
            source_revision="a" * 40, source_tree="b" * 40,
            environment={"BMS_TARGET_RESOURCES": json.dumps({
                "required": {"cpus": 1, "memory_bytes": 1, "scratch_bytes": 0}, "gpu_ids": [0]})}))
    monkeypatch.setattr(ex, "prepare_remote_bundle", lambda **_: bundle)
    monkeypatch.setattr(bundle_module, "bind_resource_admission", lambda value, admission: value)
    monkeypatch.setattr(ex, "_remote_receipt", lambda b, t, **kw: {
        "state": kw["state"], "lease_acquired_at": t.lease_acquired_at.isoformat()})
    admissions = []
    async def admit(target, **requirements):
        value = {"devices": [{"gpu_index": 0, "gpu_uuid": "original"}],
                 "observed_at": str(len(admissions))}
        admissions.append(value)
        if len(admissions) == 2:
            if conflict == "device":
                value["devices"][0]["gpu_uuid"] = "replacement"
            elif conflict in {"source", "lease"}:
                async with store() as concurrent:
                    if conflict == "source":
                        (await concurrent.get(Job, "job")).execution_source_tree = "d" * 40
                    else:
                        (await concurrent.get(ExecutionTarget, "target")).lease_acquired_at = datetime.utcnow() + timedelta(seconds=1)
                    await concurrent.commit()
        return value
    monkeypatch.setattr(targets, "admit_target_resources", admit)
    publications, commands = [], []
    publish = ex._publish_remote_transition
    async def record_publication(session, job, values, **kwargs):
        publications.append(values)
        return await publish(session, job, values, **kwargs)
    monkeypatch.setattr(ex, "_publish_remote_transition", record_publication)
    async def remote(connection, argv, **kwargs):
        commands.append(argv[0])
        if argv[0] == "run":
            async with store() as observer:
                current = await observer.get(Job, "job")
                assert current.remote_state == "launch_requested"
                assert current.provenance["remote_execution_assignment"]["resources"]["admission"] == admissions[-1]
            return SimpleNamespace(stdout=receipt(state="running").model_dump_json())
        return SimpleNamespace(stdout="")
    monkeypatch.setattr(ex, "run_remote", remote)
    async with store() as session:
        if conflict:
            with pytest.raises(ex.RemoteExecutionError, match="changed|superseded"):
                await ex.launch_remote_job(session, await session.get(Job, "job"),
                    command=["true"], native_invocation=lifecycle_invocation(["true"]))
        else:
            assert await ex.launch_remote_job(session, await session.get(Job, "job"),
                command=["true"], native_invocation=lifecycle_invocation(["true"])) == "remote:attempt"
    assert len(admissions) == 2
    assert ("run" in commands) == (conflict is None)
    # Fresh admission must not create a separately committed intermediate state.
    assert all(set(values) != {"provenance"} for values in publications)
    if conflict in {"source", "lease"}:
        async with store() as session:
            assert (await session.get(ExecutionTarget, "target")).leased_job_id == "job"
            assert (await session.get(Job, "job")).remote_state == "staging"


def receipt(state="succeeded", job_id="job", attempt_id="attempt"):
    return RemoteAttemptStatus(job_id=job_id, attempt_id=attempt_id, state=state,
                               boot_id="test-boot", quiescent=True,
                               exit_code=0, started_at=datetime.utcnow(), completed_at=datetime.utcnow())


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["succeeded", "failed", "lost", "cancelled"])
async def test_cancellation_intent_survives_remote_terminal(store, monkeypatch, terminal):
    async def status(*_):
        async with store() as other:
            current = await other.get(Job, "job")
            current.queue_status = "cancelling"
            current.params = {"cancellation_receipt": {"state": "requested"}}
            await other.commit()
        return receipt(terminal).model_copy(update={"result_manifest_sha256": "a" * 64})
    monkeypatch.setattr(ex, "remote_status", status)
    async with store() as s:
        await ex.reconcile_remote_job(s, await s.get(Job, "job"))
    async with store() as s:
        job = await s.get(Job, "job")
        assert (job.status, job.queue_status) == ("cancelled", "cancelled")
        assert job.provenance["remote_execution_receipt"]["state"] == terminal
        assert job.provenance["remote_execution_receipt"]["result_manifest_sha256"] == "a" * 64
        assert (await s.get(ExecutionTarget, "target")).leased_job_id is None


@pytest.mark.asyncio
async def test_wrong_cancel_receipt_is_not_confirmation(store, monkeypatch):
    monkeypatch.setattr(ex, "_connection_for_attempt", lambda *_: (None, "/attempt"))
    monkeypatch.setattr(ex, "_worker_argv", lambda *_: [])
    async def run(*_, **__):
        return SimpleNamespace(stdout=receipt("cancelled", job_id="other").model_dump_json())
    monkeypatch.setattr(ex, "run_remote", run)
    async with store() as s:
        assert not await ex.cancel_remote_job(await s.get(Job, "job"))


@pytest.mark.asyncio
async def test_terminal_without_quiescence_retains_target_lease(store, monkeypatch):
    async def status(*_):
        return receipt("cancelled").model_copy(update={"quiescent": False})
    monkeypatch.setattr(ex, "remote_status", status)
    async with store() as s:
        job = await s.get(Job, "job")
        job.queue_status = "cancelling"
        await s.commit()
        assert not await ex.reconcile_remote_job(s, job)
    async with store() as s:
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"
        assert (await s.get(Job, "job")).queue_status == "cancelling"


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
async def test_bulk_cancel_uses_owned_quiescence_and_attempt_cas(store, monkeypatch, replacement):
    from routers.queue import cancel_all_queued
    from services import job_control
    async with store() as session:
        job = await session.get(Job, "job")
        job.queue_status = "paused"
        await session.commit()
    async def stop(run_id):
        assert run_id == "remote:attempt"
        if replacement:
            async with store() as other:
                job = await other.get(Job, "job")
                job.remote_attempt_id = "successor"
                await other.commit()
        return True
    monkeypatch.setattr(job_control, "cancel_nextflow_job", stop)
    async with store() as session:
        if replacement:
            with pytest.raises(HTTPException) as error:
                await cancel_all_queued(session)
            assert error.value.status_code == 409
        else:
            result = await cancel_all_queued(session)
            assert result["cancelled_count"] == 1
    async with store() as session:
        target = await session.get(ExecutionTarget, "target")
        job = await session.get(Job, "job")
        assert target.leased_job_id == ("job" if replacement else None)
        assert job.remote_attempt_id == ("successor" if replacement else "attempt")


def test_existing_attempt_control_ignores_new_admission_readiness():
    target = ExecutionTarget(id="target", provider="vast", provider_instance_id="1",
                             host="worker", port=22, username="root", remote_root="/worker",
                             state="inactive", active=False, capabilities={})
    job = Job(id="job", remote_attempt_id="attempt", provenance={"remote_execution_receipt": {
        "attempt_id": "attempt", "execution_target_id": "target", "ssh_host": "worker",
        "remote_root": "/retained", "remote_attempt_dir": "/retained/attempts/attempt"}})
    connection, path = ex._connection_for_attempt(target, job)
    assert connection.remote_root == "/retained" and path == "/retained/attempts/attempt"
    target.host = "replacement"
    with pytest.raises(ex.RemoteExecutionError, match="identity changed"):
        ex._connection_for_attempt(target, job)


@pytest.mark.asyncio
async def test_reacquired_lease_epoch_blocks_old_attempt_release(store):
    async with store() as s:
        job = await s.get(Job, "job")
        target = await s.get(ExecutionTarget, "target")
        job.provenance = {"remote_execution_receipt": {
            "lease_acquired_at": (target.lease_acquired_at - timedelta(seconds=1)).isoformat()}}
        await s.commit()
        assert not await ex._finish_remote_cancellation(s, job, receipt("cancelled"))
    async with store() as s:
        assert (await s.get(ExecutionTarget, "target")).leased_job_id == "job"
