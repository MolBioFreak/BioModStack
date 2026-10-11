"""Real private filesystem/SQLite return generations; no workers or providers."""
import asyncio
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from database import Job
from services.remote_execution import executor as ex, result_generation as gen
from services.remote_execution.contracts import RemoteResultManifest
from test_remote_lifecycle_gaps import store
from test_remote_manual_result_pull import success


def package(job, payloads, generation=1):
    status = success().model_copy(update={"generation": generation})
    manifest = RemoteResultManifest(job_id=job.id, attempt_id=job.remote_attempt_id,
        source_revision=job.execution_source_revision, source_tree=job.execution_source_tree,
        execution_envelope_sha256=job.execution_bundle_sha256, generation=generation,
        artifacts=[dict(relative_path=name, size_bytes=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), role="result")
                   for name, data in payloads.items()], exit_code=0, completed_at=status.completed_at)
    raw = manifest.model_dump_json().encode()
    digest = hashlib.sha256(raw).hexdigest()
    incoming = gen.staging_path(job, digest)
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / "result-manifest.json").write_bytes(raw)
    for name, data in payloads.items():
        path = incoming / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return manifest, incoming, status.model_copy(update={"result_manifest_sha256": digest})


async def initial(store, tmp_path, payloads):
    async with store() as session:
        job = await session.get(Job, "job")
        job.output_dir = str(tmp_path / "published")
        await session.commit()
        manifest, incoming, status = package(job, payloads)
        ex._publish_result_generation(job, incoming)
        await session.commit()
        await ex._recover_result_generation(session, job)
    return manifest


@pytest.mark.asyncio
async def test_changed_only_transfer_and_private_import_leaves(store, tmp_path, monkeypatch, record_property):
    old = {"structure.pdb": b"old scientific structure", "metrics.json": b'{"old":1}', "changed.pdb": b"old"}
    await initial(store, tmp_path, old)
    new = dict(old, **{"changed.pdb": b"new", "extra.fasta": b">seq\nACD\n"})
    async with store() as session:
        job = await session.get(Job, "job")
        manifest, prepared, status = package(job, new, 2)
        raw = (prepared / "result-manifest.json").read_text()
        import shutil
        shutil.rmtree(prepared)
        async def remote(*args, **kwargs):
            return SimpleNamespace(stdout=raw)
        transfers = []
        async def transfer(connection, remote, incoming, paths, **kwargs):
            transfers.extend(paths)
            for name in paths:
                (incoming / name).write_bytes(new[name])
        monkeypatch.setattr(ex, "run_remote", remote)
        monkeypatch.setattr(ex, "rsync_selected_from_remote", transfer)
        monkeypatch.setattr(ex, "_connection_for_attempt", lambda *a: (None, "/fixture/attempt"))
        manifest, incoming = await ex.collect_remote_results(session, job, status)
        assert sorted(transfers) == ["changed.pdb", "extra.fasta"]
        output = Path(job.output_dir)
        assert (incoming / "structure.pdb").stat().st_ino == (output / "structure.pdb").stat().st_ino
        assert (incoming / "metrics.json").stat().st_ino != (output / "metrics.json").stat().st_ino
        (incoming / "metrics.json").write_bytes(b"import mutated")
        assert (output / "metrics.json").read_bytes() == old["metrics.json"]
        record_property("transferred_payload_bytes", sum(len(new[x]) for x in transfers))
        record_property("unchanged_transferred_payload_bytes", 0)
        record_property("immutable_payload_distinct_inodes", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["prepared", "prior_moved", "new_moved", "import", "flushed", "commit"])
async def test_real_publisher_sqlite_fault_boundaries(store, tmp_path, monkeypatch, fault):
    old = {"structure.pdb": b"same", "unique-old.pdb": b"old science"}
    await initial(store, tmp_path, old)
    async with store() as session:
        job = await session.get(Job, "job")
        _, incoming, _ = package(job, {"structure.pdb": b"same", "new.pdb": b"new science"}, 2)
        backup = incoming.with_name(incoming.name + ".previous")
        def crash(name):
            if name == fault:
                raise RuntimeError("injected " + name)
        monkeypatch.setattr(gen, "_checkpoint", crash)
        try:
            ex._publish_result_generation(job, incoming)
            if fault == "import":
                (Path(job.output_dir) / "import-private.json").write_text("partial import")
                raise RuntimeError("injected import")
            if fault == "flushed":
                await session.flush()  # not durable authority!
                raise RuntimeError("injected precommit")
            if fault == "commit":
                await session.commit()
                raise RuntimeError("injected after durable commit")
        except RuntimeError:
            pass
        monkeypatch.setattr(gen, "_checkpoint", lambda name: None)
        # Recovery itself discards the pending transaction before readback.
        assert await ex._recover_result_generation(session, job)
        assert not await ex._recover_result_generation(session, job)
    async with store() as fresh:
        job = await fresh.get(Job, "job")
        output = Path(job.output_dir)
        if fault == "commit":
            assert (output / "new.pdb").read_bytes() == b"new science"
            assert (backup / "unique-old.pdb").read_bytes() == b"old science"
        else:
            assert (output / "unique-old.pdb").read_bytes() == b"old science"
            assert not (output / "new.pdb").exists()
            assert incoming.exists()
        assert job.remote_attempt_id == "attempt"
        assert job.nextflow_run_id == "remote:attempt"
        assert job.execution_target_id == "target"


