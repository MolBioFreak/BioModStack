"""Offline contracts and real adapter parity; no model/provider execution."""
from __future__ import annotations

from dataclasses import replace
import importlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from component_runtime import (
    GroupingLedger, ResultReference, canonical_bytes, ordered_candidates,
    partition_ordered, plan_frustrampnn,
)


def records():
    return [dict(candidate_id=candidate, producer_candidate_key=key,
                 parent_job_id="parent", parent_workflow_id="structure_prediction",
                 requiredness="required") for candidate, key in (
                     ("b", "z/source.pdb"), ("c", "a/source.pdb"), ("a", "a/source.pdb"))]


def test_selected_plan_projection_builds_identity_payload_once(monkeypatch):
    from component_runtime import SelectedExecutionMetadata, SelectedExecutionPlan, SourceIdentity
    metadata = SelectedExecutionMetadata(availability="fixture", settings_authority="fixture",
        static_components=(), dynamic_templates=(), dependencies=(), artifact_roles=(),
        external_services=(), result_contract_json=b"{}", admission_authority=None,
        retrieval_authority=None, blockers=())
    plan = SelectedExecutionPlan(SourceIdentity("a" * 40, "b" * 40), "fixture", "fixture", "fixture",
        "fixture.nf", b"{}", b"{}", b"{}", metadata)
    expected = plan.to_dict()
    calls = []
    original = SelectedExecutionPlan._identity_payload
    def project(value):
        calls.append(value)
        return original(value)
    monkeypatch.setattr(SelectedExecutionPlan, "_identity_payload", project)
    assert plan.to_dict() == expected
    assert calls == [plan]
    assert expected["plan_sha256"] == plan.plan_sha256


SETTINGS = dict(batching_enabled=True, structures_per_job=2)


def plan():
    return plan_frustrampnn(records(), SETTINGS)


def reference(value, ordinal=0):
    return ResultReference(value.component_id(ordinal), f"results/group-{ordinal}.json", "a" * 64, 20, "native.v3")


@pytest.mark.parametrize("enabled,size,expected", [(True, 2, [["a", "c"], ["b"]]),
    (False, 25, [["a"], ["c"], ["b"]]), (True, 1, [["a"], ["c"], ["b"]]),
    (True, 3, [["a", "c", "b"]])])
def test_shared_order_and_grouping_preserve_local_authority(enabled, size, expected):
    value = plan_frustrampnn(records(), dict(batching_enabled=enabled, structures_per_job=size))
    value.require_groups(expected)
    assert value == plan_frustrampnn(list(reversed(records())), dict(batching_enabled=enabled, structures_per_job=size))


@pytest.mark.parametrize("observed", [[["a"], ["c", "b"]], [["a", "c"]],
    [["a", "c"], ["b"], ["foreign"]], [["c", "a"], ["b"]], [["a", "a"], ["b"]]])
def test_required_join_rejects_wrong_members_order_and_boundaries(observed):
    with pytest.raises(ValueError, match="required exact join"):
        plan().require_groups(observed)


def test_grouping_rejects_missing_duplicate_optional_and_mixed_authority():
    for field, value in [("parent_job_id", "foreign"), ("parent_workflow_id", "foreign"),
                         ("requiredness", "optional"), ("requested_settings", {})]:
        changed = records()
        changed[0][field] = value
        with pytest.raises(ValueError):
            plan_frustrampnn(changed, SETTINGS)
    with pytest.raises(ValueError, match="duplicate ordering"):
        plan_frustrampnn(records() + records()[:1], SETTINGS)
    changed = records()
    changed[0]["candidate_id"] = "a"
    with pytest.raises(ValueError, match="duplicate candidate"):
        plan_frustrampnn(changed, SETTINGS)
    with pytest.raises(ValueError, match="ordering authority"):
        ordered_candidates([{"candidate_id": "id"}], lambda r: r)


@pytest.mark.parametrize("enabled,size", [(1, 2), (True, True), (False, 0), (True, "2")])
def test_no_coercion_of_grouping_settings(enabled, size):
    with pytest.raises(ValueError):
        partition_ordered(["a"], batching_enabled=enabled, structures_per_job=size)


