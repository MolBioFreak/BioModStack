"""MD projection for the shared execution-plan compiler (placement-independent).

Call only with the existing globally validated/effective native MD config. This
is a graph/dependency projection, not a second scientific request schema.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from .runner import replica_seed


def compile_md_closure(config: Mapping[str, Any]) -> dict[str, Any]:
    request = copy.deepcopy(dict(config))
    if request.get("schema") not in {"bms.md.job.v1", "bms.md.job.v2"}:
        raise ValueError("MD closure requires a validated native request")
    count, seed, engine, job_id = (request.get(key) for key in ("replicas", "random_seed", "engine", "job_id"))
    if (type(count) is not int or not 1 <= count <= 64 or type(seed) is not int
            or not 1 <= seed <= 2147483647 or engine not in {"gromacs", "openmm"}
            or not isinstance(job_id, str) or not job_id):
        raise ValueError("MD closure identity is invalid")
    prefix = f"{job_id}:"
    nodes = [{"id": prefix + "md_preparation", "process": "MD_PREPARE_CONFIG", "requires": []}]
    replicas = []
    analyses = []
    for index in range(count):
        identity = prefix + f"md_replica:{index}"
        replicas.append(identity)
        nodes.append({"id": identity, "process": f"MD_{engine.upper()}_REPLICA",
                      "requires": [prefix + "md_preparation"], "replica_index": index,
                      "replica_seed": replica_seed(seed, index)})
    nodes.append({"id": prefix + "md_aggregation", "process": "MD_JOIN_REPLICAS", "requires": replicas})
    for index in range(count):
        identity = prefix + f"md_analysis:{index}"
        analyses.append(identity)
        nodes.append({"id": identity, "process": "MD_ANALYZE_REPLICA",
                      "requires": [prefix + "md_aggregation"], "replica_index": index})
    nodes.append({"id": prefix + "md_completion", "process": "MD_SEAL_RESULTS",
                  "requires": [prefix + "md_aggregation", *analyses]})
    for node in nodes:
        node["required"] = True
    return {
        "schema": "bms.md.closure-plan.v1",
        "entrypoint": "workflows/experimental/molecular_dynamics/orchestrator.nf",
        "requested_config": request,
        "requested_config_sha256": hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "components": nodes,
        "engine_runtime": copy.deepcopy(request.get("engine_runtime")),
        "preparation_runtime": copy.deepcopy(request.get("chemistry", {}).get("runtime_identity")),
        "analysis_runtime_sha256": "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68",
        "required_outputs": ["manifest.json", "normalized_config.json", "preparation", "replicas", "analysis/manifest.json", "md_completion_barrier.json"],
        "replica_concurrency": 1,
        "analysis_concurrency": 1,
        "join_policy": "exact_set_all_required",
        "completion_contract": "bms.md.completion-barrier.v1",
    }
