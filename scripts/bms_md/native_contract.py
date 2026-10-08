"""Shared native MD request identity rules for worker joins and host import."""
from __future__ import annotations
import re
from typing import Any, Mapping

SHA256 = re.compile(r"^[0-9a-f]{64}$")

def replica_protocol_matches(
    requested: Mapping[str, Any],
    observed: Any,
    *,
    qualified_gpu_offload: str | None = None,
) -> bool:
    if observed == requested:
        return True
    if requested.get("schema") == "bms.md.job.v1" and isinstance(observed, Mapping):
        execution = requested.get("execution")
        if not isinstance(execution, Mapping) or not isinstance(execution.get("gpu_id"), str):
            return False
        return observed == {**requested, "execution": {
            **execution, "gpu_id": "0", "scheduler_gpu_id": execution["gpu_id"],
        }}
    if requested.get("schema") != "bms.md.job.v2" or not isinstance(observed, Mapping):
        return False
    if observed.get("schema") != "bms.md.job.v2":
        return False
    requested_input = requested.get("input")
    observed_input = observed.get("input")
    if not isinstance(requested_input, Mapping) or not isinstance(observed_input, Mapping):
        return False
    requested_sha = requested_input.get("structure_sha256")
    observed_sha = observed_input.get("structure_sha256")
    requested_bytes = requested_input.get("structure_bytes")
    observed_bytes = observed_input.get("structure_bytes")
    if (
        not isinstance(requested_sha, str)
        or SHA256.fullmatch(requested_sha) is None
        or observed_sha != requested_sha
        or type(requested_bytes) is not int
        or requested_bytes < 1
        or observed_bytes != requested_bytes
        or not isinstance(requested_input.get("structure"), str)
        or not isinstance(observed_input.get("structure"), str)
    ):
        return False
    normalized_observed = dict(observed)
    normalized_observed["input"] = {
        **observed_input,
        "structure": requested_input["structure"],
    }
    if normalized_observed == requested:
        return True
    requested_execution = requested.get("execution")
    observed_execution = observed.get("execution")
    if not isinstance(requested_execution, Mapping) or not isinstance(observed_execution, Mapping):
        return False
    scheduler_gpu_id = requested_execution.get("gpu_id")
    if not isinstance(scheduler_gpu_id, str):
        return False
    expected_execution = {
        **requested_execution,
        "gpu_id": "0",
        "scheduler_gpu_id": scheduler_gpu_id,
    }
    if qualified_gpu_offload is not None:
        expected_execution["gpu_offload"] = qualified_gpu_offload
    expected_observed = dict(requested)
    expected_observed["input"] = normalized_observed["input"]
    expected_observed["execution"] = expected_execution
    return normalized_observed == expected_observed
