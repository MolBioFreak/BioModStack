"""Closed typed contract for general RFD3 generation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.lib.rfd3_scientific_settings import EXECUTION_FIELDS, execution_settings, integer, conditioning_settings

REQUEST_SCHEMA = "bms.rfd3.generation.request.v1"
RESULT_MANIFEST_SCHEMA = "bms.rfd3.generation.result-manifest.v1"
PREPARATION_RECEIPT_SCHEMA = "bms.rfd3.generation.preparation-receipt.v1"

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class ContractError(ValueError):
    """Raised when generation authority or its outputs are invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _exact(value: Any, expected: set[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    actual = set(value)
    if actual != expected:
        raise ContractError(
            f"{field} must have exact fields; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )
    return value


def _integer(value: Any, field: str, minimum: int, maximum: int | None = None) -> int:
    try:
        return integer(value, field, minimum, maximum)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc


def validate_request(value: Any) -> dict[str, Any]:
    optional = {key for key in ("conditioning", "source", "source_mapping", "source_model") if isinstance(value, Mapping) and key in value}
    request = _exact(value, {"schema", "request_id", "job_id", "generation", "execution"} | optional, "request")
    if "conditioning" in request:
        try:
            conditioning_settings(request["conditioning"])
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
    if "source" in request:
        _exact(request["source"], {"path", "sha256"}, "source")
    if (request.get("conditioning") or {}).get("partial_t") is not None and "source" not in request:
        raise ContractError("partial_t (partial diffusion) requires an input structure")
    if request["schema"] != REQUEST_SCHEMA:
        raise ContractError(f"unsupported request schema: {request['schema']!r}")
    for field in ("request_id", "job_id"):
        if not isinstance(request[field], str) or not _ID.fullmatch(request[field]):
            raise ContractError(f"{field} must be a bounded safe identifier")

    generation = _exact(
        request["generation"], {"min_length", "max_length", "num_designs"}, "generation"
    )
    min_length = _integer(generation["min_length"], "generation.min_length", 1)
    max_length = _integer(generation["max_length"], "generation.max_length", 1)
    if min_length > max_length:
        raise ContractError("generation.min_length must not exceed generation.max_length")
    _integer(generation["num_designs"], "generation.num_designs", 1)

    execution = request["execution"]
    if not isinstance(execution, Mapping) or set(execution) - (set(EXECUTION_FIELDS) | {"seed"}):
        raise ContractError("unsupported execution settings")
    if not {"seed", "dump_trajectories"} <= set(execution):
        raise ContractError("execution requires seed and dump_trajectories")
    try:
        execution_settings(execution)
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    seed = execution["seed"]
    if seed is not None:
        _integer(seed, "execution.seed", 0, 2_147_483_647)
    if not isinstance(execution["dump_trajectories"], bool):
        raise ContractError("execution.dump_trajectories must be boolean")
    return dict(request)