def test_native_collector_seals_only_quiescent_execution_with_actual_refs(tmp_path):
    import hashlib
    from component_runtime import ComponentRequest, ComponentRuntime
    runtime = ComponentRuntime(tmp_path / 'attempt.sqlite', attempt_id='attempt',
        root_job_id='parent', target_id='target', lease_id='lease', artifact_root=tmp_path)
    request = ComponentRequest.capture(parent_job_id='parent', stage='native-stage', child_key='1',
        payload={'model_id': 'fixture', 'mode': 'fixture', 'params': {}})
    child = runtime.submit(request)
    runtime.claim(child, owner_id='fixture-owner', boot_id='fixture-boot')
    snapshot = {'id': child, 'model_id': 'fixture', 'params': {'seed': 1}}
    runtime.bind_native_parent(child, snapshot, owner_id='fixture-owner', boot_id='fixture-boot')
    assert runtime.native_parent(child) == snapshot
    with pytest.raises(ValueError, match='snapshot conflicts'):
        runtime.bind_native_parent(child, dict(snapshot, params={'seed': 2}), owner_id='fixture-owner', boot_id='fixture-boot')
    artifact = tmp_path / 'native-fixture.json'
    artifact.write_bytes(b'{"fixture":true}')
    ref = ResultReference(child, artifact.name, hashlib.sha256(artifact.read_bytes()).hexdigest(),
                          artifact.stat().st_size, 'fixture.contract.v1')
    with pytest.raises(ValueError, match='quiescent'):
        runtime.complete_validated_child(child, result={}, references=[ref])
    runtime.execution_finished(child, owner_id='fixture-owner', boot_id='fixture-boot',
                               output_dir=str(tmp_path), exit_code=0)
    assert runtime.child_status(child)['status'] == 'execution_finished'
    with pytest.raises(ValueError):
        runtime.complete_validated_child(child, result={}, references=[replace(ref, sha256='0'*64)])
    runtime.complete_validated_child(child, result={'validated_fixture': True}, references=[ref])
    runtime.complete_validated_child(child, result={'validated_fixture': True}, references=[ref])
    assert runtime.child_status(child)['status'] == 'completed'
    assert runtime.child_status(child)['output_dir'] == str(tmp_path)
    assert len(runtime.join_children([child])) == 1


@pytest.mark.parametrize("collection", [False, True])
def test_native_sealing_streams_artifacts_without_duplicate_collection_hash(tmp_path, monkeypatch, collection):
    import component_runtime as core
    from scripts import child_job_utils
    from scripts.lib.component_adapter import runtime_from_environment
    context = tmp_path / "context.json"
    context.write_text(json.dumps(dict(ledger_path=str(tmp_path / "attempt.sqlite"),
        artifact_root=str(tmp_path), attempt_id="attempt", root_job_id="parent",
        target_id="target", lease_id="lease")))
    monkeypatch.setenv("BMS_COMPONENT_CONTEXT", str(context))
    runtime = runtime_from_environment()
    child = runtime.submit(core.ComponentRequest.capture(parent_job_id="parent", stage="native",
        child_key="1", payload={"model_id": "fixture", "mode": "fixture", "params": {}}))
    runtime.claim(child, owner_id="owner", boot_id="boot")
    output = tmp_path / "child"
    output.mkdir()
    artifact = output / "trajectory.xtc"
    artifact.write_bytes(b"opaque native collector fixture" * 100)
    runtime.execution_finished(child, owner_id="owner", boot_id="boot", output_dir=str(output), exit_code=0)
    original_read, original_identity = Path.read_bytes, core.file_identity
    reads = []
    def no_whole_artifact(path):
        assert path != artifact, "sealing must not allocate a whole trajectory"
        return original_read(path)
    def identity(path):
        if path == artifact:
            reads.append(path)
        return original_identity(path)
    monkeypatch.setattr(Path, "read_bytes", no_whole_artifact)
    monkeypatch.setattr(core, "file_identity", identity)
    if collection:
        result = child_job_utils.complete_native_collection(
            {"child_ids": [child], "child_output_dirs": [str(output)]},
            {str(output): [artifact]}, authority="native fixture collector")
        assert result["exact_join"] is True
    else:
        child_job_utils.seal_validated_child_files(child, output_dir=output,
            result={"validated_fixture": True}, files=[artifact], role="native_fixture")
    # Capture and shared sealing each own a read; collection additionally joins.
    assert len(reads) == (3 if collection else 2)
    assert runtime.child_status(child)["status"] == "completed"
    artifact.write_bytes(b"changed")
    with pytest.raises(ValueError, match="byte binding mismatch"):
        runtime.join_children([child])


