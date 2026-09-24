#!/usr/bin/env python3
"""Invoke pinned native INITIAL scripts, with export-only producer instrumentation.

Importing this adapter does not import Torch, model code, or the native sampler.
The only preprocessing compatibility repair moves the existing target-interface
assignment before its first read in the native virtual-binder helper.
"""
from __future__ import annotations

import argparse
import ast
import copy
import csv
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys


def request_authority():
    path = Path(__file__).resolve().parents[1] / "platform/api/services/ppiflow_generation.py"
    spec = importlib.util.spec_from_file_location("bms_ppiflow_generation_request", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONTEXT = None


def _json_value(value):
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    if hasattr(value, "item"):
        return _json_value(value.item())
    if isinstance(value, float):
        import math
        if not math.isfinite(value):
            return {"native_nonfinite": str(value)}
    return value


def record_preprocessed(metadata, sample_id):
    """Join native antibody preprocessing sample_id to its explicit dataset key."""
    if _CONTEXT is not None:
        _CONTEXT["preprocessed"][metadata["pdb_name"]] = {
            "sample_index": int(sample_id), "native_input_id": metadata["id"],
            "source_row_index": 0,
        }


def record_config(config):
    if _CONTEXT is not None:
        _CONTEXT["receipt"]["native_effective_config"] = copy.deepcopy(config)
        _save_receipt()


def record_sample(values):
    """Called exactly at native metric-row append; no filename/rank inference."""
    if _CONTEXT is None:
        return
    ctx = _CONTEXT
    mode = ctx["request"]["mode"]
    batch, i = values["batch"], values["i"]
    target_name = str(batch["pdb_name"][i])
    if mode == "protein_binder":
        row = int(_json_value(batch["original_index"][i]))
        sample = int(_json_value(values["sample_ids"][i]))
        identity = {"source_row_index": row, "sample_index": sample,
                    "native_input_id": target_name}
        path = Path(values["saved_path"]).resolve()
        attempt = None
    else:
        identity = ctx["preprocessed"][target_name].copy()
        path = Path(values["pdb_path"]).resolve()
        attempt = int(values["attempt"])
    source = ctx["request"]["source_rows"][identity["source_row_index"]]
    native_relative = path.relative_to(ctx["output"]).as_posix()
    # Snapshot at the producer event: duplicate native target names may overwrite
    # a native filename later. Never reconstruct an earlier sample from that file.
    relative = f"candidates/source-{identity['source_row_index']}/sample-{identity['sample_index']}.pdb"
    candidate = ctx["output"] / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    data = path.read_bytes()
    candidate.write_bytes(data)
    # Logical producer identity, independent of native filenames and ordering.
    key = f"source:{identity['source_row_index']}/sample:{identity['sample_index']}"
    record = {"schema_version": ctx["request"]["schema_version"],
              "producer": "ppiflow", "operation": "initial_generation", "mode": mode,
              "candidate_key": key, **identity, "native_target_name": target_name,
              "native_attempt_index": attempt, "path": relative, "native_path": native_relative,
              "sha256": hashlib.sha256(data).hexdigest(),
              "source": source, "source_identity": ctx["request"]["source_identity"],
              "metrics": _json_value(values["test_metric"])}
    # Retain the native CSV too; these are the exact row values before its rounding.
    with (ctx["output"] / "samples.jsonl").open("a") as handle:
        handle.write(json.dumps(record, allow_nan=False) + "\n")
    Path(str(candidate) + ".sample.json").write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    ctx["receipt"]["emitted_samples"] += 1


def _save_receipt():
    output = _CONTEXT["output"] / "generation_receipt.json"
    tmp = output.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_CONTEXT["receipt"], indent=2, allow_nan=False) + "\n")
    tmp.replace(output)


