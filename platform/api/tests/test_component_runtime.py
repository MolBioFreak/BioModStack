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
