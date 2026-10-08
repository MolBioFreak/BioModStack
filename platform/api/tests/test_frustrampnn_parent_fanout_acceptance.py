"""Parent-ingestion boundary tests; child scientific validation is a recorded seam.

These unit doubles are not scientific output or live acceptance evidence. Native
bundle validation remains the existing result_snapshot validator's responsibility.
"""
from __future__ import annotations

import copy
import asyncio
import hashlib
import io
import threading
from types import SimpleNamespace

import pytest

from services.frustrampnn import parent_fanout_acceptance as acceptance
from services.frustrampnn.contracts import canonical_json_bytes
from services.frustrampnn.jobs import ENVELOPE_KEY
from services.frustrampnn.persistence import FrustraMPNNPersistenceError
from services.frustrampnn.settings import default_settings
from services.structure_dataset_fanout import (
    FANOUT_PROVENANCE_KEY, FANOUT_SCHEMA, StructureDatasetMember, _child_id, _fanout_plan,
)


def _sha(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    settings = default_settings().model_dump(mode="json", exclude_none=False)
    settings.update(batching_enabled=True, structures_per_job=2)
    parent = SimpleNamespace(id="parent-1", model_id="boltz2", mode="predict", params={}, provenance={})
    members = []
    for ordinal in range(3):
        candidate_id = f"candidate-{ordinal}"
        coordinates = {"candidate_id": candidate_id, "producer_stage": "fold",
                       "producer_candidate_key": f"fold/{ordinal}.pdb", "parent_workflow_id": "structure_prediction"}
        lineage = {"design_id": None, "source_job_id": parent.id, "source_sha256": _sha(candidate_id),
                   "producer_stage": "fold", "producer_coordinates": coordinates}
        members.append(StructureDatasetMember(candidate_id, lineage, None))
    plan = _fanout_plan(workflow_id="structure_prediction.frustrampnn.v1", parent_job_id=parent.id,
                        members=members, batching_enabled=True, structures_per_job=2,
                        request_identity={"requested_settings": settings, "trigger": "parent_workflow_terminal_dataset"})
    fanout_id = _sha(plan)
    child_ids = [_child_id(fanout_id, ordinal) for ordinal in range(2)]
    contract = {"schema_name": FANOUT_SCHEMA, "schema_version": 1, "plan": plan, "child_job_ids": child_ids}
    parent.provenance[FANOUT_PROVENANCE_KEY] = {fanout_id: contract}
    children, receipts = [], {}
    for ordinal, start in enumerate(range(0, 3, 2)):
        batch = plan["members"][start:start + 2]
        selected = [{"candidate_id": member["structure_id"], "source_job_id": parent.id, "design_id": None,
                     "sha256": member["lineage"]["source_sha256"],
                     "producer_coordinates": copy.deepcopy(member["lineage"]["producer_coordinates"])} for member in batch]
        envelope = {"source_parent_job_id": parent.id, "execution_owner_job_id": child_ids[ordinal],
                    "trigger": "parent_workflow_terminal_dataset", "selection": selected}
        child = SimpleNamespace(id=child_ids[ordinal], parent_job_id=parent.id, model_id="frustrampnn",
                                child_stage="frustrampnn", status="completed", params={ENVELOPE_KEY: envelope},
                                output_dir=str(tmp_path / child_ids[ordinal]),
                                execution_target_id=None, provenance={FANOUT_PROVENANCE_KEY: {
                                    "schema_name": FANOUT_SCHEMA, "schema_version": 1, "fanout_id": fanout_id,
                                    "parent_job_id": parent.id, "batch_ordinal": ordinal,
                                    "structure_ids": [member["structure_id"] for member in batch],
                                    "member_lineage": [member["lineage"] for member in batch],
                                }})
        children.append(child)
        candidates = [{"candidate_id": member["structure_id"], "source_job_id": parent.id,
                       "source_artifact_sha256": member["lineage"]["source_sha256"],
                       "requested_settings_sha256": _sha(settings), "settings_value_origin": settings["settings_value_origin"],
                       "component_request_sha256": _sha(member), "effective_settings_sha256": _sha(settings),
                       "producer": {"producer_stage": "fold"}} for member in batch]
        results = [{"candidate_id": row["candidate_id"], "status": "succeeded", "manifest_sha256": _sha(row),
                    "source_artifact_sha256": row["source_artifact_sha256"],
                    "request_sha256": row["component_request_sha256"]} for row in candidates]
        receipts[child.id] = {"job_id": child.id, "child_job_id": child.id, "result_job_id": child.id,
                              "parent_job_id": parent.id, "source_parent_job_id": parent.id,
                              "trigger": "parent_workflow_terminal_dataset", "status": "completed",
                              "name": "Unit boundary double", "created_at": None, "started_at": None, "completed_at": None,
                              "settings_value_origin": settings["settings_value_origin"], "requested_settings": settings,
                              "requested_settings_sha256": _sha(settings), "candidates": candidates, "results": results,
                              "grouped_terminal_artifact": {"records": [{"candidate_id": row["candidate_id"], "status": "succeeded"}
                                                                        for row in candidates]}}
    terminal = {"schema_name": acceptance._SCHEMA, "schema_version": 1, "parent_job_id": parent.id,
                "parent_workflow_id": "structure_prediction", "status": "complete", "requiredness": "required",
                "candidate_count": 3, "candidate_ids": [member.structure_id for member in members], "child_job_ids": child_ids,
                "fanout": {"fanout_id": fanout_id, "structures_per_job": 2, "effective_structures_per_job": 2, "replayed": False},
                "child_receipts": copy.deepcopy(list(receipts.values()))}
    relative = "frustrampnn/parent_fanout/structure_prediction_terminal_v1.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    parent.stage_outputs = {"frustrampnn": [relative]}
    parent.provenance["stage_terminal_states"] = {"frustrampnn": {"status": "complete", "outputs": [relative]}}
    def seal():
        terminal["receipt_sha256"] = _sha({key: value for key, value in terminal.items() if key != "receipt_sha256"})
        path.write_bytes(canonical_json_bytes(terminal) + b"\n")
    seal()
    owning_thread = threading.get_ident()
    class Session:
        async def execute(self, _statement):
            assert threading.get_ident() == owning_thread
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: children))
    async def receipt(_session, *, child):
        assert threading.get_ident() == owning_thread
        return receipts[child.id]
    snapshots = []
    def snapshot(child, receipt, candidate_id):
        snapshots.append((child.id, candidate_id))
        return io.BytesIO()
    monkeypatch.setattr(acceptance, "child_receipt", receipt)
    monkeypatch.setattr(acceptance, "result_snapshot", snapshot)
    async def accept():
        return await acceptance.accept_parent_fanout_terminal(
            parent, tmp_path, Session(), explicit_paths=parent.stage_outputs["frustrampnn"],
            terminal_entries=list(parent.provenance["stage_terminal_states"].items()))
    return SimpleNamespace(parent=parent, children=children, receipts=receipts, terminal=terminal, contract=contract,
                           path=path, seal=seal, accept=accept, snapshots=snapshots, session=Session(), root=tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed_boundary", ["_load_parent_terminal", "result_snapshot"])
async def test_parent_acceptance_keeps_native_validation_off_loop(handoff, monkeypatch, delayed_boundary):
    owner = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    original = getattr(acceptance, delayed_boundary)
    def delayed(*args, **kwargs):
        assert threading.get_ident() != owner
        started.set()
        assert release.wait(5), "parent acceptance blocked its event loop"
        return original(*args, **kwargs)
    monkeypatch.setattr(acceptance, delayed_boundary, delayed)
    task = asyncio.create_task(handoff.accept())
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
        assert not task.done()
        assert await asyncio.wait_for(asyncio.sleep(0, result=True), timeout=1)
    finally:
        release.set()
    assert await asyncio.wait_for(task, timeout=3) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("remote,absolute,replayed", [(False, False, False), (True, False, False), (True, True, True)])
async def test_accepts_exact_fanout_local_and_remote_without_reparenting(handoff, remote, absolute, replayed):
    for child in handoff.children:
        child.execution_target_id = "remote-worker" if remote else None
    if absolute:
        handoff.parent.stage_outputs["frustrampnn"] = [str(handoff.path)]
        handoff.parent.provenance["stage_terminal_states"]["frustrampnn"]["outputs"] = [str(handoff.path)]
    handoff.terminal["fanout"]["replayed"] = replayed
    # HTTP expands optional producer fields and SQL results are not ordered.
    handoff.terminal["child_receipts"][0]["candidates"][0]["producer"]["guidance_id"] = None
    handoff.terminal["child_receipts"][0]["results"].reverse()
    handoff.seal()
    before = copy.deepcopy(handoff.parent.__dict__)
    assert await handoff.accept() is True
    assert handoff.parent.__dict__ == before
    assert handoff.snapshots == [(handoff.children[0].id, "candidate-0"), (handoff.children[0].id, "candidate-1"),
                                 (handoff.children[1].id, "candidate-2")]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    "hash", "duplicate_key", "noncanonical", "foreign_parent", "wrong_workflow", "optional", "failed",
    "count", "bool_count", "candidate_order", "child_order", "fanout", "grouping", "duplicate_receipt",
    "receipt_hash", "receipt_settings", "receipt_source", "receipt_effective", "receipt_candidate_order",
    "missing_child", "extra_child", "unfinished_child", "foreign_child", "child_provenance", "child_selection",
    "plan_hash", "missing_stage", "not_requested", "mixed_paths", "symlink", "missing_file", "grouped_failed",
    "missing_grouped", "missing_result", "failed_result", "native_bundle_failure",
])
async def test_rejects_unsealed_or_contradictory_parent_authority(handoff, monkeypatch, mutation):
    terminal = handoff.terminal
    if mutation == "foreign_parent": terminal["parent_job_id"] = "foreign"
    elif mutation == "wrong_workflow": terminal["parent_workflow_id"] = "complex_prediction"
    elif mutation == "optional": terminal["requiredness"] = "optional"
    elif mutation == "failed": terminal["status"] = "failed"
    elif mutation == "count": terminal["candidate_count"] = 2
    elif mutation == "bool_count": terminal["candidate_count"] = True
    elif mutation == "candidate_order": terminal["candidate_ids"].reverse()
    elif mutation == "child_order": terminal["child_job_ids"] = list(reversed(terminal["child_job_ids"]))
    elif mutation == "fanout": terminal["fanout"]["fanout_id"] = "0" * 64
    elif mutation == "grouping": terminal["fanout"]["effective_structures_per_job"] = 1
    elif mutation == "duplicate_receipt": terminal["child_receipts"][1] = copy.deepcopy(terminal["child_receipts"][0])
    elif mutation == "receipt_hash": terminal["child_receipts"][0]["results"][0]["manifest_sha256"] = "0" * 64
    elif mutation == "receipt_settings": terminal["child_receipts"][0]["requested_settings_sha256"] = "0" * 64
    elif mutation == "receipt_source": terminal["child_receipts"][0]["candidates"][0]["source_artifact_sha256"] = "0" * 64
    elif mutation == "receipt_effective": terminal["child_receipts"][0]["candidates"][0]["effective_settings_sha256"] = "0" * 64
    elif mutation == "receipt_candidate_order": terminal["child_receipts"][0]["candidates"].reverse()
    elif mutation == "missing_child": handoff.children.pop()
    elif mutation == "extra_child": handoff.children.append(SimpleNamespace(id="foreign"))
    elif mutation == "unfinished_child": handoff.children[0].status = "running"
    elif mutation == "foreign_child": handoff.children[0].parent_job_id = "foreign"
    elif mutation == "child_provenance": handoff.children[0].provenance[FANOUT_PROVENANCE_KEY]["batch_ordinal"] = 1
    elif mutation == "child_selection": handoff.children[0].params[ENVELOPE_KEY]["selection"][0]["producer_coordinates"]["producer_candidate_key"] = "foreign.pdb"
    elif mutation == "plan_hash": handoff.contract["plan"]["structures_per_job"] = 3
    elif mutation == "missing_stage": handoff.parent.stage_outputs["frustrampnn"] = []
    elif mutation == "not_requested": handoff.parent.provenance["stage_terminal_states"]["frustrampnn"]["status"] = "not_requested"
    elif mutation == "mixed_paths": handoff.parent.stage_outputs["frustrampnn"].append("workflow_component_result_v3.json")
    elif mutation in {"grouped_failed", "missing_grouped", "missing_result", "failed_result"}:
        receipt = handoff.receipts[handoff.children[0].id]
        if mutation == "grouped_failed": receipt["grouped_terminal_artifact"]["records"][0]["status"] = "failed"
        elif mutation == "missing_grouped": receipt["grouped_terminal_artifact"] = None
        elif mutation == "missing_result": receipt["results"].pop()
        else: receipt["results"][0]["status"] = "failed"
        terminal["child_receipts"][0] = copy.deepcopy(receipt)
    elif mutation == "native_bundle_failure":
        def reject(*_args): raise ValueError("native manifest hash mismatch")
        monkeypatch.setattr(acceptance, "result_snapshot", reject)
    handoff.seal()
    if mutation == "hash":
        terminal["receipt_sha256"] = "0" * 64
        handoff.path.write_bytes(canonical_json_bytes(terminal) + b"\n")
    elif mutation == "duplicate_key":
        handoff.path.write_bytes(handoff.path.read_bytes().replace(b'{', b'{"status":"complete",', 1))
    elif mutation == "noncanonical": handoff.path.write_bytes(handoff.path.read_bytes() + b"\n")
    elif mutation == "symlink":
        target = handoff.path.with_name("other.json")
        handoff.path.rename(target)
        handoff.path.symlink_to(target)
    elif mutation == "missing_file": handoff.path.unlink()
    with pytest.raises(FrustraMPNNPersistenceError):
        await handoff.accept()


