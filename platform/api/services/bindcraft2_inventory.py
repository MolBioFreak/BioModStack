"""Offline inventory of the pinned BC2 settings surface; no accelerator imports.

This is evidence, NOT a complete typed parameter schema. A null in reference.json
is not a native default or a type declaration. Run against an exact pinned checkout.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

PIN = "d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root: Path) -> dict:
    root = Path(root)
    source = root / "bindcraft" / "settings.py"
    settings = root / "settings"
    reference = json.loads((settings / "core" / "reference.json").read_text())["settings"]
    defaults = json.loads((settings / "core" / "default.json").read_text())
    tree = ast.parse(source.read_text())
    literals = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "CAMPAIGN_SETTING_NAMES", "TARGET_SETTING_NAMES", "LOSS_PARAMETERS",
                    "DOMAIN_PARAMETERS", "DESIGN_STAGE_NAMES", "FINAL_CONFIDENCE_FILTERS",
                }:
                    value = node.value
                    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
                        value = value.args[0]
                    literals[target.id] = ast.literal_eval(value)
    presets = {}
    preset_keys = set()
    for tier in ("core", "modality", "property", "target"):
        presets[tier] = {}
        for path in sorted((settings / tier).glob("*.json")):
            data = json.loads(path.read_text())
            keys = sorted(set(data) - {"description"})
            preset_keys.update(keys)
            presets[tier][path.stem] = {"sha256": _sha(path), "keys": keys,
                                         "nested": {k: sorted(v) for k, v in data.items() if isinstance(v, dict)}}
    registries = {}
    for block, filename, decorator in (("losses", "loss.py", "loss"),
                                       ("filters", "filters.py", "filter_metric")):
        functions = ast.parse((root / "bindcraft" / filename).read_text())
        entries = {}
        for node in functions.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for annotation in node.decorator_list:
                if not (isinstance(annotation, ast.Call) and isinstance(annotation.func, ast.Name)
                        and annotation.func.id == decorator):
                    continue
                name = ast.literal_eval(annotation.args[0])
                params = {}
                args = node.args.args
                padded = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
                for arg, value in zip(args, padded):
                    if arg.arg in {"protein_states", "predictions"}:
                        continue
                    try:
                        default = ast.literal_eval(value) if value is not None else None
                    except (ValueError, TypeError):
                        default = None  # source expression, not an invented default
                    params[arg.arg] = {"required": value is None, "default_literal": json.loads(json.dumps(default)),
                                       "source_default": ast.unparse(value) if value is not None else None}
                entries[name] = {"function": node.name, "params": params}
        registries[block] = dict(sorted(entries.items()))
    loss_tree = ast.parse((root / "bindcraft" / "loss.py").read_text())
    paratope_choices = next(ast.literal_eval(node.value) for node in loss_tree.body
                            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "PARATOPE_CONFORMATIONS" for target in node.targets))
    stages = [stage for stage in literals["DESIGN_STAGE_NAMES"] if stage != "final"]
    generated = ({f"weights_{name}" for name in registries["losses"]}
                 | {f"{stage}_steps" for stage in stages}
                 | {f"{metric}_{stage}" for metric in ("min_plddt", "min_iptm", "max_detarget_iptm")
                    for stage in literals["DESIGN_STAGE_NAMES"]}
                 | {f"min_{terminus}_terminus_away_cosine_final" for terminus in ("n", "c")})
    statically_known = (set(literals["CAMPAIGN_SETTING_NAMES"]) | set(defaults)
                        | set(literals["FINAL_CONFIDENCE_FILTERS"])
                        | set(literals["LOSS_PARAMETERS"]) | set(literals["DOMAIN_PARAMETERS"])
                        | {"core", "modality", "target", "paratope_conformations"}
                        | set(presets["property"]) | generated)
    # Source-backed values only. A missing/null value is unresolved, never a default.
    examples = {key: [] for key in statically_known}
    for key, value in reference.items():
        if value is not None and key in examples:
            examples[key].append(value)
    for key, value in defaults.items():
        if key in examples:
            examples[key].append(value)
    for tier in ("core", "modality", "property", "target"):
        for path in sorted((settings / tier).glob("*.json")):
            for key, value in json.loads(path.read_text()).items():
                if key in examples and value is not None:
                    examples[key].append(value)
    def kind(value):
        if isinstance(value, bool): return "boolean"
        if isinstance(value, int): return "integer"
        if isinstance(value, float): return "number"
        if isinstance(value, str): return "string"
        if isinstance(value, list): return "array"
        if isinstance(value, dict): return "object"
        return "unknown"
    types = {key: sorted({kind(v) for v in values}) for key, values in examples.items()}
    for observed in types.values():
        if "integer" in observed and "number" in observed:
            observed.remove("integer")
    # Explicit source contracts, not defaults: requested_preset_names(),
    # preflight's finite campaign guard, and parameter_sweep_arms().
    for key in ("core", "modality", "target"):
        types[key] = ["array", "string"]
    types["max_trajectories"] = ["integer"]
    types["parameter_sweep"] = ["object"]
    return_fields = {key: {"native_key": key, "observed_types": types[key],
                           "native_default": defaults.get(key), "has_native_default": key in defaults,
                           "preset_examples": len(examples[key]),
                           "status": "typed" if len(types[key]) == 1 or key in {"core", "modality", "target"} else "unresolved"}
                     for key in sorted(statically_known)}
    nested = {}
    for block, entries in registries.items():
        nested[block] = {name: {"params": sorted(entry["params"]),
                                "entry_keys": ["params", "prediction_state"] +
                                              (["threshold", "higher", "mandatory"] if block == "filters" else [])}
                         for name, entry in entries.items()}
    nested["targets"] = {"fields": sorted(literals["TARGET_SETTING_NAMES"])}
    nested["parameter_sweep"] = {"fields": ["axes", "levels", "max_arms", "block_trajectories", "multiplier"]}
    return {
        "upstream_commit": PIN,
        "source_sha256": _sha(source),
        "registry_sha256": {name: _sha(root / "bindcraft" / name) for name in ("loss.py", "filters.py", "parameter_sweep.py")},
        "reference_sha256": _sha(settings / "core" / "reference.json"),
        "default_sha256": _sha(settings / "core" / "default.json"),
        "reference_fields": sorted(reference),
        "reference_only_not_statically_known": sorted(set(reference) - statically_known),
        "statically_known_not_in_reference": sorted(statically_known - set(reference)),
        "preset_only_not_in_reference": sorted(preset_keys - set(reference)),
        "default_values": defaults,
        "target_fields": sorted(literals["TARGET_SETTING_NAMES"]),
        "paratope_conformations": list(paratope_choices),
        "presets": presets,
        "registered_metrics": registries,
        "nested_surfaces": nested,
        "fields": return_fields,
        "unresolved_fields": sorted(key for key, field in return_fields.items() if field["status"] != "typed"),
        "coverage_status": "INCOMPLETE: source observations are not validated types/defaults; UI/API/runtime acceptance pending",
    }


def main() -> None:
    import argparse
    import subprocess
    parser = argparse.ArgumentParser()
    parser.add_argument("upstream", type=Path)
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True).strip()
    if commit != PIN:
        parser.error(f"expected pinned upstream {PIN}, got {commit}")
    print(json.dumps(inventory(args.upstream), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