def instrument(source: str, filename: str, kind: str):
    """Instrument actual producer/settings/preparation locations, not glob results."""
    tree = ast.parse(source, filename)
    if kind == "entrypoint":
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "save_config":
                node.body.extend(ast.parse("_bms_export.record_config(self.current_config)").body)
            if isinstance(node, ast.FunctionDef) and node.name == "process_file":
                for index, statement in enumerate(node.body):
                    if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Name) and statement.value.id == "metadata":
                        node.body[index:index] = ast.parse("_bms_export.record_preprocessed(metadata, sample_id)").body
                        break
    elif kind == "preprocessing":
        # Native process_file reads target_interface_residues inside
        # generate_virtual_binder_feats before assigning it later in this function.
        # Move that identical metadata assignment to the first-use boundary.
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "process_file":
                for index, statement in enumerate(node.body):
                    if (isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
                            and isinstance(statement.value.func, ast.Name)
                            and statement.value.func.id == "generate_virtual_binder_feats"):
                        node.body[index:index] = ast.parse("complex_feats['target_interface_residues'] = row['chain1_residues']").body
                        break
    else:
        class Export(ast.NodeTransformer):
            def visit_Expr(self, node):
                self.generic_visit(node)
                call = node.value
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and ast.unparse(call.func) == "self.test_epoch_metrics.append"):
                    return [node, *ast.parse("_bms_export.record_sample(locals())").body]
                return node
        tree = Export().visit(tree)
    return compile(ast.fix_missing_locations(tree), filename, "exec")


class NativeLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, export_module):
        self.export_module = export_module
        self.files = {}

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in ("models.flow_module_binder", "models.flow_module_antibody",
                            "preprocessing.process_pdb_for_inputs"):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None:
            self.files[fullname] = spec.origin
            spec.loader = self
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        filename = self.files[module.__name__]
        kind = "preprocessing" if module.__name__.startswith("preprocessing.") else "producer"
        module.__dict__["_bms_export"] = self.export_module
        exec(instrument(Path(filename).read_text(), filename, kind), module.__dict__)


