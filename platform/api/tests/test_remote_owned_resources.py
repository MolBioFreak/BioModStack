"""Offline regression definitions; no live manager or project execution required.

Kernel qualification is a separate authorized integration run. Fake counters in
these tests test rejection/ownership semantics, not measured scientific evidence.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import importlib.util
import uuid

import pytest
from services import remote_resource_evidence as evidence
from services.resource_usage_evidence import attach_resource_usage_receipt, validate_producer_resource_usage_receipt
from tests.test_remote_lifecycle_gaps import delivery_resources, remote_readiness


@pytest.fixture
def worker():
    path = Path(__file__).resolve().parents[1] / "tools" / "bms_remote_worker.py"
    spec = importlib.util.spec_from_file_location("owned_worker_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def allocation(resources):
    policy = resources.publish_execution_target_readiness(target_id="cloud", remote_root="/worker",
                                                          readiness=remote_readiness("/worker"))
    attempt = str(uuid.uuid4())
    return resources.reserve_remote_attempt(target_id="cloud", policy=policy, attempt_id=attempt,
        job_id="job", storage_path="/worker/attempts/" + attempt)


def proof_for(allocated):
    attempt = allocated["owner"].split(":", 2)[2]
    unit = "bms-attempt-" + attempt + ".service"
    value = {
        "schema": evidence.SCHEMA, "job_id": "job", "run_attempt_id": attempt,
        "admission_id": allocated["reservation_id"], "execution_envelope_sha256": "e" * 64,
        "producer_source_revision": "b" * 40, "producer_source_tree": "c" * 40,
        "allocation": allocated,
        "execution": {"unit": unit, "invocation_id": "d" * 32,
            "control_group": "/sys/fs/cgroup/system.slice/" + unit + "/science",
            "inode": 99, "boot_id": str(uuid.uuid4()), "machine_id": allocated["machine_id"]},
        "observed": {"cpu_usage_usec": 100, "memory_peak_bytes": 1000, "pids_peak": 3,
            "memory_events": {"oom_kill": 0}, "populated": 0,
            "cpu_max": [allocated["effective"]["cpu_threads"] * 100000, 100000],
            "memory_max_bytes": allocated["effective"]["dram_bytes"], "swap_max_bytes": 0,
            "started_at": "2026-09-07T00:00:00Z", "finished_at": "2026-09-07T00:01:00Z"},
        "quiescent": True, "disk": {"scope": "attempt-tree-logical-bytes",
            "enforcement": "sampled-abort-not-quota", "resident_bytes": 1000, "sample_interval_seconds": 2},
        "complete": True, "outcome": "completed", "stage_terminal_states": {},
    }
    value["receipt_sha256"] = evidence.digest(value)
    return value


def job_for(proof):
    return SimpleNamespace(id="job", remote_attempt_id=proof["run_attempt_id"], execution_target_id="cloud",
        assigned_gpu=None, params={}, provenance={"remote_execution_receipt": {
            "resource_allocation": proof["allocation"], "execution_envelope_sha256": "e" * 64,
            "source_revision": "b" * 40, "source_tree": "c" * 40}})


def resign(proof):
    proof.pop("receipt_sha256", None)
    proof["receipt_sha256"] = evidence.digest(proof)
    return proof


def test_idle_readiness_has_no_reservations(delivery_resources):
    resources, db = delivery_resources
    resources.publish_execution_target_readiness(target_id="cloud", remote_root="/worker",
                                                 readiness=remote_readiness("/worker"))
    assert db.execute("SELECT COUNT(*) FROM derived_resource_reservations").fetchone()[0] == 0


@pytest.mark.parametrize("damage", ["missing", "machine", "idle", "quiescence", "backend"])
def test_bad_capability_cannot_publish_policy(delivery_resources, damage):
    resources, db = delivery_resources
    ready = remote_readiness("/worker")
    if damage == "missing": ready.pop("owned_boundary")
    else:
        key = {"machine": "machine_id", "idle": "idle_reservation", "quiescence": "quiescence", "backend": "backend"}[damage]
        ready["owned_boundary"][key] = {"machine": "b" * 32, "idle": True, "quiescence": False, "backend": "sampled"}[damage]
    with pytest.raises(resources.ResourceCapacityUnavailable):
        resources.publish_execution_target_readiness(target_id="cloud", remote_root="/worker", readiness=ready)
    assert db.execute("SELECT COUNT(*) FROM derived_resource_reservations").fetchone()[0] == 0


def test_release_retains_disk_not_compute_and_admits_next(delivery_resources):
    resources, db = delivery_resources
    allocated = allocation(resources)
    proof = proof_for(allocated)
    evidence.validate_document(proof)
    resources.retain_remote_attempt(allocated, quiescence_receipt=proof, resident_disk_bytes=2000)
    resources.retain_remote_attempt(allocated, quiescence_receipt=proof, resident_disk_bytes=2000)
    row = db.execute("SELECT * FROM derived_resource_reservations WHERE reservation_id=?", (allocated["reservation_id"],)).fetchone()
    assert (row["state"], row["cpu_threads"], row["dram_bytes"], row["disk_bytes"]) == ("retained", 0, 0, 2000)
    second = allocation(resources)
    assert second["effective"]["cpu_threads"] == allocated["effective"]["cpu_threads"]
    assert second["effective"]["dram_bytes"] == allocated["effective"]["dram_bytes"]


def test_overage_is_charged_without_holding_compute(delivery_resources):
    resources, db = delivery_resources
    allocated = allocation(resources)
    proof = proof_for(allocated)
    resources.retain_remote_attempt(allocated, quiescence_receipt=proof,
                                   resident_disk_bytes=allocated["effective"]["disk_bytes"] + 7)
    rows = db.execute("SELECT cpu_threads,dram_bytes,disk_bytes FROM derived_resource_reservations").fetchall()
    assert sum(row[0] for row in rows) == sum(row[1] for row in rows) == 0
    assert sum(row[2] for row in rows) == allocated["effective"]["disk_bytes"] + 7


def test_uncertain_live_state_retains_admission(delivery_resources):
    resources, db = delivery_resources
    allocated = allocation(resources)
    proof = proof_for(allocated)
    proof["quiescent"] = False
    with pytest.raises(resources.ResourceCapacityUnavailable):
        resources.retain_remote_attempt(allocated, quiescence_receipt=proof, resident_disk_bytes=10)
    row = db.execute("SELECT state,cpu_threads FROM derived_resource_reservations").fetchone()
    assert row[0] == "active" and row[1] > 0


@pytest.mark.parametrize("section,key,value", [
    ("execution", "machine_id", "b" * 32), ("execution", "invocation_id", "fake"),
    ("observed", "memory_max_bytes", 3), ("observed", "cpu_max", [1, 100000]),
    ("observed", "populated", 1), ("disk", "enforcement", "kernel-quota"),
])
def test_receipt_rejects_false_identity_or_guarantees(delivery_resources, section, key, value):
    resources, _ = delivery_resources
    proof = proof_for(allocation(resources))
    proof[section][key] = value
    with pytest.raises(ValueError): evidence.validate_document(resign(proof))


@pytest.mark.parametrize("workflow", ["ont_basecall_dna", "ont_basecall_rna", "ont_plasmid_qc",
    "ont_construct_screening", "ont_methylation_analysis", "ont_fastq_qc",
    "ont_pooled_reference_assignment", "wf_clone_validation", "external_signal_alignment"])
def test_native_consumers_share_remote_receipt_authority(delivery_resources, workflow):
    resources, _ = delivery_resources
    proof = proof_for(allocation(resources))
    job = job_for(proof)
    job.params = attach_resource_usage_receipt({"workflow_id": workflow}, proof)
    assert validate_producer_resource_usage_receipt(job, {}) == proof
    job.provenance["remote_execution_receipt"]["execution_envelope_sha256"] = "f" * 64
    with pytest.raises(Exception, match="sealed Job attempt"):
        validate_producer_resource_usage_receipt(job, {})


def test_fastq_reopen_projects_remote_schema_without_systemd_fiction(delivery_resources):
    from services.ont_ngs_results import _execution_resources
    resources, _ = delivery_resources
    proof = proof_for(allocation(resources))
    job = job_for(proof)
    job.params = attach_resource_usage_receipt({}, proof)
    result = _execution_resources(job, {"resource_evidence_status": "accepted", "resource_usage_receipt_sha256": proof["receipt_sha256"]})
    assert result["receipt_schema"] == evidence.SCHEMA
    assert result["observed_memory_peak_bytes"] == 1000


def test_attempt_kill_does_not_touch_sibling(worker, tmp_path, monkeypatch):
    boundary = worker.OwnedBoundary(str(uuid.uuid4()), {"cpu_threads": 1, "dram_bytes": 1000})
    boundary.path = tmp_path / "owned"
    boundary.path.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "cgroup.kill").write_text("untouched")
    monkeypatch.setattr(boundary, "sample", lambda: {"populated": 0})
    assert boundary.quiesce()["populated"] == 0
    assert (boundary.path / "cgroup.kill").read_text() == "1"
    assert (sibling / "cgroup.kill").read_text() == "untouched"


def test_identity_failure_precedes_kill(worker, tmp_path, monkeypatch):
    boundary = worker.OwnedBoundary(str(uuid.uuid4()), {"cpu_threads": 1, "dram_bytes": 1000})
    boundary.path = tmp_path
    def refuse(): raise RuntimeError("identity changed")
    monkeypatch.setattr(boundary, "sample", refuse)
    with pytest.raises(RuntimeError): boundary.quiesce()
    assert not (tmp_path / "cgroup.kill").exists()


def test_disk_observation_does_not_follow_sibling_aliases(worker, tmp_path):
    owned, sibling = tmp_path / "owned", tmp_path / "sibling"
    owned.mkdir(); sibling.mkdir()
    (owned / "science").write_bytes(b"abc")
    (sibling / "other-job").write_bytes(b"not ours")
    (owned / "alias").symlink_to(sibling, target_is_directory=True)
    assert worker.owned_disk_bytes(owned) == 3 + (owned / "alias").lstat().st_size


@pytest.mark.asyncio
async def test_waiting_delivery_fence_does_not_require_compute_slot(delivery_resources):
    from services.remote_execution import executor
    resources, _ = delivery_resources
    proof = proof_for(allocation(resources))
    job = job_for(proof)
    job.remote_state = "remote_finished_results_waiting"
    job.nextflow_run_id = "remote:" + job.remote_attempt_id
    job.provenance["remote_execution_receipt"].update(compute_released=True, resource_receipt=proof)
    assert executor._remote_compute_released(job)
    class Session:
        statement = None
        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(rowcount=1)
    session = Session()
    assert await executor._acquire_remote_terminal_fence(session, job)
    assert "leased_job_id" not in str(session.statement)
    assert "remote_attempt_id" in str(session.statement)
    assert "provenance" in str(session.statement)


def test_launch_gate_follows_enrollment_ack(worker, monkeypatch, tmp_path):
    import os
    events = []
    child_gate = []
    boundary = SimpleNamespace(path=tmp_path, sample=lambda: events.append("verify-enrolled"))
    def spawn(argv, **kwargs):
        gate = int(argv[argv.index("--gate-fd") + 1])
        ack = int(argv[argv.index("--ack-fd") + 1])
        child_gate.append(os.dup(gate))
        events.append("enrolled")
        os.write(ack, b"E")
        return SimpleNamespace()
    monkeypatch.setattr(worker.subprocess, "Popen", spawn)
    try:
        worker.spawn_owned(boundary, {"command": ["science"], "working_directory": str(tmp_path)}, {}, None)
        assert events == ["enrolled", "verify-enrolled"]
        assert os.read(child_gate[0], 1) == b"G"
    finally:
        for fd in child_gate: os.close(fd)


def test_capability_releases_its_temporary_boundary(worker, monkeypatch):
    events = []
    class Probe:
        def __init__(self, attempt, limits):
            assert str(uuid.UUID(attempt)) == attempt
            events.append(("create-capability-only", limits))
            self.identity = {"machine_id": "a" * 32, "boot_id": str(uuid.uuid4())}
        def create(self): events.append("created")
        def quiesce(self): return {"populated": 0}
        def close(self): events.append("closed")
    monkeypatch.setattr(worker, "OwnedBoundary", Probe)
    result = worker.resource_capability()
    assert result["idle_reservation"] is False
    assert result["disk"] == "observed-logical-bytes-not-quota"
    assert events[-1] == "closed"


def test_final_metadata_overage_blocks_scientific_acceptance(delivery_resources):
    resources, _ = delivery_resources
    proof = proof_for(allocation(resources))
    job = job_for(proof)
    job.provenance["remote_execution_receipt"]["peak_retained_disk_bytes"] = proof["allocation"]["effective"]["disk_bytes"] + 1
    assert evidence.validate_for_job(job, proof, require_complete=False) == proof
    with pytest.raises(ValueError, match="incomplete"):
        evidence.validate_for_job(job, proof)


def test_controller_flock_cannot_recover_remote_science(delivery_resources, monkeypatch):
    resources, db = delivery_resources
    allocated = allocation(resources)
    monkeypatch.setattr(resources, "_owner_lock", lambda _: pytest.fail("remote ownership is not a controller flock"))
    with resources._transaction() as transaction:
        resources._recover(transaction, allocated["target_id"], allocated["machine_id"])
    assert db.execute("SELECT state FROM derived_resource_reservations").fetchone()[0] == "active"



@pytest.mark.parametrize("stage", ["dorado_align", "fastq_qc", "dorado_basecall", "wf_clone_validation"])
def test_remote_stage_reporting_is_durable_without_callback(monkeypatch, tmp_path, stage):
    import json
    import sys
    path = Path(__file__).resolve().parents[3] / "scripts" / "stage_reporter.py"
    spec = importlib.util.spec_from_file_location("remote_stage_fixture", path)
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    output = tmp_path / "native-output"
    output.write_text("fixture bytes")
    monkeypatch.setenv("BMS_REMOTE_EXECUTION", "1")
    monkeypatch.setenv("BMS_REMOTE_JOB_ID", "job")
    monkeypatch.setenv("BMS_REMOTE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(reporter, "API_BASE_URL", "")
    monkeypatch.setattr(reporter, "STAGE_REPORT_TOKEN", "")
    monkeypatch.setattr(sys, "argv", [str(path), "job", stage, "complete", str(output)])
    reporter.main()
    reporter.main()  # idempotent report, no second terminal authority
    assert json.loads((tmp_path / "_remote" / "stage-terminal.json").read_text()) == {
        stage: {"status": "complete", "outputs": ["native-output"]}}
