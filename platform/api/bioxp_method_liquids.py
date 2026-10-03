"""Pure, source-backed liquid settings. No hardware, persistence or admission policy."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

SOURCE_PATH = Path(__file__).with_name("schemas") / "bioxp_liquid_classes.json"
# Fields are independent scientific choices; lists (segments/correction points) are atomic.
FIELDS = frozenset("""target_volume_ul commanded_corrected_aspiration_ul aspirate_speed_ul_s
blowout_leading_air_gap_ul carry_trailing_air_gap_ul dispense_segments aspiration_delay_ms
 dispense_delay_ms air_gap_aspiration_speed_ul_s separate_carry_dispense_speed_ul_s
separate_blowout_dispense_speed_ul_s conditioning_volume_ul immersion_depth_mm retract_speed_mm_s
slope cutoff_speed_ul_s start_speed_ul_s excess_volume_ul excess_destination dispense_volume_ul
number_of_dispenses number_back_to_source retract_distance_mm aspiration_top_speed_ul_s
 dispense_top_speed_ul_s reaspiration_volume_ul dispense_to_reaspiration_delay_ms slope_n1 slope_n2
contact_mode dispense_speed_ul_s final_empty_tip search_speed_mm_s tracking touch_off lld
pressure_streaming classifier_reference correction_points calibration_function channels tip_profile_id
head_reference tip_policy""".split())


def source_catalog_text() -> str:
    """Original bytes decoded UTF-8, including numeric spelling and source nulls."""
    return SOURCE_PATH.read_text(encoding="utf-8")


def load_source_catalog() -> dict:
    return json.loads(source_catalog_text())


def starter_profiles() -> list[dict]:
    """Source tip identity, deliberately not invented calibration/native support."""
    profiles = {}
    for entry in starter_entries():
        context = entry["context"]
        identity = context["tip_profile_id"]
        tip = entry["source"].get("tip", {})
        profiles.setdefault(identity, {
            "id": identity, "revision": 1, "generation": context["generation"],
            "family": tip.get("family", "Tecan LiHa disposable tips (DiTis)"),
            "nominal_capacity_ul": tip.get("nominal_capacity_ul", entry["source"].get("tip_size_without_filter_ul")),
            "filter_type": context["filter_type"], "source_variants": [],
            "native_addressing": None, "vessel_geometry": None, "dead_volume_ul": None,
            "tip_geometry": None, "tool_offsets": None, "rack_geometry": None,
            "adapter_support": "unknown", "consumable_fit": "unknown",
            "qualification": "not BioXP-qualified"})["source_variants"].append(entry["id"])
    return [profiles[key] for key in sorted(profiles)]


def recipe_accounting(settings: dict) -> dict:
    """Derived planning quantities, not manufacturer aggregate or measured delivery.

    Source return count and conditioning volume remain independent. An unknown
    member leaves the derived total unknown; no missing member is assumed zero.
    """
    def number(key):
        value = settings.get(key)
        try:
            result = Decimal(str(value))
            return result if not isinstance(value, bool) and result.is_finite() and result >= 0 else None
        except (InvalidOperation, ValueError):
            return None

    count, aliquot = number("number_of_dispenses"), number("dispense_volume_ul")
    back, conditioning = number("number_back_to_source"), number("conditioning_volume_ul")
    samples = count * aliquot if count is not None and count == count.to_integral_value() and aliquot is not None else None
    returns = back * conditioning if back is not None and back == back.to_integral_value() and conditioning is not None else None
    excess = number("excess_volume_ul")
    def text(value):
        return format(value, "f") if value is not None else None
    return {"basis": "derived intended-liquid accounting; not measured liquid or a source aggregate",
            "sample_liquid_ul": text(samples), "conditioning_return_ul": text(returns),
            "reserved_excess_ul": text(excess),
            "planned_loaded_liquid_ul": text(samples + returns + excess) if samples is not None and returns is not None and excess is not None else None,
            "commanded_corrected_aspiration_ul": deepcopy(settings.get("commanded_corrected_aspiration_ul")),
            "leading_air_ul": deepcopy(settings.get("blowout_leading_air_gap_ul")),
            "trailing_air_ul": deepcopy(settings.get("carry_trailing_air_gap_ul")),
            "dispense_segments": deepcopy(settings.get("dispense_segments")),
            "excess_destination": deepcopy(settings.get("excess_destination")),
            "reaspiration_volume_ul": deepcopy(settings.get("reaspiration_volume_ul")),
            "dispense_to_reaspiration_delay_ms": deepcopy(settings.get("dispense_to_reaspiration_delay_ms")),
            "final_empty_tip": deepcopy(settings.get("final_empty_tip"))}


def starter_entries() -> list[dict]:
    catalog = load_source_catalog()
    entries = []
    variants = [(c, v, 8 if group == "single_dispense_variants" else 9)
                for c in catalog["classes"]
                for group in ("single_dispense_variants", "multi_dispense_variants")
                for v in c[group]]
    variants += [(catalog["classes"][0], v, 6)
                 for v in catalog["supplementary_water_performance_points"]]
    for family, variant, page in variants:
        tip = variant.get("tip", {})
        capacity = tip.get("nominal_capacity_ul", variant.get("tip_size_without_filter_ul"))
        context = {"generation": "original ADP", "tip_profile_id": f"tecan-liha-T{capacity}",
                   "filter_type": tip.get("filter_type", variant.get("tip_filter_type")),
                   "mode": variant["mode"], "recipe_context": f"399156-V1.0-p{page}"}
        if page == 9:
            context.update(aliquot_volume_ul=variant["dispense_volume_ul"],
                           sample_count=variant["number_of_dispenses"])
        else:
            context["target_volume_ul"] = variant["target_volume_ul"]
        settings = {k: deepcopy(v) for k, v in variant.items() if k in FIELDS}
        settings.update(deepcopy(variant.get("unreported_parameters", {})))
        entries.append({"schema": "bms.bioxp-liquid-class.v1", "id": variant["id"],
                        "revision": family["revision"], "family_id": family["id"],
                        "label": family["label"], "context": context, "settings": settings,
                        "source": deepcopy(variant), "provenance_kind": "manufacturer",
                        "qualification": "not BioXP-qualified"})
    return entries


def _matches(entry: dict | None, context: dict) -> bool:
    if not entry or not isinstance(entry.get("context"), dict):
        return False
    required = {"generation", "tip_profile_id", "filter_type", "mode", "recipe_context"}
    required |= ({"aliquot_volume_ul", "sample_count"} if context.get("mode") == "multi-dispense"
                 else {"target_volume_ul"})
    declared = entry["context"]
    def same(key, value):
        if key not in context:
            return False
        if key in {'target_volume_ul', 'aliquot_volume_ul', 'sample_count'}:
            if isinstance(value, bool) or isinstance(context[key], bool):
                return False
            try:
                left, right = Decimal(str(value)), Decimal(str(context[key]))
                return left.is_finite() and right.is_finite() and left == right
            except (InvalidOperation, ValueError):
                return False
        return context[key] == value
    return required <= declared.keys() and all(same(k, v) for k, v in declared.items() if k != "composition")


def resolve_liquid_settings(requested: dict, *, liquid_class: dict | None = None,
                            water: dict | None = None, context: dict) -> dict:
    """Omission inherits; explicit null/blank/invalid never does. Findings are advisory.

    context.applicable_fields optionally narrows the scientific field inventory. Without
    it only fields present in the selected class/Water/request are considered applicable.
    Source nulls remain unresolved evidence, not explicit user choices or numeric zero.
    """
    requested = deepcopy(requested)
    issues, records, substitutions, resolved = [], {}, [], {}
    class_ok, water_ok = _matches(liquid_class, context), _matches(water, context)
    for kind, entry, matches in (("class", liquid_class, class_ok), ("water", water, water_ok)):
        if entry and not matches:
            issues.append({"code": "liquid_context_mismatch", "category": "advisory",
                           "path": f"/{kind}", "message": "Exact recipe context does not match; no interpolation performed."})
    cls = liquid_class.get("settings", {}) if class_ok and liquid_class else {}
    wat = water.get("settings", {}) if water_ok and water else {}
    applicable = set(context.get("applicable_fields", set(cls) | set(wat) | set(requested)))
    for field in sorted(set(requested) | applicable):
        present = field in requested
        source, ref, found, value = "unspecified", None, False, None
        if present:
            source, found, value = "requested", True, requested[field]
        elif field in applicable and field in cls and (cls[field] is not None or
                (liquid_class or {}).get("provenance_kind") != "manufacturer"):
            source, ref, found, value = "liquid_class", liquid_class, True, cls[field]
        elif field in applicable and field in wat and wat[field] is not None:
            source, ref, found, value = "water", water, True, wat[field]
        if found:
            resolved[field] = deepcopy(value)
        reference = ({"id": ref.get("id"), "revision": ref.get("revision"),
                      "provenance_kind": ref.get("provenance_kind", "authored")}
                     if ref else None)
        record = {"requested": {"present": present},
                  "resolved": {"present": found, "source": source, "reference": reference},
                  "emitted": {"status": "not_emitted"}, "applied": {"status": "unknown"}}
        if present:
            record["requested"]["value"] = deepcopy(requested[field])
        if found:
            record["resolved"]["value"] = deepcopy(value)
        if field in cls:
            record["class_value"] = deepcopy(cls[field])
        if source == "water":
            substitutions.append({"field": field, "requested_present": False,
                                  "value": deepcopy(value), "water": reference,
                                  "context": deepcopy((water or {})["context"])})
        if not found:
            issues.append({"code": "liquid_value_unknown", "category": "advisory",
                           "path": "/" + field, "message": "No defined value in matching class or Water."})
        elif field.endswith(("_ul", "_ul_s", "_ms", "_mm", "_mm_s")):
            try:
                number = Decimal(str(value))
                valid = not isinstance(value, bool) and number.is_finite() and number >= 0
            except (InvalidOperation, ValueError):
                valid = False
            if not valid:
                issues.append({"code": "explicit_liquid_value_invalid", "category": "advisory",
                               "path": "/" + field, "message": "Explicit value retained without Water replacement."})
        records[field] = record
    return {"requested": requested, "resolved": resolved, "fields": records,
            "water_substitutions": substitutions, "issues": issues}


def record_liquid_application(resolution: dict, *, emitted: dict | None = None,
                              applied: dict | None = None) -> dict:
    """Attach actual emitter/outcome records keyed by field; never infer application."""
    result = deepcopy(resolution)
    for phase, values in (("emitted", emitted or {}), ("applied", applied or {})):
        for field, value in values.items():
            result["fields"].setdefault(field, {"requested": {"present": False},
                                               "resolved": {"present": False},
                                               "emitted": {"status": "not_emitted"},
                                               "applied": {"status": "unknown"}})[phase] = deepcopy(value)
    return result