def test_ledger_restart_exact_replay_result_refs_and_attempt_fence(tmp_path):
    value = plan()
    path = tmp_path / "components.sqlite"
    ledger = GroupingLedger(path, attempt_id="attempt-1", plan=value)
    ledger.seal(reference(value, 1))  # Completion order does not change result order.
    with pytest.raises(RuntimeError, match="incomplete"):
        ledger.join()
    restarted = GroupingLedger(path, attempt_id="attempt-1", plan=value)
    restarted.seal(reference(value))
    restarted.seal(reference(value))
    assert restarted.join() == (reference(value), reference(value, 1))
    with pytest.raises(ValueError, match="conflicts"):
        restarted.seal(replace(reference(value), sha256="b" * 64))
    with pytest.raises(ValueError, match="foreign"):
        restarted.seal(replace(reference(value), component_id="foreign"))
    with pytest.raises(ValueError, match="conflicts"):
        GroupingLedger(path, attempt_id="attempt-2", plan=value)
    with pytest.raises(ValueError, match="conflicts"):
        GroupingLedger(path, attempt_id="attempt-1", plan=plan_frustrampnn(records(), {**SETTINGS, "structures_per_job": 3}))
    assert restarted.join() == (reference(value), reference(value, 1))


def test_cancel_is_durable_intent_not_success_or_quiescence(tmp_path):
    value = plan()
    path = tmp_path / "components.sqlite"
    ledger = GroupingLedger(path, attempt_id="attempt", plan=value)
    ledger.request_cancel()
    restarted = GroupingLedger(path, attempt_id="attempt", plan=value)
    with pytest.raises(RuntimeError, match="quiescence is not yet established"):
        restarted.check_active()
    with pytest.raises(RuntimeError, match="cancellation"):
        restarted.seal(reference(value))
    with pytest.raises(RuntimeError, match="cancellation"):
        restarted.join()


def test_concurrent_result_sealing_is_exactly_idempotent(tmp_path):
    value = plan()
    path = tmp_path / "components.sqlite"
    def seal(_):
        ledger = GroupingLedger(path, attempt_id="attempt", plan=value)
        ledger.seal(reference(value))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(seal, range(12)))
    ledger = GroupingLedger(path, attempt_id="attempt", plan=value)
    ledger.seal(reference(value, 1))
    assert len(ledger.join()) == 2


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/../b", "a\\b", ".", "a//b"])
def test_result_refs_reject_noncontained_paths(path):
    with pytest.raises(ValueError, match="contained"):
        replace(reference(plan()), relative_path=path)


