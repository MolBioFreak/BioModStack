"""Remote kernel evidence. Deliberately distinct from local systemd receipts."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

SCHEMA = "bms.remote-resource-usage.v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def validate_document(candidate):
    from services.global_resource_admission import validate_receipt
    if not isinstance(candidate, Mapping):
        raise ValueError("remote resource receipt must be an object")
    value = dict(candidate)
    keys = {"schema", "job_id", "run_attempt_id", "admission_id", "execution_envelope_sha256",
            "producer_source_revision", "producer_source_tree", "allocation", "execution", "observed",
            "quiescent", "disk", "complete", "outcome", "stage_terminal_states", "receipt_sha256"}
    if set(value) != keys or value["schema"] != SCHEMA:
        raise ValueError("remote resource receipt fields are not exact")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value["receipt_sha256"] != digest(unsigned):
        raise ValueError("remote resource receipt digest mismatch")
    for name in ("job_id", "run_attempt_id", "admission_id", "execution_envelope_sha256", "producer_source_revision", "producer_source_tree"):
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError("remote resource receipt identity text invalid")
    if re.fullmatch(r"[0-9a-f]{64}", value["execution_envelope_sha256"]) is None:
        raise ValueError("remote envelope digest invalid")
    allocation = validate_receipt(value["allocation"])
    if value["admission_id"] != allocation["reservation_id"]:
        raise ValueError("remote resource receipt admission mismatch")
    if allocation["owner"] != "remote-attempt:" + value["job_id"] + ":" + value["run_attempt_id"]:
        raise ValueError("remote resource receipt owner mismatch")
    identity = value["execution"]
    if not isinstance(identity, dict) or set(identity) != {"unit", "invocation_id", "control_group", "inode", "boot_id", "machine_id"}:
        raise ValueError("remote resource execution identity invalid")
    if any(not isinstance(identity[name], str) or not identity[name]
           for name in ("unit", "invocation_id", "control_group", "boot_id", "machine_id")):
        raise ValueError("remote execution identity text invalid")
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", identity["boot_id"]) is None:
        raise ValueError("remote boot identity invalid")
    if (identity["machine_id"] != allocation["machine_id"]
            or identity["unit"] != "bms-attempt-" + value["run_attempt_id"] + ".service"
            or re.fullmatch(r"[0-9a-f]{32}", str(identity["invocation_id"])) is None
            or type(identity["inode"]) is not int or identity["inode"] <= 0
            or not identity["control_group"].endswith("/" + identity["unit"] + "/science")):
        raise ValueError("remote resource execution identity mismatch")
    observed = value["observed"]
    if not isinstance(observed, dict) or set(observed) != {"cpu_usage_usec", "memory_peak_bytes", "pids_peak",
            "memory_events", "populated", "cpu_max", "memory_max_bytes", "swap_max_bytes", "started_at", "finished_at"}:
        raise ValueError("remote resource accounting fields invalid")
    for name in ("cpu_usage_usec", "memory_peak_bytes", "pids_peak", "populated", "memory_max_bytes", "swap_max_bytes"):
        if type(observed[name]) is not int or observed[name] < 0:
            raise ValueError("remote resource accounting units invalid")
    from datetime import datetime
    try:
        started = datetime.fromisoformat(observed["started_at"].replace("Z", "+00:00"))
        finished = datetime.fromisoformat(observed["finished_at"].replace("Z", "+00:00"))
        if started.tzinfo is None or finished.tzinfo is None or finished < started:
            raise ValueError("invalid remote observation times")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("remote observation timestamps invalid") from exc
    quota = observed["cpu_max"]
    if (not isinstance(quota, list) or len(quota) != 2
            or any(type(v) is not int or v <= 0 for v in quota)
            or quota[0] != quota[1] * allocation["effective"]["cpu_threads"]
            or observed["memory_max_bytes"] != allocation["effective"]["dram_bytes"]
            or observed["swap_max_bytes"] != 0 or observed["populated"] != 0
            or value["quiescent"] is not True or type(value["complete"]) is not bool
            or value["outcome"] not in {"completed", "failed", "cancelled", "lost"}):
        raise ValueError("remote resource enforcement/quiescence mismatch")
    if not isinstance(observed["memory_events"], dict) or any(type(v) is not int or v < 0 for v in observed["memory_events"].values()):
        raise ValueError("remote memory event counters invalid")
    disk = value["disk"]
    if (not isinstance(disk, dict) or set(disk) != {"scope", "enforcement", "resident_bytes", "sample_interval_seconds"}
            or disk["scope"] != "attempt-tree-logical-bytes" or disk["enforcement"] != "sampled-abort-not-quota"
            or type(disk["resident_bytes"]) is not int or disk["resident_bytes"] < 0
            or disk["sample_interval_seconds"] != 2):
        raise ValueError("remote disk observation contract invalid")
    if value["complete"] and (observed["memory_events"].get("oom_kill", 0) != 0
            or disk["resident_bytes"] > allocation["effective"]["disk_bytes"]):
        raise ValueError("complete remote receipt contradicts resource limit evidence")
    if not isinstance(value["stage_terminal_states"], dict):
        raise ValueError("remote stage evidence is missing")
    for stage, state in value["stage_terminal_states"].items():
        if (not isinstance(stage, str) or not stage or not isinstance(state, dict)
                or set(state) != {"status", "outputs"} or state["status"] not in {"complete", "failed", "not_requested"}
                or not isinstance(state["outputs"], list)):
            raise ValueError("remote stage evidence is invalid")
        for path in state["outputs"]:
            if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or any(p in {"", ".", ".."} for p in path.split("/")):
                raise ValueError("remote stage output is not root-relative")
    return value


def validate_for_job(job, candidate, *, require_complete=True):
    value = validate_document(candidate)
    authority = (job.provenance or {}).get("remote_execution_receipt") or {}
    if (value["job_id"] != str(job.id) or value["run_attempt_id"] != str(job.remote_attempt_id)
            or value["allocation"] != authority.get("resource_allocation")
            or value["allocation"]["target_id"] != str(job.execution_target_id)
            or value["execution_envelope_sha256"] != authority.get("execution_envelope_sha256")
            or value["producer_source_revision"] != authority.get("source_revision")
            or value["producer_source_tree"] != authority.get("source_tree")):
        raise ValueError("remote resource receipt differs from sealed Job attempt")
    if require_complete and (value["complete"] is not True or value["outcome"] != "completed"
            or value["observed"]["pids_peak"] < 1):
        raise ValueError("remote producer evidence is incomplete")
    return value
