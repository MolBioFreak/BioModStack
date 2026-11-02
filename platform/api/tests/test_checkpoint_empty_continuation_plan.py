"""A checkpoint continuation whose plan has no processes left (the final review gate) keeps its admitted budget."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from component_runtime import GeneratedInput


def _remote_context(tmp_path):
    devices = [dict(gpu_index=0, gpu_uuid="GPU-worker")]
    previous = {"schema": "bms.selected-plan-target-resources.v1", "execution_target_id": "worker",
                "policy": "vram_packing", "required": dict(cpus=6, memory_bytes=24 * 1024 ** 3, scratch_bytes=1000),
                "gpu_ids": [0], "components": [{"component_key": "old"}],
                "compute": dict(cpus=4, memory_bytes=16 * 1024 ** 3),
                "coordinator_overlap": dict(cpus=2, memory_bytes=8 * 1024 ** 3),
                "minimum_gpu_memory_mb": 0, "admission_required": True,
                "admission": dict(devices=devices)}
    context = dict(target_id="worker", artifact_root=str(tmp_path), resources=previous)
    admission = dict(schema="bms.target-resource-admission.v1", execution_target_id="worker", devices=devices,
        required=dict(cpus=1, memory_bytes=1, scratch_bytes=0),
        available=dict(cpus=16, memory_bytes=64 * 1024 ** 3, scratch_bytes=10 ** 9))
    return context, admission


def _invocation(*, static=(), dynamic=()):
    plan = SimpleNamespace(metadata=SimpleNamespace(static_components=tuple(static), dynamic_templates=tuple(dynamic)))
    return SimpleNamespace(execution_plan=plan,
                           generated_inputs=[GeneratedInput(".bms-review/selections/x/one.pdb", b"pdb")])


def test_empty_continuation_plan_keeps_the_admitted_budget(tmp_path):
    from services.nextflow import component_checkpoint_resources
    context, admission = _remote_context(tmp_path)
    result = component_checkpoint_resources(_invocation(), context, admission)
    assert result["required"] == dict(cpus=6, memory_bytes=24 * 1024 ** 3, scratch_bytes=3)
    assert result["compute"] == context["resources"]["compute"] and result["gpu_ids"] == [0]
    assert result["components"] == [] and result["admission_required"] is False
    assert result["admission"]["required"] == result["required"]
    assert context["resources"]["required"]["scratch_bytes"] == 1000  # the retained context is not rewritten


def test_empty_continuation_plan_still_checks_current_capacity(tmp_path):
    from services.nextflow import component_checkpoint_resources
    context, admission = _remote_context(tmp_path)
    admission["available"]["cpus"] = 2
    with pytest.raises(ValueError, match="capacity"):
        component_checkpoint_resources(_invocation(), context, admission)


def test_launch_projection_still_refuses_a_plan_with_no_declarations():
    from services.remote_execution.targets import ExecutionTargetError, selected_plan_target_resources
    with pytest.raises(ExecutionTargetError, match="no native resource declarations"):
        selected_plan_target_resources(SimpleNamespace(id="worker"), _invocation().execution_plan,
                                       gpu_ids=[0], scratch_bytes=0)
