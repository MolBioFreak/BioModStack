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
