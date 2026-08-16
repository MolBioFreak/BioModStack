from __future__ import annotations

from typing import Any, Dict, List, Optional

BOLTZ_CP_DEFAULT_SHARD_PLAN_ID = "2x2"
BOLTZ_CP_SHARD_PLANS: List[Dict[str, Any]] = [
    {
        "id": "1x1",
        "label": "1×1 (single logical shard)",
        "topology": "1x1",
        "logical_size_cp": 1,
        "description": "No logical sharding; useful for fallback/debug runs.",
    },
    {
        "id": "2x2",
        "label": "2×2 (4 logical shards)",
        "topology": "2x2",
        "logical_size_cp": 4,
        "description": "Defines a 2×2 logical tile mesh. The selected logical plan does not change with GPU count.",
    },
    {
        "id": "4x4",
        "label": "4×4 (16 logical shards)",
        "topology": "4x4",
        "logical_size_cp": 16,
        "description": "Defines a 4×4 logical tile mesh. The selected logical plan does not change with GPU count.",
    },
]

_BOLTZ_CP_LOGICAL_SIZE_CP_BY_ID = {
    plan["id"]: int(plan["logical_size_cp"])
    for plan in BOLTZ_CP_SHARD_PLANS
}
_BOLTZ_CP_SHARD_PLAN_ID_BY_SIZE_CP = {
    int(plan["logical_size_cp"]): str(plan["id"])
    for plan in BOLTZ_CP_SHARD_PLANS
}


def largest_square_divisor(value_count: int, requested_size_cp: object = None) -> int:
    if value_count < 1:
        return 1

    try:
        requested = int(requested_size_cp)
    except (TypeError, ValueError):
        requested = value_count
    if requested < 1:
        requested = value_count

    best = 1
    for candidate in range(1, value_count + 1):
        if value_count % candidate != 0:
            continue
        root = int(candidate ** 0.5)
        if root * root != candidate or candidate > requested:
            continue
        best = candidate
    return best


def coerce_boltz_cp_shard_plan_id(value: object, *, default: Optional[str] = None) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if normalized in _BOLTZ_CP_LOGICAL_SIZE_CP_BY_ID:
        return normalized
    return default


def get_boltz_cp_logical_size_cp(shard_plan_id: object, fallback: object = None) -> int:
    normalized = coerce_boltz_cp_shard_plan_id(shard_plan_id)
    if normalized:
        return _BOLTZ_CP_LOGICAL_SIZE_CP_BY_ID[normalized]
    try:
        parsed_fallback = int(fallback)
    except (TypeError, ValueError):
        parsed_fallback = _BOLTZ_CP_LOGICAL_SIZE_CP_BY_ID[BOLTZ_CP_DEFAULT_SHARD_PLAN_ID]
    return parsed_fallback if parsed_fallback > 0 else _BOLTZ_CP_LOGICAL_SIZE_CP_BY_ID[BOLTZ_CP_DEFAULT_SHARD_PLAN_ID]


def infer_boltz_cp_shard_plan_id(size_cp: object, *, default: Optional[str] = BOLTZ_CP_DEFAULT_SHARD_PLAN_ID) -> Optional[str]:
    try:
        parsed = int(size_cp)
    except (TypeError, ValueError):
        return default
    return _BOLTZ_CP_SHARD_PLAN_ID_BY_SIZE_CP.get(parsed, default)


def get_boltz_cp_shard_plan_catalog(max_physical_gpu_count: int = 1) -> Dict[str, Any]:
    capped_gpu_count = max(1, int(max_physical_gpu_count or 1))
    plans: List[Dict[str, Any]] = []
    for plan in BOLTZ_CP_SHARD_PLANS:
        logical_size_cp = int(plan["logical_size_cp"])
        plans.append(
            {
                **plan,
                "physical_gpu_resolutions": [
                    {
                        "gpu_count": gpu_count,
                        "launch_size_cp": largest_square_divisor(gpu_count, logical_size_cp),
                    }
                    for gpu_count in range(1, capped_gpu_count + 1)
                ],
            }
        )
    return {
        "default_plan_id": BOLTZ_CP_DEFAULT_SHARD_PLAN_ID,
        "plans": plans,
    }
