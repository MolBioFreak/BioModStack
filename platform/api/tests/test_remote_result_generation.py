"""Offline crash/retry tests of the integrated return path and durable journal."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import text

from database import Job
from services.remote_execution import executor as ex, result_generation as gen
from services.remote_execution.contracts import RemoteResultManifest
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import ready, success


def job_at(root):
    return SimpleNamespace(id="job", remote_attempt_id="attempt", execution_target_id="target",
        execution_source_revision="a" * 40, execution_source_tree="b" * 40,
        execution_bundle_sha256="c" * 64, output_dir=str(root / "output"),
        child_output_dir=None, provenance={})


def package(job, *, state="succeeded"):
    status = success().model_copy(update={"state": state, "exit_code": 0 if state == "succeeded" else 1})
    artifacts = [dict(relative_path=name, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), role="result")
                 for name, data in [("first.txt", b"first"), ("second.txt", b"second")]]
    assert status.exit_code is not None and status.completed_at is not None
    manifest = RemoteResultManifest(job_id=job.id, attempt_id=job.remote_attempt_id,
        source_revision=job.execution_source_revision, source_tree=job.execution_source_tree,
        execution_envelope_sha256=job.execution_bundle_sha256, artifacts=artifacts,
        exit_code=status.exit_code, completed_at=status.completed_at)
    encoded = manifest.model_dump_json().encode()
    digest = hashlib.sha256(encoded).hexdigest()
    incoming = gen.staging_path(job, digest)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / "result-manifest.json").write_bytes(encoded)
    (incoming / "first.txt").write_bytes(b"first")
    (incoming / "second.txt").write_bytes(b"second")
    return manifest, incoming, status.model_copy(update={"result_manifest_sha256": digest})


@pytest.mark.parametrize("point", ["prepared", "prior_moved", "new_moved"])
@pytest.mark.parametrize("prior", [False, True])
def test_process_death_at_each_publication_boundary(tmp_path, point, prior):
    job = job_at(tmp_path)
    output = Path(job.output_dir)
    if prior:
        output.mkdir()
        (output / "old.txt").write_text("good")
    _, incoming, _ = package(job)
    pid = os.fork()
    if pid == 0:
        gen._checkpoint = lambda name: os._exit(73) if name == point else None
        gen.publish(job, incoming)
        os._exit(74)
    _, code = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(code) == 73
    assert gen.recover(job)
    assert not gen.recover(job)
    assert (incoming / "first.txt").read_text() == "first"
    assert output.exists() == prior
    if prior:
        assert (output / "old.txt").read_text() == "good"
    gen.publish(job, incoming)
    # The parent has not committed these ORM deltas; emulate DB rollback.
    job.provenance = {}
    gen.recover(job)
    assert (incoming / "second.txt").read_text() == "second"


@pytest.mark.parametrize("point", ["rollback_new", "rollback_prior"])
def test_process_death_during_recovery_is_repeatable(tmp_path, point):
    job = job_at(tmp_path)
    output = Path(job.output_dir)
    output.mkdir()
    (output / "old.txt").write_text("good")
    _, incoming, _ = package(job)
    gen.publish(job, incoming)
    job.provenance = {}
    pid = os.fork()
    if pid == 0:
        gen._checkpoint = lambda name: os._exit(73) if name == point else None
        gen.recover(job)
        os._exit(74)
    _, code = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(code) == 73
    gen.recover(job)
    assert (output / "old.txt").read_text() == "good"
    assert (incoming / "first.txt").read_text() == "first"


def test_committed_generation_recovery_keeps_new_and_retains_prior(tmp_path):
    job = job_at(tmp_path)
    output = Path(job.output_dir)
    output.mkdir()
    (output / "old.txt").write_text("good")
    _, incoming, _ = package(job)
    _, backup = gen.publish(job, incoming)
    durable_job = SimpleNamespace(**vars(job),)  # provenance loaded after a commit
    gen.recover(durable_job)
    assert (output / "first.txt").read_text() == "first"
    assert (backup / "old.txt").read_text() == "good"
    assert not gen.recover(durable_job)


@pytest.mark.parametrize("field", ["remote_attempt_id", "execution_target_id", "execution_source_tree", "execution_bundle_sha256"])
def test_identity_change_cannot_reuse_or_recover_generation(tmp_path, field):
    job = job_at(tmp_path)
    _, incoming, status = package(job)
    gen.publish(job, incoming)
    setattr(job, field, "successor")
    assert gen.staging_path(job, status.result_manifest_sha256) != incoming
    with pytest.raises(gen.GenerationError, match="different attempt"):
        gen.recover(job)
    assert (Path(job.output_dir) / "first.txt").exists()


@pytest.mark.asyncio
async def test_transport_disconnect_reclaims_partial_and_reuses_verified_bytes(store, tmp_path, monkeypatch):
    async with store() as session:
        job = await session.get(Job, "job")
        job.output_dir = str(tmp_path / "output")
        await session.commit()
    manifest, incoming, status = package(job)
    encoded = (incoming / "result-manifest.json").read_text()
    (incoming / "second.txt").unlink()
    async def run(*args, **kwargs):
        return SimpleNamespace(stdout=encoded)
    monkeypatch.setattr(ex, "run_remote", run)
    monkeypatch.setattr(ex, "_connection_for_attempt", lambda *args: (None, "/remote/attempt"))
    calls = []
    async def transfer(connection, remote, destination, files, **kwargs):
        assert destination == incoming
        assert files == ["second.txt"]
        calls.append(files)
        if len(calls) == 1:
            (destination / "second.txt").write_text("sec")
            raise ex.RemoteTransportError("disconnect")
        assert not (destination / "second.txt").exists()
        (destination / "second.txt").write_text("second")
    monkeypatch.setattr(ex, "rsync_selected_from_remote", transfer)
    async with store() as session:
        job = await session.get(Job, "job")
        with pytest.raises(ex.RemoteCollectionPending, match="disconnect"):
            await ex.collect_remote_results(session, job, status)
    # Budget excludes retained verified bytes; only missing file + manifest needed.
    monkeypatch.setattr(ex.shutil, "disk_usage", lambda _: SimpleNamespace(free=ex.RESULT_DISK_RESERVE_BYTES + len(encoded.encode()) + 6))
    async with store() as session:
        job = await session.get(Job, "job")
        returned, staged = await ex.collect_remote_results(session, job, status)
        assert returned == manifest and staged == incoming and len(calls) == 2
        await ex.collect_remote_results(session, job, status)
    assert len(calls) == 2
    assert list(incoming.parent.iterdir()) == [incoming]


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["manifest", "symlink", "undeclared"])
async def test_retained_staging_fails_closed(tmp_path, monkeypatch, corruption):
    job = job_at(tmp_path)
    _, incoming, status = package(job)
    encoded = (incoming / "result-manifest.json").read_text()
    if corruption == "manifest":
        (incoming / "result-manifest.json").write_text("{}")
    elif corruption == "symlink":
        (incoming / "first.txt").unlink()
        (incoming / "first.txt").symlink_to(tmp_path / "outside")
    else:
        (incoming / "undeclared").write_text("untrusted")
    async def run(*args, **kwargs): return SimpleNamespace(stdout=encoded)
    monkeypatch.setattr(ex, "run_remote", run)
    with pytest.raises((ex.RemoteExecutionError, gen.GenerationError)):
        await ex._fetch_result_manifest(None, "/remote", incoming, job, status)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_import", [False, True])
@pytest.mark.parametrize("pull_mode", ["manual", "automatic"])
async def test_real_native_finalizer_transaction_and_explicit_retry(store, tmp_path, monkeypatch, fail_import, pull_mode):
    from services import result_ingester, analysis_autorun
    await ready(store)
    async with store() as session:
        job = await session.get(Job, "job")
        job.output_dir = str(tmp_path / "output")
        job.model_id = "custom_file_workflow"
        job.params = dict(job.params or {}, remote_result_policy=pull_mode)
        manifest, incoming, status = package(job)
        Path(job.output_dir).mkdir()
        (Path(job.output_dir) / "old.txt").write_text("good")
        contract = ex.resolve_job_result_contract(job)
        job.provenance = {"remote_execution_receipt": {
            **{key: value for key, value in ex._pull_identity(job).items() if key != "schema"},
            "state": status.state, "exit_code": status.exit_code,
            "remote_attempt_dir": "/fixture/attempt",
            "result_manifest_sha256": status.result_manifest_sha256,
            "expected_result_contract_sha256": hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}}
        await session.execute(text("CREATE TABLE test_projection (id TEXT PRIMARY KEY)"))
        await session.commit()
    attempts = []
    async def ingest(job_id, output_dir, session, **kwargs):
        assert kwargs["commit"] is False
        assert (Path(output_dir) / "first.txt").read_text() == "first"
        await session.execute(text("INSERT INTO test_projection VALUES ('native-row')"))
        attempts.append(1)
        if fail_import and len(attempts) == 1:
            raise RuntimeError("semantic validation failed")
        return 0
    async def proof(*args):
        assert not attempts, "Verified import retry must not require a provider"
    async def remote_status(*args):
        assert not attempts, "Verified import retry must not contact the worker"
        return status
    async def collect(*args):
        assert not attempts, "Verified import retry must reuse its received bytes"
        ex._verify_result_package(incoming, args[1], status)
        return manifest, incoming
    monkeypatch.setattr(result_ingester, "ingest_job_results", ingest)
    monkeypatch.setattr(analysis_autorun, "schedule_viewer_minimum_analyses_for_job", lambda *_: None)
    monkeypatch.setattr(ex, "_prove_pull_endpoint", proof)
    monkeypatch.setattr(ex, "remote_status", remote_status)
    monkeypatch.setattr(ex, "collect_remote_results", collect)
    for retry in range(2 if fail_import else 1):
        tasks = BackgroundTasks()
        async with store() as session:
            job = await session.get(Job, "job")
            if pull_mode == "automatic" and retry == 0:
                assert await ex.reconcile_remote_job(session, job, background_tasks=tasks)
            else:
                # A failed import never triggers automatic transfer or science
                # retries, even though the opt-in remains persisted.
                assert not await ex.reconcile_remote_job(session, job, background_tasks=BackgroundTasks())
                await ex.request_remote_result_pull(session, job, tasks)
        await tasks()
        async with store() as session:
            job = await session.get(Job, "job")
            count = await session.scalar(text("SELECT count(*) FROM test_projection"))
            if fail_import and retry == 0:
                assert count == 0 and job.remote_state == "result_pull_failed"
                assert (Path(job.output_dir) / "old.txt").read_text() == "good"
                assert (incoming / "first.txt").read_text() == "first"
                assert "remote_result_generation" not in job.provenance
            else:
                assert count == 1 and job.remote_state == "ingested", (
                    count, job.remote_state, job.error_message, attempts,
                    (job.provenance or {}).get("remote_execution_receipt"))
                assert job.status == "completed"
                assert (Path(job.output_dir) / "first.txt").read_text() == "first"
                assert "remote_result_generation" in job.provenance
                assert not gen.journal_path(job).exists()
                with pytest.raises(ex.RemoteExecutionError):
                    await ex.request_remote_result_pull(session, job, BackgroundTasks())


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_process_death_before_and_after_database_commit(store, tmp_path, committed):
    import sqlite3

    await ready(store)
    async with store() as session:
        job = await session.get(Job, "job")
        job.output_dir = str(tmp_path / "output")
        job.status = job.queue_status = "running"
        job.remote_state = "returning"
        _, incoming, _ = package(job)
        Path(job.output_dir).mkdir()
        (Path(job.output_dir) / "old.txt").write_text("good")
        await session.execute(text("CREATE TABLE test_projection (id TEXT PRIMARY KEY)"))
        await session.commit()
        snapshot = job_at(tmp_path)
        snapshot.provenance = dict(job.provenance)
    pid = os.fork()
    if pid == 0:
        # Real SQLite transaction in a separate process; abrupt exit bypasses
        # both Python rollback and filesystem exception handlers.
        conn = sqlite3.connect(tmp_path / "state.sqlite")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO test_projection VALUES ('native-row')")
        gen.publish(snapshot, incoming)
        conn.execute("UPDATE jobs SET provenance=?, remote_state='ingested', status='completed', queue_status='completed' WHERE id='job'",
                     (json.dumps(snapshot.provenance),))
        if committed:
            conn.commit()
        os._exit(73)
    _, code = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(code) == 73
    async with store() as session:
        job = await session.get(Job, "job")
        await ex.reconcile_remote_job(session, job)
        assert await session.scalar(text("SELECT count(*) FROM test_projection")) == int(committed)
        assert not gen.journal_path(job).exists()
        if committed:
            assert job.remote_state == "ingested"
            assert (Path(job.output_dir) / "first.txt").read_text() == "first"
        else:
            assert job.remote_state == "result_pull_failed"
            assert (Path(job.output_dir) / "old.txt").read_text() == "good"
            assert (incoming / "first.txt").read_text() == "first"


def test_api_death_during_transport_cannot_reuse_possible_live_writer(tmp_path):
    job = job_at(tmp_path)
    _, incoming, _ = package(job)
    pid = os.fork()
    if pid == 0:
        gen.begin_transfer(incoming)
        os._exit(73)
    _, code = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(code) == 73
    with pytest.raises(gen.GenerationError, match="writer-quiescence"):
        gen.prepare_transfer(incoming)
    # A different kernel boot proves that local predecessor writers are gone.
    gen.durable_json(gen.transfer_marker(incoming), {"boot_id": "00000000-0000-0000-0000-000000000000"})
    gen.prepare_transfer(incoming)
    assert not gen.transfer_marker(incoming).exists()


@pytest.mark.asyncio
async def test_md_dispatch_reaches_native_completion_barrier(store, tmp_path, monkeypatch):
    from services.md import completion
    from services import remote_stage_receipts

    async with store() as session:
        job = await session.get(Job, "job")
        job.model_id, job.mode = "molecular_dynamics", "simulate"
        job.output_dir = str(tmp_path / "output")
        job.remote_state = "returning"
        manifest, incoming, status = package(job)
        contract = ex.resolve_job_result_contract(job)
        job.provenance = {"remote_execution_receipt": {"remote_attempt_dir": "/fixture/attempt", "expected_result_contract_sha256":
            hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}}
        await session.commit()
        called = []
        native = completion.validate_and_finalize_md_job
        async def barrier(job, session):
            called.append("native")
            return await native(job, session)
        async def generic(**kwargs):
            pytest.fail("MD must not project generic lifecycle metadata")
        monkeypatch.setattr(completion, "validate_and_finalize_md_job", barrier)
        monkeypatch.setattr(remote_stage_receipts, "apply_remote_stage_receipts", generic)
        # An incomplete fixture reaches and is rejected by the real native
        # scientific barrier; bypassing the generic guard is not MD acceptance.
        with pytest.raises(Exception):
            await ex._finalize_pulled_results(session, job, status, manifest, incoming)
        assert called == ["native"]
        await session.rollback()
        job = await session.get(Job, "job")
        await ex._recover_result_generation(session, job)
        assert incoming.exists() and not Path(job.output_dir).exists()
