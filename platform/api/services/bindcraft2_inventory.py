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
    # This is only the statically enumerable part of known_campaign_settings().
    # Registered loss/metric names and runtime-generated feature keys need separate audit.
    statically_known = (set(literals["CAMPAIGN_SETTING_NAMES"]) | set(defaults)
                        | set(literals["FINAL_CONFIDENCE_FILTERS"])
                        | set(literals["LOSS_PARAMETERS"]) | set(literals["DOMAIN_PARAMETERS"])
                        | {"core", "modality", "target", "paratope_conformations"}
                        | set(presets["property"]))
    return {
        "upstream_commit": PIN,
        "source_sha256": _sha(source),
        "reference_sha256": _sha(settings / "core" / "reference.json"),
        "default_sha256": _sha(settings / "core" / "default.json"),
        "reference_fields": sorted(reference),
        "reference_only_not_statically_known": sorted(set(reference) - statically_known),
        "statically_known_not_in_reference": sorted(statically_known - set(reference)),
        "preset_only_not_in_reference": sorted(preset_keys - set(reference)),
        "default_values": defaults,
        "target_fields": sorted(literals["TARGET_SETTING_NAMES"]),
        "presets": presets,
        "coverage_status": "INCOMPLETE: registries, generated keys, nested parameters, types, UI/API and native differential tests pending",
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