def test_result_ref_relocates_and_rejects_tampering_and_symlink_escape(tmp_path):
    import hashlib
    import shutil
    payload = b'{"native":"receipt"}'
    source = tmp_path / "worker"
    source.mkdir()
    (source / "receipt.json").write_bytes(payload)
    ref = ResultReference(plan().component_id(0), "receipt.json",
                          hashlib.sha256(payload).hexdigest(), len(payload), "native.v3")
    host = tmp_path / "host"
    shutil.copytree(source, host)
    assert ref.resolve(host).read_bytes() == payload
    (host / "receipt.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="byte binding"):
        ref.resolve(host)
    (host / "receipt.json").unlink()
    (host / "receipt.json").symlink_to(source / "receipt.json")
    with pytest.raises(ValueError, match="escapes"):
        ref.resolve(host)


def test_real_worker_planner_preserves_native_request_bytes_and_local_groups(tmp_path):
    from test_remote_frustrampnn_self_contained import prepared, remote
    planner = importlib.import_module("scripts.plan_frustrampnn_groups")
    directories = [tmp_path / f"input-{index}" for index in (2, 0, 1)]
    native = [prepared(directory, index) for directory, index in zip(directories, (2, 0, 1))]
    local_metadata = [dict(candidate_id=r["candidate_id"],
        producer_candidate_key=r["source_artifact"]["relative_path"], parent_job_id=r["parent_job_id"],
        parent_workflow_id=r["parent_workflow_id"], requiredness=r["requiredness"]) for r in native]
    local = plan_frustrampnn(local_metadata, native[0]["requested_settings"])
    worker = planner.materialize_groups(directories, tmp_path / "groups", attempt_id="worker-attempt")
    assert worker.groups == local.groups
    assert worker.settings_sha256 == local.settings_sha256
    assert [worker.component_id(i) for i in range(len(worker.groups))] == [local.component_id(i) for i in range(len(local.groups))]
    original = {r["candidate_id"]: canonical_bytes(r) for r in native}
    observed = []
    for group_root in sorted((tmp_path / "groups").glob("group_*")):
        members = sorted(group_root.iterdir())
        group_ids = []
        for member in members:
            payload = (member / remote.FILES[0]).read_bytes()
            candidate = json.loads(payload)["candidate_id"]
            assert payload == original[candidate]
            group_ids.append(candidate)
        observed.append(group_ids)
        if len(members) > 1:
            _, batch = remote.materialize_batch(list(reversed(members)), tmp_path / "authority")
            assert [r["candidate_id"] for r in batch["records"]] == group_ids
    local.require_groups(observed)
    ledger = GroupingLedger(tmp_path / "groups/components.sqlite", attempt_id="worker-attempt", plan=worker)
    with pytest.raises(RuntimeError, match="incomplete"):
        ledger.join()  # Expansion is not inference success.


@pytest.fixture
def runtime(tmp_path):
    from component_runtime import ComponentRuntime
    return ComponentRuntime(tmp_path / "attempt.sqlite", attempt_id="attempt",
        root_job_id="root", target_id="worker-a", lease_id="lease", artifact_root=tmp_path)


def child(key="0", required=True):
    from component_runtime import ComponentRequest
    return ComponentRequest.capture(parent_job_id="root", stage="native-stage", child_key=key,
        payload=dict(model_id="native-model", mode="native-mode", params=dict(seed=42)), required=required)


def seal_child(runtime, identity, tmp_path):
    import hashlib
    path = tmp_path / (identity.replace(":", "-") + ".json")
    path.write_bytes(b'{"native":"validated"}')
    reference = ResultReference(identity, path.name, hashlib.sha256(path.read_bytes()).hexdigest(),
                                path.stat().st_size, "native.v1")
    runtime.complete(identity, owner_id="pid:100:start:200", boot_id="boot-a",
                     result=dict(output_dir=str(tmp_path)), references=[reference])
    return reference


def test_typed_runtime_request_replay_target_and_source_fences(runtime, tmp_path):
    from component_runtime import ComponentRuntime
    identity = runtime.submit(child())
    assert runtime.submit(child()) == identity
    assert runtime.pending() == (identity,)
    assert runtime.children("root", "native-stage")[0]["params"] == dict(seed=42)
    with pytest.raises(ValueError, match="request conflicts"):
        runtime.submit(replace(child(), required=False))
    with pytest.raises(ValueError, match="foreign component parent"):
        runtime.submit(replace(child(), parent_job_id="foreign"))
    for changed in [dict(target_id="worker-b"), dict(lease_id="new-lease"), dict(plan_sha256="changed")]:
        kwargs = dict(attempt_id="attempt", root_job_id="root", target_id="worker-a",
                      lease_id="lease", artifact_root=tmp_path)
        with pytest.raises(ValueError, match="conflicts"):
            ComponentRuntime(runtime.path, **{**kwargs, **changed})


def test_typed_runtime_concurrent_claim_and_uncertain_start_fence(runtime):
    identity = runtime.submit(child())
    def claim(i):
        return runtime.claim(identity, owner_id=f"pid:{i}:start:200", boot_id="boot-a")
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(claim, range(8))) == 1
    assert not runtime.claim(identity, owner_id="replacement", boot_id="new-boot")
    with pytest.raises(ValueError, match="ownership conflicts"):
        runtime.fail(identity, owner_id="replacement", boot_id="new-boot", reason="lost", quiescent=True)


def test_typed_runtime_dispatch_ambiguous_launch_never_retries(runtime):
    from component_runtime import NativeInvocation
    identity = runtime.submit(child())
    calls = []
    def compile_native(request):
        return NativeInvocation.capture(model_id=request.payload["model_id"], mode=request.payload["mode"],
            command=["nextflow", "run", "native.nf"], requested=request.payload,
            effective=request.payload, native_parameters={}, entrypoint="native.nf")
    def launch_native(*args):
        calls.append("possibly-started")
        raise OSError("lost after start")
    kwargs = dict(owner_id="pid:100:start:200", boot_id="boot-a",
                  compile_native=compile_native, launch_native=launch_native)
    with pytest.raises(OSError, match="lost after start"):
        runtime.dispatch(identity, **kwargs)
    assert runtime.child_status(identity)["status"] == "uncertain"
    assert not runtime.dispatch(identity, **kwargs)
    assert calls == ["possibly-started"]
    with pytest.raises(RuntimeError, match="incomplete"):
        runtime.join_children([identity])


