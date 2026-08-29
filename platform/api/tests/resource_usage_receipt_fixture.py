from __future__ import annotations

import hashlib
from typing import Any

import rfc8785


def valid_resource_receipt_authority(
    *,
    job_id: str = "job-1",
    generation: int = 1,
    attempt: int = 1,
    unit: str = "unit-1",
    invocation_id: str = "invocation-1",
    owner_nonce: str = "owner-nonce-1",
    execution_attempts: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from services import resource_usage_evidence as evidence

    handoff = evidence.build_resource_admission_handoff(
        admission_id="admission-1",
        run_attempt_id="run-attempt-1",
        canonical_job_id=job_id,
        preparation_id="preparation-1",
        cpu_threads=8,
        dram_bytes=4096,
        gpu_index=None,
        gpu_uuid=None,
        policy_source="test",
        policy_version="1",
        owner="workflow",
        lease_token="lease-token-1",
        source_revision="a" * 40,
        source_tree="b" * 40,
    )
    dispatch = evidence.build_dispatch_materialization_authority(
        payload_sha256="f" * 64,
        handoff=handoff,
    )
    receipt: dict[str, Any] = {
        "schema": "bms.workflow-resource-usage.v1",
        "producer": "bms.workflow_job_runner",
        "producer_source_revision": "a" * 40,
        "producer_source_tree": "b" * 40,
        "job_id": job_id,
        "run_attempt_id": "run-attempt-1",
        "admission_id": "admission-1",
        "preparation_id": "preparation-1",
        "execution": {
            "generation": generation,
            "attempt": attempt,
            "unit": unit,
            "owner_nonce_sha256": hashlib.sha256(owner_nonce.encode("utf-8")).hexdigest(),
            "invocation_id": invocation_id,
            "control_group_sha256": "d" * 64,
        },
        "admission": {
            "cpu_threads": 8,
            "dram_bytes": 4096,
            "gpu_index": None,
            "gpu_uuid": None,
            "policy_source": "test",
            "policy_version": "1",
            "owner": "workflow",
        },
        "enforcement": {
            "cpu_accounting": True,
            "memory_accounting": True,
            "tasks_accounting": True,
            "cpu_quota_per_sec_usec": 8000000,
            "memory_max_bytes": 4096,
            "expected_cpu_quota_per_sec_usec": 8000000,
            "expected_memory_max_bytes": 4096,
            "cuda_visible_devices": "",
            "expected_cuda_visible_devices": "",
            "gpu_visibility": None,
            "invocation_id_matches": True,
            "control_group_matches": True,
            "main_pid_matches": True,
            "runner_in_cgroup": True,
            "device_policy": "closed",
            "device_allow_is_empty": True,
            "device_allow_paths": [],
            "expected_device_allow_paths": [],
            "device_allow_exact": True,
            "cpu_only_device_denial": True,
            "gpu_device_denial": False,
        },
        "observed": {
            "started_at": "2026-08-21T00:00:00Z",
            "finished_at": "2026-08-21T00:00:01Z",
            "sample_interval_seconds": 1,
            "sample_count": 1,
            "monitor_failures": 0,
            "accounting": {
                "cpu": {
                    "usage_usec": 100,
                    "user_usec": 80,
                    "system_usec": 20,
                    "nr_periods": 2,
                    "nr_throttled": 0,
                    "throttled_usec": 0,
                },
                "memory_peak_bytes": 4096,
                "pids_peak": 7,
            },
            "gpu_peak_by_uuid": {},
            "gpu_peak_by_pid_uuid": [],
            "gpu_usage_disposition": "cpu_only",
        },
        "outcome": "completed",
        "complete": True,
        "incompleteness_code": None,
        "admission_handoff_sha256": handoff["handoff_sha256"],
        "dispatch_payload_sha256": dispatch["payload_sha256"],
        "dispatch_authority_sha256": dispatch["authority_sha256"],
    }
    receipt["receipt_sha256"] = hashlib.sha256(rfc8785.dumps(receipt)).hexdigest()
    params = {
        evidence.GLOBAL_RESOURCE_ADMISSION_PARAM: handoff,
        evidence.GLOBAL_DISPATCH_AUTHORITY_PARAM: dispatch,
        "execution_attempts": execution_attempts or [{
            "generation": generation,
            "attempt": attempt,
            "unit": unit,
            "invocation_id": invocation_id,
            "owner_nonce": owner_nonce,
        }],
        evidence.RESOURCE_USAGE_RECEIPTS_PARAM: [receipt],
    }
    return params, receipt


def valid_cpu_resource_receipt(*, job_id: str = "job-1") -> dict[str, Any]:
    _params, receipt = valid_resource_receipt_authority(job_id=job_id)
    return receipt
