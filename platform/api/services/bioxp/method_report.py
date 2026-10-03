"""Read-only projection of native action progress onto immutable method provenance.

Never infer success, physical tip state, applied fields or no motion from missing
results. Native children remain individually visible for compound operations.
"""
from copy import deepcopy


def occurrence_outcomes(snapshot, state):
    compilation = (snapshot or {}).get("compilation")
    provenance = compilation.get("provenance", []) if isinstance(compilation, dict) else []
    if not isinstance(provenance, list):
        provenance = []
    results = {}
    for result in state.action_results:
        action_id = result.get("action_id")
        if isinstance(action_id, str):
            results.setdefault(action_id, []).append(deepcopy(result))
    completed = {action_id for stage in state.stage_states.values() for action_id in stage.completed_actions}
    current = {stage.current_action_id: stage for stage in state.stage_states.values() if stage.current_action_id}
    rows = []
    for occurrence in provenance:
        if not isinstance(occurrence, dict):
            continue
        children = []
        action_ids = occurrence.get("native_action_ids")
        for action_id in action_ids if isinstance(action_ids, list) else []:
            if not isinstance(action_id, str):
                continue
            evidence = results.get(action_id, [])
            stage = current.get(action_id)
            status = "unknown"
            # A returned explicit failure takes precedence over a stale cursor.
            if any(result.get("ok") is False for result in evidence):
                status = "failed"
            elif action_id in completed:
                status = "completed"
            elif stage is not None:
                status = {"paused": "paused", "running": "running", "failed": "failed"}.get(stage.status, "unknown")
            children.append({"action_id": action_id, "status": status, "results": evidence})
        statuses = {child["status"] for child in children}
        if statuses == {"completed"}:
            status = "completed"
        elif "failed" in statuses:
            status = "failed"
        elif "paused" in statuses:
            status = "paused"
        elif "running" in statuses:
            status = "running"
        elif "completed" in statuses:
            status = "partial"
        else:
            status = "unknown"
        rows.append({**deepcopy(occurrence), "status": status, "children": children})
    return rows
