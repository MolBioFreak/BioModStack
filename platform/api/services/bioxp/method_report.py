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
        action_id = result.get("action_id") or result.get("parent_action_id")
        if isinstance(action_id, str):
            results.setdefault(action_id, []).append(deepcopy(result))
    completed = {action_id for stage in state.stage_states.values() for action_id in stage.completed_actions}
    current = {stage.current_action_id: stage for stage in state.stage_states.values() if stage.current_action_id}
    applied = application_report(state)["fields"]
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
            owned = [child for result in evidence for child in result.get("child_outcomes", []) if isinstance(child, dict)]
            assessment = evidence + owned
            stage = current.get(action_id)
            status = "unknown"
            # A returned explicit failure takes precedence over a stale cursor.
            if any(result.get("ok") is False or result.get("status") == "failed" for result in assessment):
                status = "failed"
            elif any(result.get("uncertain") is True or result.get("status") == "ambiguous" for result in assessment):
                status = "unknown"
            elif any(result.get("pending") is True or result.get("status") == "running" for result in assessment):
                status = "running"
            elif evidence and all(result.get("status") in ("skipped", "not_run") for result in assessment):
                status = "skipped" if any(result.get("status") == "skipped" for result in assessment) else "not_run"
            elif action_id in completed:
                status = "completed"
            elif stage is not None:
                status = {"paused": "paused", "running": "running", "failed": "failed"}.get(stage.status, "unknown")
            children.append({"action_id": action_id, "status": status, "results": evidence,
                             "reported_applied": [deepcopy(field) for field in applied if field["action_id"] == action_id],
                             "child_outcomes": deepcopy(owned)})
        statuses = {child["status"] for child in children}
        if statuses == {"completed"}:
            status = "completed"
        elif statuses == {"skipped"} or statuses == {"not_run"}:
            status = next(iter(statuses))
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


def application_report(state):
    """Project native field events without decoding ASCII into invented values.

    Settings ACK/completion is not physical verification. Keep channel completion
    separate: one failed channel must not erase another channel's partial effects.
    """
    fields = []
    for action in state.action_results:
        # Native semantic results explicitly distinguish setpoint acceptance,
        # attained temperature, dwell, timer and profile completion.
        for field in ("setpoint_accepted", "temperature_reached", "dwell_complete",
                      "profile_complete", "elapsed_wait_complete", "timer_started", "timer_complete"):
            if field in action:
                fields.append({"action_id": action.get("action_id") or action.get("parent_action_id"),
                    "field": field, "operation": action.get("kind"),
                    "status": "unknown" if action[field] is None else "reported",
                    "reported_applied": {"value": deepcopy(action[field])}})
        application = action.get("pipette_result")
        if not isinstance(application, dict):
            continue
        for event in application.get("events", []):
            inputs = event.get("inputs", {})
            if not isinstance(inputs, dict) or "field" not in inputs:
                continue
            result = event.get("result") or event.get("partial") or {}
            fields.append({"action_id": action.get("action_id") or action.get("parent_action_id"),
                "field": inputs["field"], "operation_index": inputs.get("operation_index"),
                "operation": event.get("operation"), "source_identity": event.get("source_identity"),
                "inputs": deepcopy(inputs), "status": event.get("status", "unknown"),
                "reported_applied": deepcopy(event.get("reported_applied", {"status": "unknown"})),
                "channels": deepcopy(result.get("channels", [])),
                "physical_effect_verified": result.get("physical_effect_verified")})
    return {"status": "reported" if fields else "unknown", "fields": fields,
            "reason": "Native controller/application evidence; not inferred physical application"}


def duration_report(state):
    """Receipt dispatch-to-finish clocks, never job create/update or dwell input.

    Child intervals may overlap and exclude holds; do not sum them or present
    their span as the complete method's elapsed execution time.
    """
    import math
    intervals = []
    for result in state.action_results:
        receipt = result.get("receipt")
        if not isinstance(receipt, dict):
            continue
        start, end = receipt.get("dispatched_at"), receipt.get("finished_at")
        if all(type(value) in (int, float) and math.isfinite(value) for value in (start, end)) and end >= start:
            intervals.append({"action_id": result.get("action_id") or result.get("parent_action_id"),
                "command_id": receipt.get("command_id"), "value": end - start, "unit": "seconds",
                "start": start, "end": end, "clock": "native_receipt_dispatch_to_finish"})
    return {"value": None, "status": "unknown", "unit": "seconds", "action_intervals": intervals,
            "reason": "No whole-method execution clock; child receipt intervals exclude holds and may overlap"}
