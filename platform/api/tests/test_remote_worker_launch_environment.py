"""Worker continuation controls compile under the launch envelope's environment, not the SSH shell's."""
from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest


def _bound_launcher(tmp_path: Path) -> Path:
    launcher = tmp_path / "bound" / "bms-nextflow"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return launcher


def test_launch_environment_applies_and_restores(tmp_path, monkeypatch):
    from tools import bms_remote_worker as worker
    monkeypatch.delenv("BMS_FIXTURE_NEW", raising=False)
    monkeypatch.setenv("BMS_FIXTURE_OLD", "shell")
    envelope = {"environment": {"BMS_FIXTURE_NEW": "bound", "BMS_FIXTURE_OLD": "bound"}}
    with worker._launch_environment(envelope):
        assert os.environ["BMS_FIXTURE_NEW"] == "bound" and os.environ["BMS_FIXTURE_OLD"] == "bound"
    assert "BMS_FIXTURE_NEW" not in os.environ and os.environ["BMS_FIXTURE_OLD"] == "shell"
    with pytest.raises(RuntimeError):
        with worker._launch_environment({"environment": {"BMS_FIXTURE_NEW": "bad\x00value"}}):
            pass


def test_checkpoint_resume_compiles_with_the_bound_nextflow_launcher(tmp_path, monkeypatch):
    from component_runtime import (ComponentRuntime, GeneratedInput, NativeComponent, NativeInvocation,
                                   SelectedExecutionMetadata, SelectedExecutionPlan, SourceIdentity, canonical_bytes)
    from services import nextflow
    from tools import bms_remote_worker as worker
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts.open_stage_gate import open_component_gate

    launcher = _bound_launcher(tmp_path)
    # The SSH shell that runs checkpoint-resume carries no Nextflow pin.
    monkeypatch.delenv("BMS_NEXTFLOW_BIN", raising=False)
    monkeypatch.delenv("BMS_NEXTFLOW_EXECUTABLE", raising=False)
    identity = SourceIdentity(revision="a" * 40, tree="b" * 40)
    runtime = ComponentRuntime(tmp_path / "ledger.sqlite", artifact_root=tmp_path, attempt_id="attempt",
        root_job_id="root", target_id="worker", lease_id="lease", source_identity=identity.__dict__)
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    (candidates / "one.pdb").write_text("native fixture")
    checkpoint = open_component_gate(runtime, job_id="root", stage="review", payload={},
        directories={"candidate": candidates})
    runtime.claim_root(owner_id="owner", boot_id="boot")
    runtime.set_root_state("paused", owner_id="owner", boot_id="boot", quiescent=True)
    devices = [dict(gpu_index=3, gpu_uuid="GPU-worker")]
    previous = dict(gpu_ids=[3], required=dict(cpus=1, memory_bytes=1, scratch_bytes=999999),
        admission=dict(devices=devices))
    runtime.context = dict(target_id="worker", resources=previous, artifact_root=str(tmp_path),
        parent=dict(id="root", model_id="fixture", mode="run", params={}, output_dir=str(tmp_path)))
    component = NativeComponent(component_key="native", authority="fixture:native", selection_json=b"{}",
        resources_json=canonical_bytes(dict(cpus=dict(value=4), memory=dict(value="12 GB"), gpu=dict(count=1))))
    metadata = SelectedExecutionMetadata("fixture", "fixture", (component,), (), (), (), (), b"{}", None, None, ())
    plan = SelectedExecutionPlan(identity, "fixture", "fixture", "run", "fixture.nf", b"{}", b"{}",
        canonical_bytes({"out_dir": str(tmp_path)}), metadata)
    admission = dict(schema="bms.target-resource-admission.v1", execution_target_id="worker", devices=devices,
        required=dict(cpus=1, memory_bytes=1, scratch_bytes=0),
        available=dict(cpus=8, memory_bytes=16 * 1024 ** 3, scratch_bytes=3))
    worker.atomic_json(worker.envelope_path(tmp_path), {"environment": {"BMS_NEXTFLOW_EXECUTABLE": str(launcher)}})
    status = dict(attempt_id="attempt", boot_id="boot", state="awaiting_input", quiescent=True)
    worker.atomic_json(worker.status_path(tmp_path), status)
    monkeypatch.setattr(worker, "status", lambda _: status)
    monkeypatch.setattr(worker, "boot_id", lambda: "boot")
    monkeypatch.setattr(worker, "_component_checkpoint_runtime", lambda _: runtime)
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: None)
    resolved = []

    def compile_edge(context, checkpoint_, decision):
        # The real compiler asks for the workflow launcher exactly like this.
        resolved.append(nextflow.resolve_nextflow_executable())
        return replace(NativeInvocation.capture(model_id="fixture", mode="run", command=["never-science"],
            requested={}, effective={}, native_parameters={"out_dir": str(tmp_path)}, entrypoint="fixture.nf",
            generated_inputs=[GeneratedInput("new.json", b"new")]), source_identity=identity, execution_plan=plan)

    monkeypatch.setattr(nextflow, "compile_component_checkpoint_continuation", compile_edge)
    worker.checkpoint_control(tmp_path, attempt_id="attempt", expected_boot_id="boot", lease_id="lease",
        operation_id="review-op", checkpoint_id="root:review", checkpoint_sha256=checkpoint["checkpoint_sha256"],
        decision={"continue": True}, continuation_lease_id="new-lease", resource_admission=admission)
    assert resolved == [str(launcher.resolve())]
    assert "BMS_NEXTFLOW_EXECUTABLE" not in os.environ


def test_checkpoint_context_is_placed_with_the_launch_path_map():
    from tools import bms_remote_worker as worker
    envelope = {"path_map": {
        "/home/dalab/.biomodstack-dev": "/root/bms/attempts/a/data",
        "/home/dalab/.biomodstack-dev/bms_results/job": "/root/bms/attempts/a/results",
        "/home/dalab/code": "/root/bms/attempts/a/materialized/source",
    }}
    context = {"artifact_root": "/root/bms/attempts/a/results", "parent": {
        "id": "job", "output_dir": "/root/bms/attempts/a/results",
        "params": {"out_dir": "/home/dalab/.biomodstack-dev/bms_results/job", "code_root": "/home/dalab/code",
                   "data_root": "/home/dalab/.biomodstack-dev", "msa_cache_dir": "/home/dalab/.biomodstack-dev/msa_cache",
                   "plr_designable_positions": "A30-40", "gpus": 1, "paths": ["/home/dalab/code/x", "relative/y", "/elsewhere/../z"]}}}
    placed = worker._placed_checkpoint_context(envelope, context)
    params = placed["parent"]["params"]
    assert params["out_dir"] == "/root/bms/attempts/a/results"
    assert params["code_root"] == "/root/bms/attempts/a/materialized/source"
    assert params["data_root"] == "/root/bms/attempts/a/data"
    assert params["msa_cache_dir"] == "/root/bms/attempts/a/data/msa_cache"
    assert params["plr_designable_positions"] == "A30-40" and params["gpus"] == 1
    assert params["paths"] == ["/root/bms/attempts/a/materialized/source/x", "relative/y", "/elsewhere/../z"]
    assert context["parent"]["params"]["out_dir"].startswith("/home/dalab")  # the retained context is not rewritten
    assert placed["parent"]["output_dir"] == context["parent"]["output_dir"] and placed["artifact_root"] == context["artifact_root"]
