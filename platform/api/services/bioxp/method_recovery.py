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

def _setting_values(params):
    """Return the sparse frozen settings and editable keys, not assumed hardware defaults."""
    recipe = params.get('recipe') or {}
    if params.get('operation') != 'cavro_liquid_recipe' or recipe.get('mode') not in ('single', 'multi'):
        return {}, []
    values = {k: recipe[k] for k in ('aspiration_delay_ms', 'dispense_delay_ms') if k in recipe}
    keys = ['aspiration_delay_ms', 'dispense_delay_ms']
    detection = recipe.get('liquid_detection')
    multi = recipe.get('multi') or {}
    sources = [recipe.get('before_liquid', [])]
    if multi.get('conditioning_back_to_source_count'):
        sources.append(multi.get('conditioning_before', []))
    destinations = ([r.get('before', []) for r in multi.get('aliquots', [])] if multi else [recipe.get('before_dispense', [])])
    for key, hooks in [('source_offset_steps', sources), ('destination_offset_steps', destinations)]:
        if key == 'source_offset_steps' and (detection or recipe.get('aspiration_tracking')):
            continue
        lowers = [[s['positioning'] for s in steps if s.get('operation') == 'position'
                   and s.get('positioning', {}).get('operation') == 'lower' and not s.get('rack_transform')] for steps in hooks]
        if lowers and all(len(rows) == 1 for rows in lowers):
            keys.append(key)
            offsets = [rows[0].get('offset_steps') for rows in lowers]
            if any(v is not None for v in offsets):
                values[key] = offsets[0] if all(v == offsets[0] for v in offsets) else offsets
    if detection:
        key = 'on_contact_delay_ms' if detection.get('mode') == 'on_contact' else 'submerge_steps'
        keys.append(key)
        if key in detection:
            values[key] = detection[key]
        if detection.get('sensing', 'pressure') != 'capacitive':
            from .protocol_models import PRESSURE_LIMITS
            keys.extend(k for k in PRESSURE_LIMITS if k != 'hybrid_interval_ms' or detection.get('sensing') == 'hybrid')
            values.update({k: v for k, v in (detection.get('pressure_overrides') or {}).items() if k in PRESSURE_LIMITS})
    return values, keys


def _attempt_pressure_refs(result, job_id, data):
    # Only the validated selected result owns the references. A sibling,
    # earlier attempt or stand-alone capture is not evidence for this stroke.
    refs = result.get('pressure_traces', [])
    identity = {'job_id': job_id, 'source_occurrence_id': data.source_occurrence_id,
                'native_action_id': data.native_action_id, 'attempt': data.expected_attempt}
    found = {}
    uncertain = not isinstance(refs, list)
    for ref in refs if isinstance(refs, list) else []:
        if (not isinstance(ref, dict) or result.get('source_occurrence_id') != data.source_occurrence_id
                or any(k in ref and ref[k] != v for k, v in identity.items())):
            uncertain = True
            continue
        stream = ref.get('stream_session_id')
        if isinstance(stream, str) and stream.strip():
            found[stream] = {'stream_session_id': stream}
        else:
            uncertain = True
        if ref.get('observation_error'):
            uncertain = True
    return {'pressure_traces': list(found.values()), **({'pressure_trace_warning':
            'Some pressure evidence could not be verified for this attempt.'} if uncertain else {})}


