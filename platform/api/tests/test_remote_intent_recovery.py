"""Deferred offline F2 crash/interleaving contracts, not live cgroup evidence.

Do not interpret fixture receipts/counters as scientific acceptance. These tests
exercise the production immutable resource triggers and generation protocol.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from services import remote_resource_evidence as evidence
from services.remote_execution import executor
from tools import bms_remote_worker as worker
from tests.test_remote_lifecycle_gaps import delivery_resources, remote_readiness, store, preparing


def intent_for(policy, *, path=None):
    attempt = str(uuid.uuid4())
    return {"schema": "bms.remote-attempt-intent.v1", "job_id": "job", "attempt_id": attempt,
        "target_id": policy["target_id"], "machine_id": policy["machine_id"],
        "storage_device": policy["storage_device"],
        "storage_path": path or policy["storage_root"] + "/attempts/" + attempt,
        "policy": policy, "staging_reservation_id": uuid.uuid4().hex,
        "compute_reservation_id": uuid.uuid4().hex, "staging_disk_bytes": 100000}


def nonexecution(intent, size=1234, phase="fenced"):
    return {"schema": "bms.remote-attempt-fence.v1", "intent": intent,
        "intent_sha256": evidence.digest(intent), "machine_id": intent["machine_id"],
        "boot_id": str(uuid.uuid4()), "nonexecution": True, "resource_receipt": None,
        "quiescent": True, "resident_disk_bytes": size, "phase": phase, "boundary_cleanup": "complete"}


def reserve(resources, intent, *, compute=False):
    return resources.reserve_remote_attempt(target_id=intent["target_id"], policy=intent["policy"],
        attempt_id=intent["attempt_id"], job_id=intent["job_id"], storage_path=intent["storage_path"],
        reservation_id=intent["compute_reservation_id"] if compute else intent["staging_reservation_id"],
        staging_disk_bytes=None if compute else intent["staging_disk_bytes"],
        staging_reservation_id=intent["staging_reservation_id"] if compute else None)


@pytest.mark.parametrize("boundary", ["before_allocation", "after_staging_insert", "after_compute_insert"])
def test_allocation_publication_crashes_fence_missing_and_present_ids(delivery_resources, boundary):
    resources, db = delivery_resources
    policy = resources.publish_execution_target_readiness(target_id="cloud", remote_root="/worker",
        readiness=remote_readiness("/worker"))
    intent = intent_for(policy)
    if boundary != "before_allocation":
        reserve(resources, intent)
    if boundary == "after_compute_insert":
        reserve(resources, intent, compute=True)
    identities = dict(db.execute("SELECT reservation_id,receipt_json FROM derived_resource_reservations"))
    proof = nonexecution(intent)
    resources.reconcile_remote_storage(intent, proof)
    resources.reconcile_remote_storage(intent, proof)
    rows = db.execute("SELECT * FROM derived_resource_reservations").fetchall()
    assert sum(r["cpu_threads"] for r in rows) == sum(r["dram_bytes"] for r in rows) == 0
    assert sum(r["disk_bytes"] for r in rows) == proof["resident_disk_bytes"]
    for row in rows:
        if row["reservation_id"] in identities:
            assert row["receipt_json"] == identities[row["reservation_id"]]
    # A controller delayed past the remote fence cannot insert/revive compute.
    with pytest.raises(resources.ResourceCapacityUnavailable):
        reserve(resources, intent, compute=True)
    with pytest.raises(resources.ResourceCapacityUnavailable):
        reserve(resources, intent)


def test_staging_owns_only_bounded_disk_and_does_not_block_unrelated_compute(delivery_resources):
    resources, db = delivery_resources
    policy = resources.publish_execution_target_readiness(target_id="cloud", remote_root="/worker",
        readiness=remote_readiness("/worker"))
    staged = intent_for(policy)
    receipt = reserve(resources, staged)
    assert receipt["effective"] == {"cpu_threads": 0, "dram_bytes": 0, "disk_bytes": 100000}
    other = intent_for(policy)
    reserve(resources, other)
    science = reserve(resources, other, compute=True)
    before = dict(db.execute("SELECT * FROM derived_resource_reservations WHERE reservation_id=?",
        (science["reservation_id"],)).fetchone())
    resources.reconcile_remote_storage(staged, nonexecution(staged))
    after = dict(db.execute("SELECT * FROM derived_resource_reservations WHERE reservation_id=?",
        (science["reservation_id"],)).fetchone())
    assert before == after
    assert science["effective"]["cpu_threads"] == policy["cpu_threads"]


def test_removed_accounting_is_idempotent_and_rejects_old_observation(delivery_resources):
    resources, db = delivery_resources
    policy = resources.publish_execution_target_readiness(target_id="cloud", remote_root="/worker",
        readiness=remote_readiness("/worker"))
    intent = intent_for(policy)
    reserve(resources, intent)
    resources.reconcile_remote_storage(intent, nonexecution(intent, 1000))
    proof = nonexecution(intent, 100, "removed")  # Permanent owner metadata remains.
    resources.reconcile_remote_storage(intent, proof)
    resources.reconcile_remote_storage(intent, proof)
    assert db.execute("SELECT SUM(disk_bytes) FROM derived_resource_reservations").fetchone()[0] == 100
    with pytest.raises(resources.ResourceCapacityUnavailable, match="stale"):
        resources.reconcile_remote_storage(intent, nonexecution(intent, 1000))


@pytest.fixture
def remote_owner(tmp_path):
    policy = {"target_id": "cloud", "storage_root": str(tmp_path),
        "storage_device": str(tmp_path.stat().st_dev),
        "machine_id": Path("/etc/machine-id").read_text().strip()}
    intent = intent_for(policy)
    attempt = Path(intent["storage_path"])
    attempt.parent.mkdir()
    worker.initialize_owner(attempt, intent)
    return attempt, intent


@pytest.mark.parametrize("phase", ["staging", "armed"])
def test_fence_prevents_late_receive_and_supervisor_launch(remote_owner, phase):
    attempt, intent = remote_owner
    root, path = worker.owner_paths(attempt)
    record = worker.load_json(path)
    record["phase"] = phase
    worker.atomic_json(path, record)
    proof = worker.fence_owner(attempt, evidence.digest(intent))
    evidence.validate_fence(intent, proof)
    assert proof["nonexecution"] and proof["resource_receipt"] is None
    assert proof["resident_disk_bytes"] == worker.owned_disk_bytes(root)
    with pytest.raises(RuntimeError, match="fenced"):
        worker.stage_command(attempt, evidence.digest(intent), ["must-not-exec"])
    with pytest.raises(RuntimeError, match="fenced"):
        worker.supervise(attempt)
    assert worker.initialize_owner(attempt, intent)["phase"] == "fenced"


def test_live_remote_receiver_lock_refuses_fence_without_inference(remote_owner):
    attempt, intent = remote_owner
    with worker.owner_guard(attempt):
        with pytest.raises(BlockingIOError):
            worker.fence_owner(attempt, evidence.digest(intent), remove=True)
    assert worker.load_json(worker.owner_paths(attempt)[1])["phase"] == "staging"


@pytest.mark.parametrize("interruption", ["before_identity", "after_identity", "stop_failure"])
def test_interrupted_boundary_creation_preserves_original_invocation(remote_owner, monkeypatch, interruption):
    attempt, intent = remote_owner
    _, path = worker.owner_paths(attempt)
    record = worker.load_json(path)
    original = "a" * 32
    record.update(phase="armed", boundary_creation={
        "unit": "bms-attempt-" + intent["attempt_id"] + ".service",
        "marker": "bms-owner:" + evidence.digest(intent),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "invocation_id": None if interruption == "before_identity" else original})
    worker.atomic_json(path, record)
    stops = []
    fail = interruption == "stop_failure"
    def control(self, *args):
        nonlocal fail
        if args[0] == "stop":
            stops.append(args[1])
            if fail:
                fail = False
                raise RuntimeError("manager stop interrupted")
            return ""
        return {"--property=LoadState": "loaded", "--property=Description": record["boundary_creation"]["marker"],
            "--property=InvocationID": original}[args[2]]
    monkeypatch.setattr(worker.OwnedBoundary, "control", control)
    if fail:
        pending = worker.fence_owner(attempt, evidence.digest(intent))
        assert pending["boundary_cleanup"] == "pending" and pending["nonexecution"]
        evidence.validate_fence(intent, pending)
    proof = worker.fence_owner(attempt, evidence.digest(intent))
    assert proof["nonexecution"]
    saved = worker.load_json(path)
    assert saved["boundary_creation"]["invocation_id"] == original
    assert saved["anchor_removed"]
    assert set(stops) == {record["boundary_creation"]["unit"]}


def test_partial_boundary_refuses_replacement_invocation(remote_owner, monkeypatch):
    attempt, intent = remote_owner
    _, path = worker.owner_paths(attempt)
    record = worker.load_json(path)
    record["boundary_creation"] = {"unit": "bms-attempt-" + intent["attempt_id"] + ".service",
        "marker": "bms-owner:" + evidence.digest(intent), "invocation_id": "a" * 32,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    worker.atomic_json(path, record)
    def control(self, *args):
        assert args[0] != "stop"
        return {"--property=LoadState": "loaded", "--property=Description": record["boundary_creation"]["marker"],
            "--property=InvocationID": "b" * 32}[args[2]]
    monkeypatch.setattr(worker.OwnedBoundary, "control", control)
    with pytest.raises(RuntimeError, match="ambiguous"):
        worker.fence_owner(attempt, evidence.digest(intent), remove=True)


def test_active_or_ambiguous_science_cannot_be_deleted(remote_owner, monkeypatch):
    attempt, intent = remote_owner
    _, path = worker.owner_paths(attempt)
    record = worker.load_json(path)
    record.update(phase="armed", execution_authorized=True)
    worker.atomic_json(path, record)
    monkeypatch.setattr(worker, "status", lambda _: {"state": "running"})
    with pytest.raises(RuntimeError, match="active or ambiguous"):
        worker.fence_owner(attempt, evidence.digest(intent), remove=True)
    assert worker.load_json(path)["phase"] == "armed"


def test_interrupted_remove_resumes_and_metadata_stays_charged(remote_owner, monkeypatch):
    attempt, intent = remote_owner
    attempt.mkdir()
    (attempt / "received.partial").write_bytes(b"interrupted receive")
    (attempt / "metadata.json").write_bytes(b"{}")
    sibling = attempt.parent / "unrelated"
    sibling.mkdir()
    (sibling / "data").write_bytes(b"do not touch")
    digest = evidence.digest(intent)
    before = worker.fence_owner(attempt, digest)
    assert before["resident_disk_bytes"] >= len(b"interrupted receive{}")
    original = worker.shutil.rmtree
    def interrupted(path):
        (path / "received.partial").unlink()
        raise OSError("crash during deletion")
    monkeypatch.setattr(worker.shutil, "rmtree", interrupted)
    with pytest.raises(OSError):
        worker.fence_owner(attempt, digest, remove=True)
    assert worker.load_json(worker.owner_paths(attempt)[1])["phase"] == "removing"
    monkeypatch.setattr(worker.shutil, "rmtree", original)
    removed = worker.fence_owner(attempt, digest, remove=True)
    again = worker.fence_owner(attempt, digest, remove=True)
    assert removed == again
    assert removed["phase"] == "removed" and removed["resident_disk_bytes"] > 0
    assert not attempt.exists()
    assert (sibling / "data").read_bytes() == b"do not touch"


@pytest.mark.asyncio
async def test_explicit_cleanup_refuses_active_job_before_remote_io(remote_owner, monkeypatch):
    _, intent = remote_owner
    job = SimpleNamespace(id="job", remote_attempt_id=intent["attempt_id"],
        provenance={"remote_resource_intent": intent})
    from contextlib import contextmanager
    @contextmanager
    def guard(_):
        yield True
    monkeypatch.setattr(executor, "_controller_attempt_guard", guard)
    async def forbidden(*_, **__):
        pytest.fail("active cleanup issued remote I/O")
    monkeypatch.setattr(executor, "_authenticated_remote_fence", forbidden)
    with pytest.raises(executor.RemoteExecutionError, match="Active or ambiguous"):
        await executor.reconcile_remote_retained_storage(None, job, attempt_id=intent["attempt_id"],
            intent_sha256=evidence.digest(intent), remove=True)




@pytest.mark.asyncio
@pytest.mark.parametrize("crash_at", ["before_staging_insert", "after_staging_insert",
    "before_compute_insert", "after_compute_insert", "after_compute_publication", "arm", "run"])
async def test_controller_crash_boundaries_have_durable_recoverable_intent(
        store, delivery_resources, tmp_path, monkeypatch, crash_at):
    from datetime import datetime
    from database import Job, ExecutionTarget
    from services.remote_execution.bundle import PreparedRemoteBundle, TransferPlan
    from services.remote_execution.contracts import RemoteExecutionEnvelope
    from services.remote_execution import transport
    resources, db = delivery_resources
    await preparing(store)
    policy = resources.publish_execution_target_readiness(target_id="target", remote_root="/worker",
        readiness=remote_readiness("/worker"))
    async with store() as session:
        target = await session.get(ExecutionTarget, "target")
        target.active, target.state = True, "ready"
        target.host, target.port, target.username, target.remote_root = "203.0.113.1", 22, "root", "/worker"
        target.capabilities = {"resource_policy": policy, "runner_sha256": "a" * 64,
            "nextflow_launcher_sha256": "b" * 64}
        await session.commit()
    class Crash(BaseException):
        pass
    original_reserve = resources.reserve_remote_attempt
    def admission(**kwargs):
        phase = "staging" if kwargs.get("staging_disk_bytes") is not None else "compute"
        if crash_at == "before_" + phase + "_insert":
            raise Crash()
        result = original_reserve(**kwargs)
        if crash_at == "after_" + phase + "_insert":
            raise Crash()
        return result
    monkeypatch.setattr(resources, "reserve_remote_attempt", admission)
    async def ready(session, *_):
        return await session.get(ExecutionTarget, "target")
    async def noop(*_, **__):
        pass
    async def probe(*_):
        return remote_readiness("/worker")
    monkeypatch.setattr(executor, "get_ready_target", ready)
    monkeypatch.setattr(executor, "_verify_launch_runner", noop)
    monkeypatch.setattr(executor, "_verify_remote_runner", noop)
    monkeypatch.setattr(transport, "probe_readiness", probe)
    monkeypatch.setattr(executor, "_stage_secret_environment", noop)
    monkeypatch.setattr(executor, "_stage_upload", noop)
    monkeypatch.setattr(executor, "_stage_run", noop)
    monkeypatch.setattr(executor, "_cleanup_local_bundle", lambda *_: None)
    def bundle(**kwargs):
        attempt = kwargs["attempt_id"]
        remote = "/worker/attempts/" + attempt
        local = tmp_path / "bundle" / attempt
        local.mkdir(parents=True)
        envelope = RemoteExecutionEnvelope(job_id="job", root_job_id="job", parent_job_id=None,
            attempt_id=attempt, execution_target_id="target", source_revision="b" * 40,
            source_tree="c" * 40, source_archive_sha256="d" * 64, command=["science"],
            working_directory=remote + "/source", output_directory=remote + "/results",
            expected_result_contract={}, path_map={}, files=[], created_at=datetime.utcnow())
        (local / "execution-envelope.json").write_text(envelope.model_dump_json(by_alias=True))
        return PreparedRemoteBundle(attempt, local, remote, remote + "/source", remote + "/runtime",
            remote + "/results", tmp_path / "result", envelope, "e" * 64, "f" * 64,
            TransferPlan(local, remote + "/source"), (), ())
    monkeypatch.setattr(executor, "prepare_remote_bundle", bundle)
    async def stage(*_):
        async with store() as other:
            assert (await other.get(ExecutionTarget, "target")).leased_job_id is None
        assert db.execute("SELECT SUM(cpu_threads),SUM(dram_bytes) FROM derived_resource_reservations").fetchone()[:] == (0, 0)
    monkeypatch.setattr(executor, "_stage_bundle", stage)
    def archive(*_):
        if crash_at == "after_compute_publication":
            raise Crash()
    monkeypatch.setattr(executor, "_archive_envelope", archive)
    async def remote(connection, argv, **kwargs):
        if argv[2] == crash_at:
            raise Crash()
        return SimpleNamespace(stdout="{}")
    monkeypatch.setattr(executor, "run_remote", remote)
    async with store() as session:
        with pytest.raises(Crash):
            await executor.launch_remote_job(session, await session.get(Job, "job"), command=["science"])
    async with store() as session:
        job = await session.get(Job, "job")
        intent = job.provenance["remote_resource_intent"]
        assert job.remote_attempt_id == intent["attempt_id"]
        assert job.nextflow_run_id == "remote:" + intent["attempt_id"]
        assert {r[0] for r in db.execute("SELECT reservation_id FROM derived_resource_reservations")} <= {
            intent["staging_reservation_id"], intent["compute_reservation_id"]}
        async def fenced(*_, **__):
            return nonexecution(intent)
        monkeypatch.setattr(executor, "_authenticated_remote_fence", fenced)
        assert await executor._recover_remote_intent(session, job)
        await session.refresh(job)
        assert job.status == "failed" and job.remote_attempt_id == intent["attempt_id"]
        assert executor._remote_compute_released(job)
        assert (await session.get(ExecutionTarget, "target")).leased_job_id is None
    assert db.execute("SELECT SUM(cpu_threads),SUM(dram_bytes) FROM derived_resource_reservations").fetchone()[:] == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("authorized,attempt", [(False, "attempt"), (True, "successor")])
async def test_cleanup_router_checks_owner_and_exact_generation(monkeypatch, authorized, attempt):
    from fastapi import HTTPException
    from routers import jobs, ngs_alignment_sessions
    from services import alignment_access
    monkeypatch.setattr(ngs_alignment_sessions, "_mutation_principal", lambda _: "principal")
    monkeypatch.setattr(alignment_access, "request_is_authorized", lambda *_: authorized)
    class Session:
        async def get(self, *_):
            return SimpleNamespace(id="job", execution_target_id="target", remote_attempt_id="attempt", provenance={})
    async def forbidden(*_, **__):
        pytest.fail("unauthorized/stale cleanup reached remote owner")
    monkeypatch.setattr(executor, "reconcile_remote_retained_storage", forbidden)
    with pytest.raises(HTTPException) as error:
        await jobs.reconcile_remote_storage("job", jobs.RemoteRetainedStorageRequest(
            attempt_id=attempt, intent_sha256="a" * 64, remove=True), object(), Session())
    assert error.value.status_code == (403 if not authorized else 409)


@pytest.mark.asyncio
async def test_old_recovery_releases_only_old_allocation_not_future_retry(store, delivery_resources, monkeypatch):
    from database import Job, ExecutionTarget
    resources, db = delivery_resources
    policy = resources.publish_execution_target_readiness(target_id="target", remote_root="/worker",
        readiness=remote_readiness("/worker"))
    intent = intent_for(policy)
    reserve(resources, intent)
    async with store() as session:
        job = await session.get(Job, "job")
        job.remote_attempt_id, job.nextflow_run_id = intent["attempt_id"], "remote:" + intent["attempt_id"]
        job.provenance = {"remote_resource_intent": intent}
        await session.commit()
        old = executor._attempt_snapshot(job)
    async with store() as session:
        job = await session.get(Job, "job")
        job.remote_attempt_id, job.nextflow_run_id = "future-retry", "remote:future-retry"
        job.provenance = {"operator": "future identity"}
        await session.commit()
    async def fenced(*_, **__):
        return nonexecution(intent)
    monkeypatch.setattr(executor, "_authenticated_remote_fence", fenced)
    async with store() as session:
        assert not await executor._recover_remote_intent(session, old)
        job = await session.get(Job, "job")
        assert job.remote_attempt_id == "future-retry" and job.provenance == {"operator": "future identity"}
        assert (await session.get(ExecutionTarget, "target")).leased_job_id == "job"
    assert db.execute("SELECT SUM(cpu_threads),SUM(dram_bytes),SUM(disk_bytes) FROM derived_resource_reservations").fetchone()[:] == (0, 0, 1234)


def test_interrupted_receive_overage_is_truthful(delivery_resources, tmp_path):
    resources, db = delivery_resources
    allocation = resources.reserve(owner="remote-incoming:job:attempt", storage_root=tmp_path,
        owned_path=tmp_path / "incoming", cpu_threads=1, dram_bytes=10, disk_bytes=10)
    allocation.retain_observed(disk_bytes=13)
    allocation.release()
    assert db.execute("SELECT SUM(disk_bytes),SUM(cpu_threads),SUM(dram_bytes) FROM derived_resource_reservations").fetchone()[:] == (13, 0, 0)