def test_typed_runtime_exact_group_native_result_replay_and_event_cursor(runtime, tmp_path):
    ids = [runtime.submit(child(str(i))) for i in range(2)]
    runtime.register_group("native-batch", ids)
    with pytest.raises(ValueError, match="grouping conflicts"):
        runtime.register_group("native-batch", list(reversed(ids)))
    with pytest.raises(RuntimeError, match="incomplete"):
        runtime.join_group("native-batch")
    for identity in reversed(ids):
        assert runtime.claim(identity, owner_id="pid:100:start:200", boot_id="boot-a")
        seal_child(runtime, identity, tmp_path)
    assert [row["job_id"] for row in runtime.join_group("native-batch")] == ids
    last = runtime.events()[-1]["sequence"]
    assert runtime.events(after=last) == ()
    seal_child(runtime, ids[0], tmp_path)
    assert runtime.events(after=last) == ()
    (tmp_path / (ids[0].replace(":", "-") + ".json")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="byte binding"):
        runtime.join_group("native-batch")


def test_typed_runtime_optional_failure_is_explicit_and_cancel_not_quiescence(runtime):
    identity = runtime.submit(child(required=False))
    runtime.claim(identity, owner_id="pid:100:start:200", boot_id="boot-a")
    runtime.fail(identity, owner_id="pid:100:start:200", boot_id="boot-a", reason="native failure", quiescent=True)
    assert runtime.join_children([identity])[0]["status"] == "failed"
    pending = runtime.submit(child("pending"))
    running = runtime.submit(child("running"))
    runtime.claim(running, owner_id="pid:101:start:201", boot_id="boot-a")
    runtime.request_cancel()
    assert runtime.child_status(pending)["status"] == "cancelled"
    assert runtime.child_status(running)["status"] == "running"
    with pytest.raises(RuntimeError, match="quiescence"):
        runtime.join_children([identity])


def test_typed_runtime_checkpoint_explicit_artifact_binding(runtime, tmp_path):
    identity = runtime.submit(child())
    runtime.claim(identity, owner_id="pid:100:start:200", boot_id="boot-a")
    reference = seal_child(runtime, identity, tmp_path)
    status = runtime.checkpoint("review", component_ids=[identity], artifacts=[reference])
    assert status["decision"] is None
    assert runtime.checkpoint("review", component_ids=[identity], artifacts=[reference]) == status
    with pytest.raises(ValueError, match="binding conflicts"):
        runtime.decide_checkpoint("review", checkpoint_sha256="wrong", actor="operator", decision=dict(selected=[identity]))
    kwargs = dict(checkpoint_sha256=status["checkpoint_sha256"], actor="operator", decision=dict(selected=[identity]))
    decided = runtime.decide_checkpoint("review", **kwargs)
    assert runtime.decide_checkpoint("review", **kwargs) == decided
    with pytest.raises(ValueError, match="decision conflicts"):
        runtime.decide_checkpoint("review", **{**kwargs, "decision": dict(selected=[])})


def test_shared_native_adapter_context_never_falls_back(tmp_path, monkeypatch):
    from scripts.lib import component_adapter as adapter
    monkeypatch.delenv("BMS_COMPONENT_CONTEXT", raising=False)
    assert adapter.runtime_from_environment() is None
    monkeypatch.setenv("BMS_COMPONENT_CONTEXT", "relative.json")
    with pytest.raises(ValueError, match="absolute"):
        adapter.runtime_from_environment()
    context = tmp_path / "context.json"
    context.write_text(json.dumps(dict(ledger_path=str(tmp_path / "native.sqlite"), artifact_root=str(tmp_path),
        attempt_id="attempt", root_job_id="root", target_id="worker", lease_id="lease",
        source_identity=dict(revision="source"), plan_sha256="plan", execution_plan=dict(metadata={}))))
    monkeypatch.setenv("BMS_COMPONENT_CONTEXT", str(context))
    request = child()
    identity = adapter.submit_child(request.payload, parent_job_id="root", stage=request.stage, child_key="0")
    assert adapter.submit_child(request.payload, parent_job_id="root", stage=request.stage, child_key="0") == identity
    assert adapter.child_status(identity)["status"] == "queued"
    assert adapter.runtime_from_environment().plan_sha256 == "plan"
