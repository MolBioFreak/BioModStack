"""Read-only acceptance of scheduler-owned parent fanout terminal evidence.

A parent receipt is not a component result. Validate it against durable child
ownership and native bundles, then let ordinary parent Design ingestion continue.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from database import Job
from services.frustrampnn.contracts import canonical_json_bytes, canonical_json_loads
from services.frustrampnn.jobs import ENVELOPE_KEY, child_receipt
from services.frustrampnn.manifests import _read_regular
from services.frustrampnn.parent_results import check_child_lineage, result_snapshot
from services.frustrampnn.persistence import FrustraMPNNPersistenceError
from services.structure_dataset_fanout import FANOUT_PROVENANCE_KEY, FANOUT_SCHEMA, _child_id

_SCHEMA = "bms.frustrampnn.parent-fanout-terminal.v1"
_TRIGGER = "parent_workflow_terminal_dataset"
_WORKFLOWS = {
    "structure_prediction", "complex_prediction", "protein_design",
    "antibody_denovo", "conformational_mapping",
}
_FIELDS = {
    "schema_name", "schema_version", "parent_job_id", "parent_workflow_id",
    "status", "requiredness", "candidate_count", "candidate_ids", "child_job_ids",
    "fanout", "child_receipts", "receipt_sha256",
}
_PRESENTATION = {"name", "created_at", "started_at", "completed_at"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FrustraMPNNPersistenceError(f"FrustraMPNN parent fanout: {message}")


def _equal(left: Any, right: Any) -> bool:
    return canonical_json_bytes(left) == canonical_json_bytes(right)


def _wire_authority(value: Any) -> Any:
    # The HTTP response models expand optional producer fields to null. They do
    # not add authority; compare non-null fields without weakening value types.
    if isinstance(value, dict):
        return {key: _wire_authority(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_wire_authority(item) for item in value]
    return value


def _receipt_authority(receipt: dict[str, Any]) -> dict[str, Any]:
    authority = {key: value for key, value in receipt.items() if key not in _PRESENTATION}
    # The child receipt query does not promise result-row SQL ordering. Candidate
    # selection order is authoritative; result rows are an exact keyed set.
    authority["results"] = sorted(authority["results"], key=lambda row: row["candidate_id"])
    return _wire_authority(authority)


def _load_parent_terminal(path: Path) -> dict[str, Any]:
    payload = _read_regular(path.parent, path.name, max_bytes=16 * 1024 * 1024)
    receipt = canonical_json_loads(payload)
    _require(isinstance(receipt, dict) and set(receipt) == _FIELDS, "terminal receipt fields are invalid")
    _require(payload == canonical_json_bytes(receipt) + b"\n", "terminal receipt is not canonical")
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    _require(receipt["receipt_sha256"] == hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
             "terminal receipt hash mismatch")
    return receipt


def _validate_candidate_result(*, child_id: str, output_dir: str, receipt: dict, candidate_id: str) -> None:
    # Keep the existing native validator and its temporary file lifecycle in one
    # worker-thread operation. Live ORM/session objects never cross this boundary.
    child = SimpleNamespace(id=child_id, output_dir=output_dir)
    with result_snapshot(child, receipt, candidate_id):
        pass


async def accept_parent_fanout_terminal(
    parent: Any,
    output_root: Path,
    session: Any,
    *,
    explicit_paths: Sequence[str],
    terminal_entries: Sequence[tuple[str, Any]],
) -> bool:
    """Return False only for non-fanout stages; never persist/reparent results.

    Both local and downloaded remote receipts use the same API-owned durable
    fanout and child stores. No worker paths or copied bundles are authority.
    """
    provenance = parent.provenance if isinstance(parent.provenance, dict) else {}
    fanouts = provenance.get(FANOUT_PROVENANCE_KEY) or {}
    parent_fanouts = {
        key: value for key, value in fanouts.items()
        if isinstance(value, dict)
        and isinstance(value.get("plan"), dict)
        and isinstance(value["plan"].get("request_identity"), dict)
        and value["plan"]["request_identity"].get("trigger") == _TRIGGER
    } if isinstance(fanouts, dict) else {}
    hinted = any("parent_fanout" in Path(path).parts for path in explicit_paths)
    if not parent_fanouts and not hinted:
        return False
    try:
        _require(len(parent_fanouts) == 1, "exact durable parent fanout is missing or ambiguous")
        fanout_id, contract = next(iter(parent_fanouts.items()))
        plan = contract["plan"]
        _require(hashlib.sha256(canonical_json_bytes(plan)).hexdigest() == fanout_id,
                 "durable fanout plan hash mismatch")
        workflow = plan["workflow_id"].removesuffix(".frustrampnn.v1")
        _require(workflow in _WORKFLOWS and plan["workflow_id"] == f"{workflow}.frustrampnn.v1",
                 "unsupported parent workflow")
        _require(plan["parent_job_id"] == str(parent.id)
                 and plan["schema_name"] == FANOUT_SCHEMA and type(plan["schema_version"]) is int
                 and plan["schema_version"] == 1
                 and contract["schema_name"] == FANOUT_SCHEMA and contract["schema_version"] == 1,
                 "durable parent identity mismatch")
        relative = f"frustrampnn/parent_fanout/{workflow}_terminal_v1.json"
        expected_path = output_root.absolute() / relative
        _require(len(explicit_paths) == 1, "terminal stage must publish only one parent receipt")
        observed_path = Path(explicit_paths[0])
        observed_path = observed_path if observed_path.is_absolute() else output_root.absolute() / observed_path
        _require(observed_path == expected_path, "terminal receipt path is not the exact parent output")
        _require(len(terminal_entries) == 1 and terminal_entries[0][0] == "frustrampnn",
                 "terminal stage authority is missing or ambiguous")
        state = terminal_entries[0][1]
        _require(isinstance(state, dict) and state.get("status") == "complete"
                 and state.get("outputs") == list(explicit_paths), "terminal stage contradicts receipt output")
        receipt = await run_in_threadpool(_load_parent_terminal, expected_path)
        _require(receipt["schema_name"] == _SCHEMA and type(receipt["schema_version"]) is int
                 and receipt["schema_version"] == 1 and receipt["parent_job_id"] == str(parent.id)
                 and receipt["parent_workflow_id"] == workflow and receipt["status"] == "complete"
                 and receipt["requiredness"] == "required", "terminal receipt identity/status mismatch")
        members = plan["members"]
        candidate_ids = [member["structure_id"] for member in members]
        _require(bool(candidate_ids) and all(isinstance(value, str) and value for value in candidate_ids)
                 and len(set(candidate_ids)) == len(candidate_ids), "durable candidate set is invalid")
        size = plan["effective_structures_per_job"]
        _require(type(size) is int and size > 0 and type(plan["structures_per_job"]) is int
                 and plan["structures_per_job"] > 0 and type(plan["batching_enabled"]) is bool
                 and size == (plan["structures_per_job"] if plan["batching_enabled"] else 1),
                 "durable grouping is invalid")
        batches = [members[start:start + size] for start in range(0, len(members), size)]
        child_ids = [_child_id(fanout_id, ordinal) for ordinal in range(len(batches))]
        _require(_equal(contract["child_job_ids"], child_ids)
                 and _equal(receipt["child_job_ids"], child_ids)
                 and _equal(receipt["candidate_ids"], candidate_ids)
                 and type(receipt["candidate_count"]) is int
                 and receipt["candidate_count"] == len(candidate_ids), "candidate/child order or cardinality mismatch")
        summary = receipt["fanout"]
        _require(isinstance(summary, dict)
                 and set(summary) == {"fanout_id", "structures_per_job", "effective_structures_per_job", "replayed"}
                 and type(summary["replayed"]) is bool
                 and _equal({key: summary[key] for key in summary if key != "replayed"}, {
                     "fanout_id": fanout_id, "structures_per_job": plan["structures_per_job"],
                     "effective_structures_per_job": size,
                 }), "terminal fanout grouping mismatch")
        children = (await session.execute(select(Job).where(
            Job.parent_job_id == str(parent.id), Job.child_stage == "frustrampnn",
        ))).scalars().all()
        _require(len(children) == len(child_ids) and {str(child.id) for child in children} == set(child_ids),
                 "durable child set is missing, extra, or foreign")
        by_id = {str(child.id): child for child in children}
        embedded = receipt["child_receipts"]
        _require(isinstance(embedded, list) and len(embedded) == len(child_ids), "child receipt cardinality mismatch")
        settings = plan["request_identity"]["requested_settings"]
        for ordinal, (child_id, batch, supplied) in enumerate(zip(child_ids, batches, embedded, strict=True)):
            child = by_id[child_id]
            check_child_lineage(parent, child)
            expected_fanout = {
                "schema_name": FANOUT_SCHEMA, "schema_version": 1, "fanout_id": fanout_id,
                "parent_job_id": str(parent.id), "batch_ordinal": ordinal,
                "structure_ids": [member["structure_id"] for member in batch],
                "member_lineage": [member["lineage"] for member in batch],
            }
            _require(_equal((child.provenance or {}).get(FANOUT_PROVENANCE_KEY), expected_fanout),
                     "child fanout lineage mismatch")
            selections = child.params[ENVELOPE_KEY]["selection"]
            _require(len(selections) == len(batch), "child selection cardinality mismatch")
            for selected, member in zip(selections, batch, strict=True):
                lineage = member["lineage"]
                coordinates = lineage["producer_coordinates"]
                _require(selected["candidate_id"] == member["structure_id"]
                         and selected["source_job_id"] == str(parent.id)
                         and lineage["source_job_id"] == str(parent.id)
                         and selected["design_id"] == lineage["design_id"]
                         and selected["sha256"] == lineage["source_sha256"]
                         and _equal(selected["producer_coordinates"], coordinates)
                         and coordinates["candidate_id"] == member["structure_id"]
                         and coordinates["parent_workflow_id"] == workflow
                         and coordinates["producer_stage"] == lineage["producer_stage"],
                         "child selection contradicts exact source lineage")
            durable = await child_receipt(session, child=child)
            _require(isinstance(supplied, dict)
                     and _equal(_receipt_authority(supplied), _receipt_authority(durable)),
                     "embedded child receipt contradicts durable receipt")
            _require(_equal(durable["requested_settings"], settings)
                     and durable["settings_value_origin"] == settings["settings_value_origin"],
                     "child requested settings contradict fanout plan")
            candidates, results = durable["candidates"], durable["results"]
            ids = expected_fanout["structure_ids"]
            _require([item["candidate_id"] for item in candidates] == ids
                     and len(results) == len(ids)
                     and {item["candidate_id"] for item in results} == set(ids)
                     and all(item["status"] == "succeeded" for item in results),
                     "required child candidate results are incomplete")
            grouped = durable["grouped_terminal_artifact"]
            _require(len(ids) == 1 or grouped is not None, "grouped terminal receipt is missing")
            if grouped is not None:
                _require([row["candidate_id"] for row in grouped["records"]] == ids
                         and all(row["status"] == "succeeded" for row in grouped["records"]),
                         "required grouped candidate failed")
            results_by_id = {row["candidate_id"]: row for row in results}
            for selected, member in zip(candidates, batch, strict=True):
                result = results_by_id[selected["candidate_id"]]
                _require(selected["source_job_id"] == str(parent.id)
                         and selected["source_artifact_sha256"] == member["lineage"]["source_sha256"]
                         and result["source_artifact_sha256"] == selected["source_artifact_sha256"]
                         and selected["requested_settings_sha256"] == durable["requested_settings_sha256"]
                         and selected["settings_value_origin"] == durable["settings_value_origin"],
                         "child source/settings lineage mismatch")
                # Reuse the exact transfer validator: it binds durable result and
                # selection IDs/hashes to fully validated native manifest bytes.
                # The temporary snapshot is discarded; no child result is ingested
                # under the parent and no copied worker artifact is trusted.
                await run_in_threadpool(
                    _validate_candidate_result, child_id=str(child.id), output_dir=str(child.output_dir),
                    receipt=durable, candidate_id=selected["candidate_id"],
                )
        return True
    except FrustraMPNNPersistenceError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise FrustraMPNNPersistenceError("FrustraMPNN parent fanout authority is invalid") from exc
