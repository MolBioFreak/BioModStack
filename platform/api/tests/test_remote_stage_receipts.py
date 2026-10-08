"""Offline receipt contract tests; model execution is not exercised here."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from services.remote_stage_receipts import (
    RECEIPT_DIRECTORY, canonical_bytes, validate_remote_stage_receipts,
    write_remote_stage_receipt,
)


@pytest.mark.parametrize('denial', [None, 'cpus', 'memory_bytes', 'scratch_bytes', 'devices', 'target'])
def test_worker_continuation_rebinds_actual_budget(tmp_path, monkeypatch, denial):
    from dataclasses import replace
    from component_runtime import ComponentRuntime, NativeInvocation, GeneratedInput, SourceIdentity
    from services import nextflow
    from tools import bms_remote_worker as worker
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts.lib.component_adapter import current_generation_context
    from scripts.open_stage_gate import open_component_gate
    identity = SourceIdentity(revision='a'*40, tree='b'*40)
    runtime = ComponentRuntime(tmp_path / 'ledger.sqlite', artifact_root=tmp_path,
        attempt_id='attempt', root_job_id='root', target_id='worker', lease_id='lease',
        source_identity=identity.__dict__)
    candidates = tmp_path / 'candidates'
    candidates.mkdir()
    (candidates / 'one.pdb').write_text('native fixture')
    checkpoint = open_component_gate(runtime, job_id='root', stage='review', payload={},
        directories={'candidate': candidates})
    runtime.claim_root(owner_id='owner', boot_id='boot')
    runtime.set_root_state('paused', owner_id='owner', boot_id='boot', quiescent=True)
    devices = [dict(gpu_index=3, gpu_uuid='GPU-worker')]
    previous = dict(gpu_ids=[3], required=dict(cpus=1, memory_bytes=1, scratch_bytes=999999),
        admission=dict(devices=devices))
    context = dict(target_id='worker', resources=previous, artifact_root=str(tmp_path),
        parent=dict(id='root', model_id='fixture', mode='run', params={}, output_dir=str(tmp_path)))
    runtime.context = context
    from component_runtime import NativeComponent, SelectedExecutionMetadata, SelectedExecutionPlan, canonical_bytes
    component = NativeComponent(component_key='native', authority='fixture:native', selection_json=b'{}',
        resources_json=canonical_bytes(dict(cpus=dict(value=4), memory=dict(value='12 GB'), gpu=dict(count=1))))
    metadata = SelectedExecutionMetadata('fixture', 'fixture', (component,), (), (), (), (), b'{}', None, None, ())
    plan = SelectedExecutionPlan(identity, 'fixture', 'fixture', 'run', 'fixture.nf',
        b'{}', b'{}', canonical_bytes({'out_dir': str(tmp_path)}), metadata)
    invocation = replace(NativeInvocation.capture(model_id='fixture', mode='run', command=['never-science'],
        requested={}, effective={}, native_parameters={'out_dir': str(tmp_path)}, entrypoint='fixture.nf',
        generated_inputs=[GeneratedInput('new.json', b'new')]), source_identity=identity, execution_plan=plan)
    admission = dict(schema='bms.target-resource-admission.v1', execution_target_id='worker', devices=devices,
        required=dict(cpus=1, memory_bytes=1, scratch_bytes=0),
        available=dict(cpus=8, memory_bytes=16*1024**3, scratch_bytes=3))
    if denial in ('cpus', 'memory_bytes', 'scratch_bytes'):
        admission['available'][denial] = 0
    elif denial == 'devices': admission['devices'] = [dict(gpu_index=3, gpu_uuid='replacement')]
    elif denial == 'target': admission['execution_target_id'] = 'other'
    worker.atomic_json(worker.envelope_path(tmp_path), {})
    status = dict(attempt_id='attempt', boot_id='boot', state='awaiting_input', quiescent=True)
    worker.atomic_json(worker.status_path(tmp_path), status)
    monkeypatch.setattr(worker, 'status', lambda _: status)
    monkeypatch.setattr(worker, 'boot_id', lambda: 'boot')
    monkeypatch.setattr(worker, '_component_checkpoint_runtime', lambda _: runtime)
    monkeypatch.setattr(nextflow, 'compile_component_checkpoint_continuation', lambda *args: invocation)
    spawned = []
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **kw: spawned.append(a))
    kwargs = dict(attempt_id='attempt', expected_boot_id='boot', lease_id='lease', operation_id='review-op',
        checkpoint_id='root:review', checkpoint_sha256=checkpoint['checkpoint_sha256'],
        decision={'continue': True}, continuation_lease_id='new-lease', resource_admission=admission)
    if denial:
        rejected = worker.checkpoint_control(tmp_path, **kwargs)
        assert rejected['operation']['state'] == 'rejected'
        assert any(word in rejected['operation']['error'] for word in ('capacity', 'devices'))
        assert runtime.root_state()['state'] == 'paused'
        assert runtime.checkpoint_status('root:review')['decision'] is None
        assert not spawned and not (tmp_path / 'new.json').exists()
    else:
        worker.checkpoint_control(tmp_path, **kwargs)
        projected = current_generation_context(context, runtime.root_state())
        assert projected['resources']['required'] == dict(cpus=4, memory_bytes=12*1024**3, scratch_bytes=3)
        assert projected['resources']['admission']['required'] == projected['resources']['required']
        assert context['resources'] == previous and len(spawned) == 1
        assert (tmp_path / 'new.json').read_bytes() == b'new'


def test_component_gate_native_artifacts_and_annotation_are_bound_without_http(tmp_path, monkeypatch):
    import sys
    from component_runtime import ComponentRuntime, ResultReference
    code = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(code))
    from scripts import open_stage_gate as gate
    root = tmp_path / "artifacts"
    root.mkdir()
    candidate = tmp_path / "native"
    candidate.mkdir()
    (candidate / "candidate.pdb").write_text("native fixture bytes")
    annotation = tmp_path / "gate_post_fampnn_annotations.json"
    annotation.write_text('{"annotation_receipt": "native fixture"}')
    context = tmp_path / "context.json"
    context.write_text(json.dumps(dict(artifact_root=str(root), ledger_path=str(tmp_path / "ledger.sqlite"),
        attempt_id="attempt", root_job_id="root", target_id="worker", lease_id="lease")))
    monkeypatch.setenv("BMS_COMPONENT_CONTEXT", str(context))
    monkeypatch.setattr(gate.requests, "post", lambda *a, **kw: pytest.fail("checkpoint used HTTP"))
    monkeypatch.setattr(sys, "argv", ["gate", "--job_id", "root", "--stage", "post_fampnn",
        "--candidate_dir", str(candidate), "--payload_json", str(annotation), "--output", str(tmp_path / "gate.json")])
    assert gate.main() == 0
    runtime = ComponentRuntime(tmp_path / "ledger.sqlite", artifact_root=root,
        attempt_id="attempt", root_job_id="root", target_id="worker", lease_id="lease")
    status = runtime.pending_checkpoints()[0]
    refs = status["checkpoint"]["artifacts"]
    assert any(r["relative_path"].endswith(annotation.name) for r in refs)
    assert any(ResultReference(**r).resolve(root).read_bytes() == b"native fixture bytes" for r in refs)
    projection = gate.component_checkpoint_projection(runtime)[0]
    assert projection["checkpoint_sha256"] == status["checkpoint_sha256"]
    assert projection["stage"] == "post_fampnn"
    assert gate.main() == 0  # exact replay, never approval
    assert len(runtime.pending_checkpoints()) == 1
    with pytest.raises(ValueError, match="binding"):
        runtime.decide_checkpoint("root:post_fampnn", checkpoint_sha256="0" * 64,
                                  decision={"continue": True}, actor="explicit-reviewer")
    (candidate / "candidate.pdb").write_text("changed native bytes")
    with pytest.raises(ValueError, match="immutable"):
        gate.main()


def test_worker_pause_is_quiescent_not_result_ready_and_resume_is_fenced(tmp_path, monkeypatch):
    import ctypes
    from component_runtime import ComponentRuntime
    from tools import bms_remote_worker as worker
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts.open_stage_gate import open_component_gate
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "results"
    root.mkdir()
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "one.pdb").write_text("native review fixture")
    runtime = ComponentRuntime(tmp_path / "ledger.sqlite", artifact_root=root,
        attempt_id="attempt", root_job_id="root", target_id="worker", lease_id="lease")
    checkpoint = open_component_gate(runtime, job_id="root", stage="review", payload={},
                                     directories={"candidate": candidates})
    runtime.claim_root(owner_id="fixture", boot_id="boot")
    runtime.set_root_state("paused", owner_id="fixture", boot_id="boot", quiescent=True)
    monkeypatch.setattr(worker, "boot_id", lambda: "boot")
    worker.atomic_json(worker.envelope_path(tmp_path), dict(attempt_id="attempt", job_id="root",
        output_directory=str(root), working_directory=str(tmp_path), command=["never-executed"]))
    worker.atomic_json(worker.status_path(tmp_path), dict(attempt_id="attempt", job_id="root",
        state="prepared", boot_id="boot"))
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: SimpleNamespace(prctl=lambda *a: 0))
    monkeypatch.setattr(worker, "process_start_ticks", lambda pid: 10)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(pid=123, poll=lambda: 75, wait=lambda: 75))
    joined = []
    monkeypatch.setattr(worker, "quiesce_writers", lambda status: joined.append(True) or True)
    monkeypatch.setattr(worker.os, "waitpid", lambda *a: (0, 0))
    def owned_runtime(envelope):
        assert joined
        return runtime
    monkeypatch.setattr(worker, "_component_checkpoint_runtime", owned_runtime)
    monkeypatch.setattr(worker, "build_result_manifest", lambda *a: pytest.fail("review published full results"))
    assert worker._supervise_owned(tmp_path) == 0
    status = worker.load_json(worker.status_path(tmp_path))
    assert status["state"] == "awaiting_input" and status["quiescent"]
    assert status["result_manifest_sha256"] is None
    monkeypatch.setattr(worker, "status", lambda path: status)
    kwargs = dict(attempt_id="attempt", expected_boot_id="boot", lease_id="lease", operation_id='review-op',
                  checkpoint_id="root:review", checkpoint_sha256=checkpoint["checkpoint_sha256"],
                  decision={"selected_artifacts": ["one.pdb"]}, continuation_lease_id="next-lease")
    with pytest.raises(RuntimeError, match="boot authority"):
        worker.checkpoint_control(tmp_path, **dict(kwargs, expected_boot_id="old-boot"))
    with pytest.raises(RuntimeError, match="artifact-set"):
        worker.checkpoint_control(tmp_path, **dict(kwargs, checkpoint_sha256="0" * 64))
    assert runtime.checkpoint_status("root:review")["decision"] is None


def test_checkpoint_edge_compiles_native_resume_and_runs_shared_adapter_once(tmp_path, monkeypatch):
    import sys
    from dataclasses import replace
    from component_runtime import ComponentRuntime, NativeInvocation, SourceIdentity
    from services import nextflow
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts.open_stage_gate import open_component_gate
    from scripts.lib.component_adapter import run_component_workflow
    import os
    monkeypatch.setattr(os, 'environ', os.environ.copy())
    root = tmp_path / "results"
    root.mkdir()
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "one.pdb").write_text("native fixture")
    identity = {"revision": "a" * 40, "tree": "b" * 40}
    context = dict(attempt_id="attempt", root_job_id="root", target_id="worker", lease_id="lease",
        source_identity=identity, artifact_root=str(root), ledger_path=str(tmp_path / "ledger.sqlite"),
        working_directory=str(tmp_path), root_command=[sys.executable, "-c", "raise RuntimeError('original root replayed')"],
        parent=dict(id="root", model_id="antibody_design", mode="denovo", params={}, output_dir=str(root)))
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    runtime = ComponentRuntime(tmp_path / "ledger.sqlite", artifact_root=root,
        attempt_id="attempt", root_job_id="root", target_id="worker", lease_id="lease", source_identity=identity)
    checkpoint = open_component_gate(runtime, job_id="root", stage="post_fampnn", payload={},
                                     directories={"candidate": candidates})
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    runtime.claim_root(owner_id="predecessor", boot_id=boot)
    runtime.set_root_state("paused", owner_id="predecessor", boot_id=boot, quiescent=True)
    selected = [r["relative_path"] for r in checkpoint["checkpoint"]["artifacts"] if r["relative_path"].endswith(".pdb")]
    decision = {"selected_artifacts": selected}
    calls = []
    marker = tmp_path / 'recorder.txt'
    def native_compiler(model, mode, params, output, **kwargs):
        calls.append(params)
        command = [sys.executable, '-c', f"from pathlib import Path; p=Path({str(marker)!r}); p.write_text('resumed')"]
        return replace(NativeInvocation.capture(model_id=model, mode=mode, command=command,
            requested=params, effective=params, native_parameters={**params, "out_dir": str(output)}, entrypoint='fixture.nf'),
            source_identity=kwargs['source_identity'])
    monkeypatch.setattr(nextflow, 'compile_nextflow_invocation', native_compiler)
    # Harmless Python recorder is not a Nextflow task; resource-config tests own that boundary.
    from scripts.lib import component_adapter
    # This test records processes; real Nextflow resource conformance is separate.
    monkeypatch.setattr(component_adapter, 'resource_bound_command', lambda argv, *_: argv)

    invocation = nextflow.compile_component_checkpoint_continuation(context, checkpoint, decision)
    assert calls[0]['interactive_gate_continue'] is True
    assert calls[0]['fampnn_collected_pdbs'].startswith(str(root / '.bms-review' / 'selections'))
    kwargs = dict(checkpoint_sha256=checkpoint['checkpoint_sha256'], decision=decision, actor='reviewer',
                  boot_id=boot, invocation=invocation, continuation_lease_id='new-lease',
                  parent_snapshot=nextflow.component_checkpoint_parent_snapshot(invocation, context))
    with pytest.raises(ValueError, match='same-boot'):
        runtime.resume_checkpoint('root:post_fampnn', **dict(kwargs, boot_id='foreign-boot'))
    runtime.resume_checkpoint('root:post_fampnn', **kwargs)
    assert runtime.root_state()['state'] == 'resume_ready'
    assert run_component_workflow(path) == 0
    assert marker.read_text() == 'resumed'
    marker.unlink()
    assert run_component_workflow(path) == 0 and not marker.exists()
    assert runtime.root_state()['generation'] == 1
    with pytest.raises(ValueError, match='paused'):
        runtime.resume_checkpoint('root:post_fampnn', **kwargs)


@pytest.mark.parametrize('stage,model', [('post_boltzgen', 'boltzgen'), ('post_ppiflow_generator', 'ppiflow')])
def test_checkpoint_domain_uses_ordinary_native_refinement_policy(tmp_path, monkeypatch, stage, model):
    from routers import jobs
    from component_runtime import ResultReference
    from services import nextflow
    from dataclasses import replace
    from component_runtime import NativeInvocation, SourceIdentity, GeneratedInput, ComponentRuntime
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts.open_stage_gate import open_component_gate
    root = tmp_path / 'results'
    root.mkdir()
    candidates = tmp_path / 'candidates'
    candidates.mkdir()
    (candidates / 'one.pdb').write_text('retained native bytes')
    parent = dict(id='root', model_id=model, mode='nanobody', params={'framework_type': 'vhh',
        'seq_design_fampnn': True, 'interactive_gating': True, 'remote_result_policy': 'manual'},
        execution_target_id='worker', output_dir=str(root))
    runtime = ComponentRuntime(tmp_path / 'ledger.sqlite', artifact_root=root,
        attempt_id='attempt', root_job_id='root', target_id='worker', lease_id='lease')
    checkpoint = open_component_gate(runtime, job_id='root', stage=stage, payload={}, directories={'candidate': candidates})
    context = dict(parent=parent, artifact_root=str(root), attempt_id='attempt', root_job_id='root',
        target_id='worker', lease_id='lease', source_identity={'revision': 'a'*40, 'tree': 'b'*40})
    selected = [ref['relative_path'] for ref in checkpoint['checkpoint']['artifacts'] if ref['relative_path'].endswith('.pdb')]
    calls = []
    real_compiler = nextflow.compile_nextflow_invocation
    def compile_native(model, mode, params, output, **kwargs):
        calls.append((model, mode, params))
        return replace(NativeInvocation.capture(model_id=model, mode=mode, command=['true'],
            requested=params, effective=params, native_parameters={**params, "out_dir": str(output)}, entrypoint='fixture.nf',
            generated_inputs=[GeneratedInput('native-input.json', b'new generation')]), source_identity=kwargs['source_identity'])
    monkeypatch.setattr(nextflow, 'compile_nextflow_invocation', compile_native)
    invocation = nextflow.compile_component_checkpoint_continuation(context, checkpoint, {'selected_artifacts': selected})
    assert calls[0][0] == 'template_antibody_denovo'
    assert calls[0][2]['skip_rfantibody'] is True
    assert calls[0][2]['seq_design_fampnn'] is True
    assert calls[0][2]['interactive_gate_continue'] is True
    assert all(item.relative_path.startswith('.bms-review/selections/') for item in invocation.generated_inputs)
    assert not (root / 'native-input.json').exists()
    monkeypatch.setattr(nextflow, 'compile_nextflow_invocation', real_compiler)
    actual = nextflow.compile_component_checkpoint_continuation(context, checkpoint, {'selected_artifacts': selected})
    assert actual.execution_plan is not None
    assert actual.model_id == 'template_antibody_denovo'
    assert not (root / 'native-input.json').exists()


def test_paused_checkpoint_cancellation_uses_retained_quiescence(tmp_path, monkeypatch):
    from component_runtime import ComponentRuntime
    from tools import bms_remote_worker as worker
    runtime = ComponentRuntime(tmp_path / 'ledger.sqlite', artifact_root=tmp_path,
        attempt_id='attempt', root_job_id='root', target_id='worker', lease_id='lease')
    runtime.claim_root(owner_id='owner', boot_id='boot')
    runtime.set_root_state('paused', owner_id='owner', boot_id='boot', quiescent=True)
    worker.atomic_json(worker.envelope_path(tmp_path), dict(job_id='root', attempt_id='attempt'))
    worker.atomic_json(worker.status_path(tmp_path), dict(job_id='root', attempt_id='attempt',
        state='awaiting_input', boot_id='boot', quiescent=True))
    monkeypatch.setattr(worker, 'boot_id', lambda: 'boot')
    monkeypatch.setattr(worker, 'process_matches', lambda *args: False)
    monkeypatch.setattr(worker, '_component_checkpoint_runtime', lambda envelope: runtime)
    assert worker.cancel(tmp_path, 0)['state'] == 'cancelled'
    with pytest.raises(RuntimeError, match='cancel'):
        runtime.check_active()
    assert (tmp_path / 'ledger.sqlite').exists()


def test_component_gate_empty_artifacts_cannot_open(tmp_path, monkeypatch):
    from component_runtime import ComponentRuntime
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts.open_stage_gate import open_component_gate
    monkeypatch.chdir(tmp_path)
    runtime = ComponentRuntime(tmp_path / "ledger.sqlite", artifact_root=tmp_path,
        attempt_id="attempt", root_job_id="root", target_id="worker", lease_id="lease")
    with pytest.raises(ValueError, match="actual declared review artifacts"):
        open_component_gate(runtime, job_id="root", stage="review", payload={}, directories={})
    assert not runtime.pending_checkpoints()


@pytest.fixture
def remote(tmp_path, monkeypatch):
    attempt = str(uuid.uuid4())
    monkeypatch.setenv("BMS_REMOTE_EXECUTION", "1")
    monkeypatch.setenv("BMS_REMOTE_ATTEMPT_ID", attempt)
    monkeypatch.setenv("BMS_REMOTE_JOB_ID", "job-one")
    monkeypatch.setenv("BMS_REMOTE_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.delenv("BMS_STAGE_REPORT_TOKEN", raising=False)
    return tmp_path, attempt


def manifest(root, attempt):
    return SimpleNamespace(job_id="job-one", attempt_id=attempt, exit_code=0, artifacts=[
        SimpleNamespace(relative_path=p.relative_to(root).as_posix(),
                        size_bytes=p.stat().st_size,
                        sha256=hashlib.sha256(p.read_bytes()).hexdigest(), link_target=None)
        for p in root.rglob("*") if p.is_file()
    ])


def write(stage="protenix", status="complete", outputs=None):
    return write_remote_stage_receipt(job_id="job-one", stage=stage, status=status,
        outputs=outputs or [], job_root_relative=True)


def test_remote_receipts_canonical_immutable_and_manifest_bound(remote):
    root, attempt = remote
    (root / "structure.cif").write_text("fixture-only")
    write(status="start")
    receipt = write(outputs=["structure.cif"])
    assert receipt.read_bytes() == canonical_bytes(json.loads(receipt.read_bytes()))
    assert write(outputs=["structure.cif"]) == receipt
    with pytest.raises(ValueError, match="immutable"):
        write(status="failed")
    result = validate_remote_stage_receipts(output_root=root, job_id="job-one",
                                            attempt_id=attempt, manifest=manifest(root, attempt))
    assert {r["status"] for r in result} == {"start", "complete"}
    bound = manifest(root, attempt)
    (root / "structure.cif").write_text("tampered")
    with pytest.raises(ValueError, match="size|digest"):
        validate_remote_stage_receipts(output_root=root, job_id="job-one", attempt_id=attempt, manifest=bound)


@pytest.mark.parametrize("output", ["../outside", "/tmp/absolute", "a//b", "a/./b", "a\\b", ".bms-stage-receipts/a.json"])
def test_outputs_fail_closed(remote, output):
    with pytest.raises(ValueError):
        write(outputs=[output])


def test_receipt_job_attempt_closed_schema_and_missing_integrity(remote):
    root, attempt = remote
    receipt = write(status="not_requested")
    for key, value in [("job_id", "other"), ("attempt_id", str(uuid.uuid4())), ("unknown", True)]:
        original = receipt.read_bytes()
        payload = json.loads(original)
        payload[key] = value
        receipt.chmod(0o600)
        receipt.write_bytes(canonical_bytes(payload))
        with pytest.raises(ValueError):
            validate_remote_stage_receipts(output_root=root, job_id="job-one", attempt_id=attempt, manifest=manifest(root, attempt))
        receipt.write_bytes(original)
    bound = manifest(root, attempt)
    bound.artifacts.clear()
    with pytest.raises(ValueError, match="set"):
        validate_remote_stage_receipts(output_root=root, job_id="job-one", attempt_id=attempt, manifest=bound)


def test_symlink_and_unfinished_stage_rejected(remote, tmp_path):
    root, attempt = remote
    (root / "alias").symlink_to(root.parent)
    with pytest.raises(ValueError):
        write(outputs=["alias/outside"])
    (root / "alias").unlink()
    write(status="start")
    with pytest.raises(ValueError, match="no terminal"):
        validate_remote_stage_receipts(output_root=root, job_id="job-one", attempt_id=attempt, manifest=manifest(root, attempt))


def test_remote_reporter_does_not_call_http_or_require_credentials(remote, monkeypatch):
    reporter_path = Path(__file__).resolve().parents[3] / "scripts" / "stage_reporter.py"
    spec = importlib.util.spec_from_file_location("remote_test_reporter", reporter_path)
    assert spec is not None and spec.loader is not None
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    def forbidden(*args, **kwargs):
        pytest.fail("remote stage reporter attempted HTTP")
    monkeypatch.setattr("requests.post", forbidden)
    monkeypatch.setattr("sys.argv", [str(reporter_path), "job-one", "frustrampnn", "not_requested"])
    reporter.main()
    assert (remote[0] / RECEIPT_DIRECTORY / "frustrampnn.terminal.json").exists()


def test_worker_manifest_includes_stage_receipts(remote):
    root, attempt = remote
    write(status="not_requested")
    worker_path = Path(__file__).resolve().parents[1] / "tools" / "bms_remote_worker.py"
    spec = importlib.util.spec_from_file_location("receipt_test_worker", worker_path)
    assert spec is not None and spec.loader is not None
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    attempt_dir = root / "attempt-metadata"
    attempt_dir.mkdir()
    (attempt_dir / "execution-envelope.json").write_text("{}")
    result = worker.build_result_manifest(attempt_dir, dict(output_directory=str(root),
        attempt_id=attempt, job_id="job-one", source_revision="a" * 40, source_tree="b" * 40), 0)
    record = next(a for a in result["artifacts"] if a["relative_path"].startswith(RECEIPT_DIRECTORY + "/"))
    assert record["sha256"] == hashlib.sha256((root / record["relative_path"]).read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_apply_uses_exact_session_and_preserves_scientific_ingestion(remote, monkeypatch):
    from unittest.mock import AsyncMock
    import sqlalchemy
    from services.remote_stage_receipts import apply_remote_stage_receipts
    from services.result_ingester import _ingest_explicit_frustrampnn_results

    root, attempt = remote
    write(stage="frustrampnn", status="not_requested")
    job = SimpleNamespace(id="job-one", remote_attempt_id=attempt,
        execution_target_id="target", remote_state="returning", status="awaiting_input",
        nextflow_run_id=f"remote:{attempt}", output_dir=str(root), child_output_dir=None,
        model_id="protenix", completed_stages=[], stage_outputs={}, provenance={}, current_stage=None)
    # Explicitly mocked attachment/flush; this is metadata unit coverage, not DB proof.
    session = SimpleNamespace(sync_session=object(), flush=AsyncMock())
    monkeypatch.setattr(sqlalchemy, "inspect", lambda obj: SimpleNamespace(session=session.sync_session))
    args = dict(session=session, job=job, attempt_id=attempt, output_root=root, manifest=manifest(root, attempt))
    assert await apply_remote_stage_receipts(**args) == 1
    assert job.completed_stages == []
    assert job.stage_outputs == {"frustrampnn": []}
    assert await _ingest_explicit_frustrampnn_results(job, root, session, commit=False) is None
    assert job.status == "awaiting_input"
    assert "stage_report_token_sha256" not in job.provenance
    job.remote_state = "results_available"
    with pytest.raises(ValueError, match="authority"):
        await apply_remote_stage_receipts(**args)
    job.remote_state = "returning"
    job.remote_attempt_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match="authority"):
        await apply_remote_stage_receipts(**args)


def test_local_reporter_http_contract_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("BMS_REMOTE_EXECUTION", raising=False)
    monkeypatch.setenv("API_BASE_URL", "http://local.invalid")
    monkeypatch.setenv("BMS_STAGE_REPORT_TOKEN", "unit-fixture-token")
    reporter_path = Path(__file__).resolve().parents[3] / "scripts" / "stage_reporter.py"
    spec = importlib.util.spec_from_file_location("local_test_reporter", reporter_path)
    assert spec is not None and spec.loader is not None
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr("sys.argv", [str(reporter_path), "--job-root-relative", "local-job", "protenix", "complete", "final/model.cif"])
    reporter.main()
    assert calls == [("http://local.invalid/api/jobs/local-job/stage-complete", {
        "params": {"stage": "protenix"}, "json": ["final/model.cif"],
        "headers": {"Authorization": "Bearer unit-fixture-token"}, "timeout": 10})]