@pytest.mark.asyncio
async def test_redundant_previous_removed_only_after_commit_and_recovery_repeats(store, tmp_path, monkeypatch):
    payloads = {"structure.pdb": b"same"}
    await initial(store, tmp_path, payloads)
    async with store() as session:
        job = await session.get(Job, "job")
        _, incoming, _ = package(job, payloads, 2)
        _, backup = ex._publish_result_generation(job, incoming)
        assert backup.is_dir()
        job.provenance = dict(job.provenance, remote_execution_receipt={
            "previous_generation_quarantine": str(backup)})
        await session.commit()
        assert backup.is_dir()  # publication/commit do not themselves delete it
        original = gen.shutil.rmtree
        monkeypatch.setattr(gen.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("cleanup interrupted")))
        assert not await ex._recover_result_generation(session, job)
        assert gen.journal_path(job).exists()
        monkeypatch.setattr(gen.shutil, "rmtree", original)
        assert await ex._recover_result_generation(session, job)
        assert not backup.exists()
        assert job.provenance["remote_execution_receipt"]["previous_generation_quarantine"] is None
        assert not await ex._recover_result_generation(session, job)
    async with store() as fresh:
        job = await fresh.get(Job, "job")
        assert (Path(job.output_dir) / "structure.pdb").read_bytes() == b"same"
        assert job.remote_attempt_id == "attempt"  # no terminal-attempt sweep


@pytest.mark.asyncio
async def test_uncommitted_or_changed_donor_never_reused(store, tmp_path):
    await initial(store, tmp_path, {"structure.pdb": b"same"})
    async with store() as session:
        job = await session.get(Job, "job")
        _, incoming, _ = package(job, {"structure.pdb": b"same"}, 2)
        artifact = gen.committed_payloads(job)["structure.pdb"]
        destination = incoming / "borrow.pdb"
        (Path(job.output_dir) / "structure.pdb").write_bytes(b"evil")
        assert not gen.reuse_payload(job, artifact, {artifact.relative_path: artifact}, destination)
        job.provenance = {}
        assert gen.committed_payloads(job) == {}
        assert not destination.exists()


@pytest.mark.asyncio
async def test_cancelled_thread_drains_before_owned_cleanup(tmp_path):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    stage = tmp_path / "owned-staging"
    stage.mkdir()
    def writer():
        started.set()
        assert release.wait(10)
        (stage / "payload").write_bytes(b"complete")
        finished.set()
    async def owner():
        try:
            await ex._joined_thread(writer)
        finally:
            assert finished.is_set()
            ex._cleanup_local_bundle(SimpleNamespace(local_attempt_dir=stage / "attempt"))
    task = asyncio.create_task(owner())
    while not started.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    assert stage.exists()
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set() and not stage.exists()

@pytest.mark.asyncio
async def test_crash_after_retirement_readback_is_idempotent(store, tmp_path, monkeypatch):
    payloads = {"structure.pdb": b"one retained body"}
    await initial(store, tmp_path, payloads)
    async with store() as session:
        job = await session.get(Job, "job")
        _, incoming, _ = package(job, payloads, 2)
        _, backup = ex._publish_result_generation(job, incoming)
        job.status = job.queue_status = "completed"
        await session.commit()
        def crash(name):
            if name == "previous_retired":
                raise RuntimeError("postcommit interruption")
        monkeypatch.setattr(gen, "_checkpoint", crash)
        with pytest.raises(RuntimeError, match="postcommit interruption"):
            await ex._recover_result_generation(session, job)
        await session.rollback()
        assert not backup.exists()
    monkeypatch.setattr(gen, "_checkpoint", lambda name: None)
    async with store() as fresh:
        job = await fresh.get(Job, "job")
        assert await ex._recover_result_generation(fresh, job)
        assert not await ex._recover_result_generation(fresh, job)
        assert job.status == "completed"
        assert job.nextflow_run_id == "remote:attempt"
        assert (Path(job.output_dir) / "structure.pdb").read_bytes() == payloads["structure.pdb"]


@pytest.mark.asyncio
async def test_cross_filesystem_projection_is_private_without_retransfer(store, tmp_path, monkeypatch):
    import errno
    await initial(store, tmp_path, {"structure.pdb": b"same"})
    async with store() as session:
        job = await session.get(Job, "job")
        previous = gen.committed_payloads(job)
        artifact = previous["structure.pdb"]
        def cross_device(*args, **kwargs):
            raise OSError(errno.EXDEV, "fixture cross-device boundary")
        monkeypatch.setattr(gen.os, "link", cross_device)
        destination = tmp_path / "projection" / "structure.pdb"
        assert gen.reuse_payload(job, artifact, previous, destination)
        assert destination.stat().st_ino != (Path(job.output_dir) / "structure.pdb").stat().st_ino
        assert destination.read_bytes() == b"same"