@pytest.mark.asyncio
async def test_ingester_acceptance_returns_none_not_child_count(handoff):
    from services.result_ingester import _ingest_explicit_frustrampnn_results
    assert await _ingest_explicit_frustrampnn_results(handoff.parent, handoff.root, handoff.session, commit=True) is None
    assert len(handoff.snapshots) == 3


@pytest.mark.asyncio
async def test_ordinary_ingestion_is_reached_after_acceptance(handoff, monkeypatch):
    from services import result_ingester
    class OrdinaryReached(Exception): pass
    class Session:
        async def execute(self, statement):
            if "jobs.parent_job_id" in str(statement):
                return await handoff.session.execute(statement)
            return SimpleNamespace(scalar_one_or_none=lambda: handoff.parent)
    def ordinary(_output): raise OrdinaryReached
    monkeypatch.setattr(result_ingester, "extract_pdb_files", ordinary)
    monkeypatch.setattr(result_ingester, "resolve_runtime_data_path", lambda path: path)
    with pytest.raises(OrdinaryReached):
        await result_ingester.ingest_job_results(handoff.parent.id, str(handoff.root), Session())
    assert len(handoff.snapshots) == 3


@pytest.mark.asyncio
async def test_nonfanout_component_path_retains_existing_fail_closed_ingestion(tmp_path):
    from services.result_ingester import _ingest_explicit_frustrampnn_results
    parent = SimpleNamespace(id="parent", provenance={}, stage_outputs={"frustrampnn": ["unknown.json"]})
    (tmp_path / "unknown.json").write_text("{}")
    with pytest.raises(FrustraMPNNPersistenceError, match="no explicit terminal result"):
        await _ingest_explicit_frustrampnn_results(parent, tmp_path, object(), commit=False)
