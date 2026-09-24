#!/usr/bin/env python3
"""Prepare a pinned BC2 compilation for native design; --execute explicitly runs it.

This is the model-owned leaf for campaign, resume and native postprocessing.
It does not install weights, claim a GPU, parse results, or enable the model.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

# Load only the model-owned leaf modules: services/__init__.py eagerly imports
# unrelated BMS API services unavailable inside the native image.
service_dir = Path(__file__).resolve().parents[1] / "platform/api/services"
package = types.ModuleType("services")
package.__path__ = [str(service_dir)]
sys.modules["services"] = package
for name in ("bindcraft2_native", "bindcraft2_runtime"):
    spec = importlib.util.spec_from_file_location(f"services.{name}", service_dir / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load BC2 adapter module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
from services.bindcraft2_native import PIN
from services.bindcraft2_runtime import prepare_campaign, run_campaign, materialize_runtime_compilation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compilation", type=Path, help="typed compiler's compilation.json")
    parser.add_argument("destination", type=Path, help="job-owned preparation directory")
    parser.add_argument("--native-source", required=True, type=Path, help="exact pinned upstream checkout")
    parser.add_argument("--execute", action="store_true", help="invoke native GPU campaign after CPU preparation")
    args = parser.parse_args(argv)
    # Native env overrides outrank the compiled settings; only the scheduler's
    # CUDA_VISIBLE_DEVICES placement is permitted outside the typed request.
    overrides = ("BINDCRAFT_BINDER_LENGTHS", "BINDCRAFT_WORKERS_PER_GPU",
                 "BINDCRAFT_MAX_WORKERS_PER_GPU", "BINDCRAFT_DESIGN_WORKERS",
                 "BINDCRAFT_WORKER_LAUNCH_STAGGER", "BINDCRAFT_GPU_IDS",
                 "BINDCRAFT_MPNN_WEIGHTS")
    conflicting = [name for name in overrides if os.environ.get(name)]
    if conflicting:
        parser.error("unbound native overrides: " + ", ".join(conflicting))
    source = args.native_source.resolve()
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != PIN:
        parser.error(f"BC2 native checkout must be {PIN}, got {revision}")
    if not (source / "bindcraft/settings.py").is_file():
        parser.error("BC2 source does not contain bindcraft/settings.py")
    sys.path.insert(0, str(source))
    from bindcraft.parameter_sweep import parameter_sweep_arms
    from bindcraft.preflight import cleaned_campaign_settings
    from bindcraft.settings import load_settings, read_settings

    compiled = json.loads(args.compilation.read_text())
    compiled = materialize_runtime_compilation(compiled, args.compilation.resolve().parent, args.destination)
    prepared = prepare_campaign(compiled, args.destination, load_settings, parameter_sweep_arms,
                                lambda path: cleaned_campaign_settings(read_settings(path)))
    print(json.dumps(prepared, sort_keys=True), flush=True)
    if args.execute:
        return run_campaign(prepared)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
