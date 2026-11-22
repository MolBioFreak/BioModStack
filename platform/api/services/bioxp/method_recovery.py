"""Advisory identity resolution against immutable compilation; never admission."""
from copy import deepcopy
import json


def original_occurrences(snapshot):
    compilation = snapshot.get("compilation")
    provenance = compilation.get("provenance") if isinstance(compilation, dict) else None
    return deepcopy([row for row in provenance if isinstance(row, dict)]) if isinstance(provenance, list) else []


def resolve_occurrence(snapshot, requested):
    rows = original_occurrences(snapshot)
    if requested is None:
        return {"status": "not_requested", "matches": [], "unmatched": []}
    # Compare every supplied provenance component, including nested loop/call
    # paths. A step ID alone is not necessarily an expanded occurrence identity.
    def equal(a, b):
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    def matches(row, key, value):
        if key in ("action_id", "native_action_id"):
            return isinstance(value, str) and value in row.get("native_action_ids", [])
        return key in row and equal(row[key], value)
    found = [row for row in rows if requested and all(matches(row, k, v) for k, v in requested.items())]
    unmatched = [{"field": k, "value": deepcopy(v), "reason": "not_in_original_provenance"}
                 for k, v in requested.items() if not any(matches(row, k, v) for row in rows)]
    if not found and not unmatched:
        unmatched = [{"field": "/occurrence", "value": deepcopy(requested),
                      "reason": "components_do_not_identify_the_same_occurrence"}]
    return {"status": "matched" if len(found) == 1 else "ambiguous" if found else "unmatched",
            "matches": deepcopy(found), "unmatched": unmatched}

def _setting_slots(params):
    recipe = params.get("recipe")
    if params.get("operation") != "cavro_liquid_recipe" or not isinstance(recipe, dict) or recipe.get("mode") != "single":
        return {}
    slots = {"aspiration_delay_ms": recipe}
    detection = recipe.get("liquid_detection")
    if isinstance(detection, dict):
        slots.update({key: detection for key in ("submerge_steps", "on_contact_delay_ms")})
    return slots


def preview_recovery(job, snapshot, data):
    """Read-only preview. The native executor arbitrates the eventual control."""
    from bioxp_method_native import native_action

    aid = data.native_action_id
    selection = {"occurrence_id": data.source_occurrence_id, "native_action_id": aid}
    resolution = resolve_occurrence(snapshot or {}, selection)
    owners = resolve_occurrence(snapshot or {}, {"native_action_id": aid})
    if resolution["status"] != "matched" or owners["status"] != "matched":
        raise ValueError("Recovery requires one unambiguous frozen occurrence and native action")
    row, = resolution["matches"]
    actions = [a for stage in job.protocol.document.get("stages", []) for a in stage.get("actions", [])
               if a.get("action_id") == aid]
    if len(actions) != 1 or actions[0].get("source_occurrence_id") != data.source_occurrence_id:
        raise ValueError("Selected action does not match the frozen document")
    action, = actions
    kind = action["kind"]
    params = deepcopy(action.get("params", {}))
    slots = _setting_slots(params)
    if kind not in {"pipette_aspirate", "pipette_dispense", "pipette_mix"} and not (
            kind == "pipette_manual_physical" and slots):
        raise ValueError("Selected action is not a supported pipetting scope")
    state = job.execution.runtime_state
    workflow = state.workflow
    if (job.command is None or job.command.terminal or not workflow or workflow.phase != "waiting"
            or workflow.gate_id != data.gate_id or workflow.gate not in ("ordinary_pause", "error_hold")):
        raise ValueError("Selected hold is not current")
    history = [result for result in state.action_results if result.get("action_id") == aid and "attempt" in result]
    # Native records sparse changes on each successive attempt. Never fall back
    # to the original settings if the intervening attempt history is missing.
    if ([result["attempt"] for result in history] != list(range(1, len(history) + 1))
            or len(history) != data.expected_attempt):
        raise ValueError("Selected attempt history is incomplete or stale")
    if any(r.get("source_occurrence_id") != data.source_occurrence_id for r in history):
        raise ValueError("Attempt history does not match selected occurrence")
    latest = history[-1]
    if data.decision == "repeat":
        if latest.get("ok") is not True:
            raise ValueError("Repeat requires a completed attempt")
    elif workflow.gate != "error_hold" or latest.get("hold_id") != data.gate_id:
        raise ValueError("Decision does not address the held attempt")
    for result in history[1:]:
        edits = result.get("overrides")
        if not isinstance(edits, dict) or edits.keys() - slots.keys():
            raise ValueError("Recorded attempt settings cannot be reconstructed")
        for key, value in edits.items():
            slots[key][key] = value
    previous = {key: parent[key] for key, parent in slots.items() if key in parent}
    for key, value in data.overrides.items():
        if key not in slots:
            raise ValueError(f"{key} is not an attempt-local setting on this action")
        slots[key][key] = value
    if data.overrides:
        native_action("liquid_recipe", {"recipe": params["recipe"]})
    siblings = set(row["native_action_ids"]) - {aid}
    return {"job_id": job.job_id, **data.model_dump(), "occurrence": row,
            "previous_settings": previous, "proposed_settings": {**previous, **data.overrides},
            "editable_settings": list(slots), "attempt_results": history,
            "sibling_results": [r for r in state.action_results if r.get("action_id") in siblings],
            "submitted": False}