def preview_recovery(job, snapshot, data):
    """Read-only preview. The native executor arbitrates the eventual control."""

    aid = data.native_action_id
    selection = {"occurrence_id": data.source_occurrence_id, "native_action_id": aid}
    resolution = resolve_occurrence(snapshot or {}, selection)
    owners = resolve_occurrence(snapshot or {}, {"native_action_id": aid})
    if resolution["status"] != "matched" or (owners["status"] != "matched" and data.scope != "occurrence"):
        raise ValueError("Recovery requires one unambiguous frozen occurrence and native action")
    row, = resolution["matches"]
    actions = [a for stage in job.protocol.document.get("stages", []) for a in stage.get("actions", [])
               if a.get("action_id") == aid]
    if len(actions) != 1:
        raise ValueError("Selected action does not match the frozen document")
    action, = actions
    kind = action["kind"]
    params = deepcopy(action.get("params", {}))
    sequence = action.get('metadata', {}).get('bms_method', {}).get('sequence') or []
    projection = None
    if len(sequence) > 1 or owners['status'] != 'matched':
        members = [m.get('occurrence_id') for m in sequence]
        if (data.scope != 'occurrence' or kind != 'pipette_position' or params.get('operation') != 'move_sequence'
                or len(sequence) != len(params.get('targets', [])) or len(set(members)) != len(sequence)
                or set(members) != {r['occurrence_id'] for r in owners['matches']}
                or any(m.get('target_index') != i for i, m in enumerate(sequence)) or data.source_occurrence_id not in members):
            raise ValueError('Shared action cannot be projected onto one frozen target')
        projection = members.index(data.source_occurrence_id)
    elif action.get('source_occurrence_id') != data.source_occurrence_id:
        raise ValueError('Frozen action occurrence mismatch')
    previous, slots = _setting_values(params)
    if data.scope == "action" and kind not in {"pipette_aspirate", "pipette_dispense", "pipette_mix"} and not (
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
    if projection is None and any(r.get("source_occurrence_id") != data.source_occurrence_id for r in history):
        raise ValueError("Attempt history does not match selected occurrence")
    latest = history[-1]
    if data.decision == "repeat":
        if latest.get("ok") is not True:
            raise ValueError("Repeat requires a completed attempt")
    elif workflow.gate != "error_hold" or latest.get("hold_id") != data.gate_id:
        raise ValueError("Decision does not address the held attempt")
    if projection is not None and data.decision == 'retry' and (latest.get('projected_target_index') != projection or latest.get('source_occurrence_id') != data.source_occurrence_id):
        raise ValueError('Cannot resume an indivisible failed sequence')
    ids = row["native_action_ids"] if data.scope == 'occurrence' else [aid]
    affected = [a for stage in job.protocol.document.get('stages', []) for a in stage.get('actions', []) if a.get('action_id') in ids]
    if [a['action_id'] for a in affected] != ids or projection is None and any(a.get('source_occurrence_id') != data.source_occurrence_id for a in affected):
        raise ValueError('Occurrence children differ from the frozen document')
    if data.scope == 'occurrence':
        if data.decision not in ('retry', 'repeat'):
            raise ValueError('Occurrence scope requires a redo decision')
        if data.decision == 'repeat' and any(not next((r.get('ok') is True for r in reversed(state.action_results) if r.get('action_id') == child), False) for child in ids):
            raise ValueError('Only fully completed occurrences can be repeated')
        groups = [[a for a in stage.get('actions', [])] for stage in job.protocol.document.get('stages', [])]
        if not any(affected[0] in group and group[group.index(affected[0]):group.index(affected[0])+len(affected)] == affected for group in groups) or any(a['kind'] == 'oem_operation' for a in affected):
            raise ValueError('Occurrence is not a consecutive authored step')
        if data.decision == 'retry':
            affected = affected[:ids.index(aid) + 1]  # suspended cursor owns the untouched suffix
    if data.decision in ('skip', 'manual_complete'):
        affected = []
    for result in history[1:]:
        edits = result.get("overrides")
        if not isinstance(edits, dict) or edits.keys() - set(slots):
            raise ValueError("Recorded attempt settings cannot be reconstructed")
        previous.update(edits)
    for key in data.overrides:
        if key not in slots:
            raise ValueError(f"{key} is not an attempt-local setting on this action")
    siblings = set(row["native_action_ids"]) - {aid}
    return {"job_id": job.job_id, **data.model_dump(), "occurrence": row,
            **_attempt_pressure_refs(latest, job.job_id, data),
            "previous_settings": previous, "proposed_settings": {**previous, **data.overrides},
            "projected_target": params["targets"][projection] if projection is not None else None,
            "affected_actions": [{"action_id": a["action_id"], "kind": a["kind"]} for a in affected],
            "editable_settings": list(slots), "attempt_results": history,
            "sibling_results": [r for r in state.action_results if r.get("action_id") in siblings],
            "submitted": False}
