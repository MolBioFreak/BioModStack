"""Read immutable facade or compiler snapshots without recompiling old jobs."""
from copy import deepcopy
import json


def method_snapshot(document):
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return None
    snapshot = metadata.get("bms_method_run")
    if isinstance(snapshot, dict) and snapshot.get("schema") == "bms.bioxp-method-run.v1":
        return deepcopy(snapshot) if isinstance(snapshot.get("method"), dict) else None
    embedded = metadata.get("bms_method")
    if not isinstance(embedded, dict) or not isinstance(embedded.get("method"), dict):
        return None
    if embedded["method"].get("schema") != "bms.bioxp-method.v1":
        return None
    # Compiler-authored exports predate the facade envelope. Their action
    # metadata is the immutable mapping, not today's compiler or result cursor.
    provenance = {}
    for stage in document.get("stages", []):
        for action in stage.get("actions", []):
            action_metadata = action.get("metadata")
            source = action_metadata.get("bms_method") if isinstance(action_metadata, dict) else None
            if not isinstance(source, dict) or not isinstance(source.get("occurrence_id"), str):
                continue
            action_id = action.get("action_id")
            if not isinstance(action_id, str):
                continue
            key = json.dumps(source, sort_keys=True, ensure_ascii=False)
            row = provenance.setdefault(key, {**deepcopy(source), "native_action_ids": []})
            row["native_action_ids"].append(action_id)
    return {
        "schema": "bms.bioxp-compiler-snapshot.v1",
        **{key: deepcopy(embedded[key]) for key in
           ("method", "bindings", "dependencies", "initial_state") if key in embedded},
        "snapshot_evidence": {
            "source": "protocol.document.metadata.bms_method",
            "original_metadata": deepcopy(embedded),
            "complete_run_envelope": False,
            "initial_state_presence": "compiler_recorded_only",
            "limitations": ["Original submission omitted/null distinction is unavailable when the compiler recorded null.",
                            "No saved revision or full original compilation/simulation envelope was recorded."],
        },
        "compilation": {
            "document": deepcopy(document),
            "provenance": list(provenance.values()),
            **{key: deepcopy(embedded[key]) for key in
               ("digest", "compiler", "native_source_commit", "resolved_liquids", "water_substitutions") if key in embedded},
        },
    }