def prepare_invocation(request_dir: str | Path, output_dir: str | Path,
                       native_root: str | Path = "/app/ppiflow",
                       checkpoint_root: str | Path = "/opt/ppiflow/ckpt") -> tuple[dict, list[str]]:
    """Prepare actual native argv/config without importing or invoking science."""
    import yaml
    authority = request_authority()
    request_root, output, native_root = Path(request_dir).resolve(), Path(output_dir).resolve(), Path(native_root).resolve()
    payload = json.loads((request_root / "request.json").read_text())
    mode = payload["mode"]
    authority.normalize_ppiflow_generation_params(mode, payload["effective_settings"])
    transport = payload["transport_settings"].copy()
    output.mkdir(parents=True, exist_ok=True)
    for field in ("target_pdb", "framework_pdb", "input_csv"):
        if transport.get(field):
            transport[field] = str((request_root / transport[field]).resolve())
    if transport.get("input_csv"):
        with Path(transport["input_csv"]).open(newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames, rows = reader.fieldnames, list(reader)
        for row in rows:
            row["processed_path"] = str((request_root / row["processed_path"]).resolve())
        materialized = output / "native_input.csv"
        with materialized.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        transport["input_csv"] = str(materialized)
    script, config_name, checkpoint = authority.MODES[mode]
    config_path = native_root / config_name
    config = yaml.safe_load(config_path.read_text())
    argv = [str(native_root / script)]
    for field in authority.parameter_contract(mode):
        name = field["name"]
        value = transport.get(name)
        if value is None:
            continue
        if field.get("native_path"):
            *parents, leaf = field["native_path"].split(".")
            node = config
            for part in parents:
                node = node.setdefault(part, {})
            node[leaf] = value
        if field.get("native_cli"):
            flag = field["native_cli"]
            if isinstance(flag, dict):
                flag = flag[mode]
            argv.extend(["--" + flag, str(value)])
    compiled = output / "bms_native_config.yaml"
    compiled.write_text(yaml.safe_dump(config, sort_keys=False))
    argv.extend(["--config", str(compiled), "--model_weights", str(Path(checkpoint_root) / checkpoint),
                 "--output_dir", str(output), "--name", "bms_target"])
    relevant = [script, config_name, "experiments/inference_binder.py" if mode == "protein_binder" else "experiments/inference_antibody.py",
                "models/flow_module_binder.py" if mode == "protein_binder" else "models/flow_module_antibody.py",
                "data/datasets.py" if mode == "protein_binder" else "data/datasets_antibody.py",
                "data/interpolant_binder.py" if mode == "protein_binder" else "data/interpolant_antibody.py"]
    if mode == "protein_binder":
        relevant.append("preprocessing/process_pdb_for_inputs.py")
    source = {}
    for name in relevant:
        try:
            source[name] = hashlib.sha256((native_root / name).read_bytes()).hexdigest()
        except OSError:
            # Source inspection is observational, not a new startup proof gate.
            source[name] = None
    try:
        head = native_root / ".git/HEAD"
        observed_ref = head.read_text().strip()
        if observed_ref.startswith("ref: "):
            ref = native_root / ".git" / observed_ref.removeprefix("ref: ")
            observed_ref = ref.read_text().strip()
    except OSError:
        observed_ref = None
    receipt = {"schema_version": authority.SCHEMA_VERSION, "model": "ppiflow", "mode": mode,
               "operation": "initial_generation", "requested_settings": payload["requested_settings"],
               "effective_settings": payload["effective_settings"], "source_bindings": payload["source_bindings"],
               "source_identity": payload["source_identity"], "runtime": payload["runtime"],
               "native_source": {"expected_revision": authority.SOURCE_REVISION,
                                 "observed_ref": observed_ref,
                                 "sha256": source},
               "native_argv": argv, "native_effective_config": None,
               "adapter_repairs": ["target_interface_metadata_before_virtual_binder"] if mode == "protein_binder" else [],
               "requested_samples": transport["samples_per_target"] * len(payload["source_rows"]),
               "emitted_samples": 0, "status": "prepared"}
    return {"request": payload, "receipt": receipt, "output": output, "preprocessed": {}}, argv


def run(request_dir, output_dir, native_root="/app/ppiflow", checkpoint_root="/opt/ppiflow/ckpt"):
    global _CONTEXT
    _CONTEXT, argv = prepare_invocation(request_dir, output_dir, native_root, checkpoint_root)
    _CONTEXT["receipt"]["status"] = "running"
    _save_receipt()
    # This process is a single native invocation; restore hooks for unit callers.
    old_argv, old_path = sys.argv, sys.path[:]
    loader = NativeLoader(sys.modules[__name__])
    sys.meta_path.insert(0, loader)
    sys.path.insert(0, str(Path(native_root).resolve()))
    sys.argv = argv
    try:
        namespace = {"__name__": "__main__", "__file__": argv[0], "_bms_export": sys.modules[__name__]}
        exec(instrument(Path(argv[0]).read_text(), argv[0], "entrypoint"), namespace)
        _CONTEXT["receipt"]["status"] = "completed"
    except BaseException:
        _CONTEXT["receipt"]["status"] = "failed"
        raise
    finally:
        _CONTEXT["receipt"]["unemitted_samples"] = (
            _CONTEXT["receipt"]["requested_samples"] - _CONTEXT["receipt"]["emitted_samples"])
        _save_receipt()
        sys.meta_path.remove(loader)
        sys.argv, sys.path = old_argv, old_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    # Runtime-owned paths, not scientific operator controls.
    parser.add_argument("--native-root", default="/app/ppiflow")
    parser.add_argument("--checkpoint-root", default="/opt/ppiflow/ckpt")
    args = parser.parse_args()
    run(args.request, args.output, args.native_root, args.checkpoint_root)


if __name__ == "__main__":
    main()
