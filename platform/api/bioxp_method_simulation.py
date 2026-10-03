"""Advisory planned accounting, never physical readback or an execution gate."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
import re


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _text(value):
    return format(value, "f") if value is not None else None


def channel_wells(reference_well: str, channels: list[int], *, row_increment: int,
                  column_increment: int, reference_channel: int) -> list[dict]:
    """Authored signed fixed-head increments. No independent-channel XY assumption."""
    match = re.fullmatch(r"([A-Z])([1-9][0-9]*)", reference_well)
    if not match:
        raise ValueError("Expected reference well A1-style")
    rows = []
    for channel in channels:
        row = ord(match[1]) + (channel - reference_channel) * row_increment
        column = int(match[2]) + (channel - reference_channel) * column_increment
        if not ord("A") <= row <= ord("Z") or column < 1:
            raise ValueError("Signed head mapping outside well address space")
        rows.append({"channel": channel, "well": f"{chr(row)}{column}",
                     "head_reference": reference_well})
    return rows


def plan_tip_policy(occurrences: list[dict], policy: str | dict) -> dict:
    """Insert attributed logical tips after expansion. Native pickup mapping is separate.

    per_source reuses only across consecutive identical ordered sources/channels;
    per_step keys occurrence.step_id + call_path + loop_path so repeated step IDs
    are not silently conflated. Manual preserves rows exactly and inserts nothing.
    """
    config = {"mode": policy} if isinstance(policy, str) else deepcopy(policy)
    mode = config.get("mode", "manual")
    if mode == "manual":
        return {"occurrences": deepcopy(occurrences), "issues": []}
    if mode not in {"per_transfer", "per_source", "per_step"}:
        return {"occurrences": deepcopy(occurrences), "issues": [
            {"code": "unknown_tip_policy", "category": "advisory", "message": str(mode)}]}
    result, active, prior = [], False, None
    liquid_actions = {"transfer", "distribute", "consolidate", "aspirate", "dispense", "mix"}

    def generated(action, occurrence):
        return {"occurrence_id": str(occurrence.get("occurrence_id", "")) + "/policy/" + action,
                "step_id": occurrence.get("step_id"), "action": action,
                "inputs": deepcopy(config.get(action, {})),
                "generated_by": {"policy": mode, "occurrence_id": occurrence.get("occurrence_id")}}

    previous = None
    for occurrence in occurrences:
        if occurrence.get("action") in {"tip_pickup", "tip_eject"}:
            active, prior = False, None
        if occurrence.get("action") in liquid_actions:
            inputs = occurrence.get("inputs", {})
            transfers = inputs.get("channel_transfers", [])
            if mode == "per_source":
                key = repr([(t.get("channel"), t.get("source")) for t in transfers])
            elif mode == "per_step":
                key = repr([occurrence.get(k) for k in ("step_id", "call_path", "loop_path")])
            else:
                # Lowered move/aspirate/dispense children share an explicit
                # logical transfer identity; never eject between its strokes.
                key = occurrence.get("transfer_id", object())
            if active and previous and occurrence.get("transfer_id") is not None and occurrence.get("transfer_id") == previous.get("transfer_id"):
                key = prior
            if not active or key != prior:
                if active:
                    result.append(generated("tip_eject", previous))
                result.append(generated("tip_pickup", occurrence))
                active, prior = True, key
            previous = occurrence
        result.append(deepcopy(occurrence))
    if active:
        result.append(generated("tip_eject", previous))
    return {"occurrences": result, "issues": []}


def simulate_method(occurrences: list[dict], initial_state: dict | None = None) -> dict:
    """Account explicitly lowered channel transfers; preserve unknown initial quantities.

    Missing endpoints refer to the channel tip for aspirate/dispense. Transfer rows
    have both endpoints. ``effects`` overrides planned rows for partial-result review;
    no effects means no inferred completed prefix. State layers stay separate.
    """
    state = deepcopy(initial_state or {})
    vessels, channels = state.setdefault("vessels", {}), state.setdefault("channels", {})
    labware = state.setdefault("labware", {})
    issues, lineage, snapshots, strokes = [], [], [], []
    totals = {k: Decimal(0) for k in ("liquid", "conditioning_return", "excess", "waste", "air", "commanded_displacement")}
    unknown_totals = set()
    known_time, unknown_time = Decimal(0), []

    def issue(code, occurrence, message):
        issues.append({"code": code, "category": "advisory", "message": message,
                       "occurrence_id": occurrence.get("occurrence_id")})

    def vessel(endpoint):
        if not isinstance(endpoint, dict) or "labware_id" not in endpoint or "well" not in endpoint:
            return None, None
        key = f"{endpoint['labware_id']}:{endpoint['well']}"
        touched_vessels.add(key)
        return vessels.setdefault(key, {"volume_ul": None, "materials": None}), key

    def change(container, delta):
        current = _number(container.get("volume_ul"))
        container["volume_ul"] = _text(current + delta) if current is not None and delta is not None else None
        known_delta = _number(container.get("known_delta_ul", 0))
        if delta is not None:
            container["known_delta_ul"] = _text((known_delta or Decimal(0)) + delta)
        else:
            container["unknown_delta"] = True

    # Decimal precision is local to this invocation, not process-global.
    with localcontext() as decimal_context:
        decimal_context.prec = 50
        for occurrence in occurrences:
            action, inputs = occurrence.get("action"), occurrence.get("inputs", {})
            oid = occurrence.get("occurrence_id")
            if occurrence.get("status") in {"not_run", "skipped"}:
                snapshots.append({"occurrence_id": oid, "state_delta": {}})
                continue
            if occurrence.get("status") in {"failed", "partial", "unknown"} and action not in {
                    "aspirate", "dispense", "transfer", "distribute", "consolidate"}:
                issue("partial_effects_unknown", occurrence, "Non-liquid partial state is unknown; requested pose/custody is not completion evidence.")
                snapshots.append({"occurrence_id": oid, "state_delta": {}, "unknown_effects": True})
                unknown_time.append(oid)
                continue
            touched_vessels, touched_channels = set(), set()
            contact_offsets = {key: len(value.get("contacts", [])) for key, value in channels.items()}
            changed_other = {}
            duration = _number(inputs.get("duration_ms"))
            if duration is not None and inputs.get("duration_semantics") == "dispatch":
                known_time += duration
            else:
                unknown_time.append(oid)
            if action == "plate_move":
                identity = inputs.get("labware_id")
                if identity is not None:
                    labware.setdefault(identity, {})["station"] = deepcopy(inputs.get("destination_station"))
                    changed_other["labware"] = {identity: deepcopy(labware[identity])}
            if action in {"move", "park", "pipette_position"}:
                state["head_reference"] = deepcopy(inputs)
                changed_other["head_reference"] = deepcopy(inputs)
            if action in {"cover_move", "move_cover", "catch", "release"}:
                state.setdefault("custody", {})[str(inputs.get("object_id", "unknown"))] = deepcopy(inputs)
                changed_other["custody"] = {str(inputs.get("object_id", "unknown")): deepcopy(inputs)}
            if action in {"thermal_start", "thermal_wait", "thermal_profile"}:
                state.setdefault("thermal_tasks", {})[str(inputs.get("task_id", oid))] = {
                    "action": action, "inputs": deepcopy(inputs), "attainment": "unknown"}
                changed_other["thermal_tasks"] = {str(inputs.get("task_id", oid)): deepcopy(state["thermal_tasks"][str(inputs.get("task_id", oid))])}
            if action in {"tip_pickup", "tip_eject"}:
                for channel in inputs.get("channels", []):
                    touched_channels.add(str(channel))
                    tip = channels.setdefault(str(channel), {})
                    if action == "tip_pickup":
                        tip.update(tip_loaded=True, volume_ul="0", air_ul="0", materials=[], contacts=[])
                    else:
                        tip.update(tip_loaded=False, volume_ul="0", air_ul="0", materials=[])
            rows = inputs.get("channel_transfers", [])
            if occurrence.get("status") in {"failed", "partial", "unknown", "not_run", "skipped"}:
                rows = occurrence.get("effects", [])
                if occurrence.get("status") not in {"not_run", "skipped"}:
                    issue("partial_effects_unknown", occurrence, "Only explicitly reported effects accounted; remaining effects unknown.")
            if action in {"aspirate", "dispense", "transfer", "distribute", "consolidate"} and not rows:
                issue("liquid_effects_unbound", occurrence, "No explicit channel liquid effects; volume/geometry remain unknown.")
            for row in rows:
                channel = row.get("channel")
                if channel is None:
                    issue("channel_unknown", occurrence, "Liquid effect lacks channel association.")
                    continue
                tip = channels.setdefault(str(channel), {"tip_loaded": None, "volume_ul": None,
                                                         "air_ul": None, "contacts": [], "materials": None})
                touched_channels.add(str(channel))
                source, source_key = vessel(row.get("source"))
                destination, destination_key = vessel(row.get("destination"))
                if action == "aspirate" and destination is None:
                    destination, destination_key = tip, f"tip:{channel}"
                if action == "dispense" and source is None:
                    source, source_key = tip, f"tip:{channel}"
                volume = _number(row.get("volume_ul"))
                if volume is not None and volume < 0:
                    issue("negative_liquid_volume", occurrence, "Negative authored volume retained as unknown effect.")
                    volume = None
                kind = row.get("kind", "liquid")
                if kind not in totals:
                    issue("accounting_kind_unknown", occurrence, str(kind))
                    kind = "liquid"
                if volume is None:
                    unknown_totals.add(kind)
                else:
                    totals[kind] += volume
                materials = deepcopy(source.get("materials")) if source is not None else None
                if source is not None:
                    change(source, -volume if volume is not None else None)
                    remaining = _number(source.get("volume_ul"))
                    if remaining is not None and remaining < 0:
                        issue("planned_volume_deficit", occurrence, "Authored accounting exceeds known source fill; not a runtime gate.")
                if destination is not None:
                    change(destination, volume)
                    existing = destination.get("materials")
                    destination["materials"] = (list(dict.fromkeys(existing + materials))
                                                if isinstance(existing, list) and isinstance(materials, list) else None)
                if source is None or destination is None:
                    issue("endpoint_unknown", occurrence, "Unbound liquid endpoint; accounting is partial.")
                for endpoint, endpoint_key in ((source, source_key), (destination, destination_key)):
                    if endpoint is not None and endpoint is not tip:
                        tip.setdefault("contacts", []).append({"occurrence_id": oid, "vessel": endpoint_key,
                                                               "materials": deepcopy(endpoint.get("materials")),
                                                               "contact_mode": row.get("contact_mode", "unknown"),
                                                               "contact": (True if endpoint is source else
                                                                           False if row.get("contact_mode") in {"free", "non-contact"} else
                                                                           True if row.get("contact_mode") == "contact" else None)})
                for field, total in (("air_ul", "air"), ("commanded_displacement_ul", "commanded_displacement")):
                    if field in row:
                        amount = _number(row[field])
                        if amount is None:
                            unknown_totals.add(total)
                        else:
                            totals[total] += amount
                        if field == "air_ul" and action in {"aspirate", "dispense"}:
                            current_air = _number(tip.get("air_ul"))
                            sign = 1 if action == "aspirate" else -1
                            tip["air_ul"] = _text(current_air + sign * amount) if current_air is not None and amount is not None else None
                strokes.append({"occurrence_id": oid, "channel": channel,
                                "commanded_displacement_ul": deepcopy(row.get("commanded_displacement_ul")),
                                "air_ul": deepcopy(row.get("air_ul")),
                                "dispense_segments": deepcopy(row.get("dispense_segments")),
                                "final_empty_tip": deepcopy(row.get("final_empty_tip"))})
                lineage.append({"occurrence_id": oid, "channel": channel, "source": source_key,
                                "destination": destination_key, "volume_ul": _text(volume),
                                "kind": kind, "materials": materials,
                                "transformation": deepcopy(row.get("transformation"))})
            # Compact per-occurrence deltas avoid copying the whole deck and
            # cumulative contact history at every primitive in a large method.
            channel_delta = {}
            for key in sorted(touched_channels):
                channel_delta[key] = {k: deepcopy(v) for k, v in channels[key].items() if k != "contacts"}
                channel_delta[key]["contacts_append"] = deepcopy(channels[key].get("contacts", [])[contact_offsets.get(key, 0):])
                if action == "tip_pickup":
                    channel_delta[key]["contacts_reset"] = True
            snapshots.append({"occurrence_id": oid, "state_delta": {
                "vessels": {key: deepcopy(vessels[key]) for key in sorted(touched_vessels)},
                "channels": channel_delta, **changed_other}})
    return {"layer": "planned", "state": state, "observed": None, "issues": issues,
            "lineage": lineage, "strokes": strokes, "after_occurrences": snapshots,
            "accounting": {k: {"known_ul": _text(v), "has_unknown": k in unknown_totals}
                           for k, v in totals.items()},
            "time": {"known_duration_ms": _text(known_time), "unknown_occurrences": unknown_time,
                     "eta_ms": None},
            "limitations": ["Not hardware readback", "No reaction yield, evaporation or purification prediction",
                            "Corrected displacement and segments are not measured liquid delivery"]}
