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
_ACTIVE_WRITE = None
_SOURCE_FIELDS = ("bms_source_chain", "bms_source_residue", "bms_source_icode",
                  "bms_source_aa", "bms_source_index")


def optional_export(function):
    """Missing optional correspondence must not change native execution."""
    import functools
    @functools.wraps(function)
    def observe(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            import warnings
            warnings.warn(f"PPIFlow target metadata unavailable at {function.__name__}: {exc}", RuntimeWarning)
            return None
    return observe


@optional_export
def capture_chain(features, chain):
    """Keep insertion codes at the actual native parser's one-residue/row boundary."""
    import numpy as np
    features["bms_source_icode"] = np.array([ord(res.id[2]) for res in chain])
    features["bms_source_chain"] = np.full(len(features["aatype"]), ord(chain.id))


@optional_export
def capture_features(features, native_utils):
    """Observe already-loaded native features; never unpickle in the API."""
    import numpy as np
    features.setdefault("bms_source_chain", np.array([
        ord(native_utils.INT_TO_CHAIN[int(c)]) for c in features["chain_index"]]))
    features["bms_source_residue"] = features["residue_index"].copy()
    features.setdefault("bms_source_icode", np.full(len(features["aatype"]), -1))
    features["bms_source_aa"] = np.array([
        ord((native_utils.residue_constants.restypes + ["X"])[int(a)])
        for a in features["aatype"]])
    features["bms_source_index"] = np.arange(len(features["aatype"]))


@optional_export
def retain_feature_indices(output, features, torch):
    for key in _SOURCE_FIELDS:
        if key in features:
            output[key] = torch.tensor(features[key])


@optional_export
def capture_target(values, antibody=False):
    """Export the native selected target array, before generation or writer renumbering."""
    target = values["target_feats"]
    if not all(key in target for key in _SOURCE_FIELDS):
        return
    arrays = {key: _json_value(target[key]) for key in _SOURCE_FIELDS}
    indices = _json_value(values["target_index"])
    residues = []
    for index, feature_index in enumerate(arrays["bms_source_index"]):
        icode = arrays["bms_source_icode"][index]
        residues.append({
            "input_index": int(indices[index]) if antibody else index,
            "feature_index": int(feature_index),
            "chain_id": chr(arrays["bms_source_chain"][index]),
            "auth_seq_id": int(arrays["bms_source_residue"][index]),
            "insertion_code": chr(icode).strip() if icode >= 0 else None,
            "amino_acid": chr(arrays["bms_source_aa"][index]),
        })
    # A JSON string survives native DataLoader collation without variable-length
    # metadata being transposed or passed as an additional model tensor.
    values["output_feats"]["bms_target_residues"] = json.dumps(residues)


@optional_export
def record_writer_indices(indices):
    if _ACTIVE_WRITE is not None:
        _ACTIVE_WRITE["indices"] = _json_value(indices)


@optional_export
def record_writer_atom(index, chain, number, insertion):
    if _ACTIVE_WRITE is not None and "indices" in _ACTIVE_WRITE:
        source_index = int(_ACTIVE_WRITE["indices"][index])
        _ACTIVE_WRITE["residues"][source_index] = {
            "chain_id": str(chain), "auth_seq_id": int(number),
            "insertion_code": str(insertion).strip()}


@optional_export
def record_target_write(path, batch, index, observation):
    if _CONTEXT is not None and "bms_target_residues" in batch:
        _CONTEXT.setdefault("target_writes", {})[str(Path(path).resolve())] = {
            "source": json.loads(batch["bms_target_residues"][index]),
            "output": observation["residues"],
        }


def write_sample(writer, batch, index, *args, **kwargs):
    """Join the producer batch to actual atom emission, including native swaps."""
    global _ACTIVE_WRITE
    previous = _ACTIVE_WRITE
    observation = {"residues": {}}
    _ACTIVE_WRITE = observation
    try:
        path = writer(*args, **kwargs)
        record_target_write(path, batch, index, observation)
        return path
    finally:
        _ACTIVE_WRITE = previous


@optional_export
def target_metadata(context, source_row, path):
    observation = context.get("target_writes", {}).pop(str(path), None)
    if observation is None:
        return {}
    residues = observation["source"]
    bindings = {row["role"]: row for row in context["request"]["source_bindings"]}
    result = {}
    # Initial native outputs contain the target plus generated binder. The
    # captured pre-sampling target indices, composed with actual atom emission,
    # establish output roles without guessing chain labels or amino acids.
    target_indices = {row['input_index'] for row in residues}
    output_targets = list(dict.fromkeys(row['chain_id'] for index, row in observation['output'].items()
                                       if index in target_indices))
    output_binders = list(dict.fromkeys(row['chain_id'] for index, row in observation['output'].items()
                                       if index not in target_indices))
    if output_binders and not set(output_binders).intersection(output_targets):
        result.update(binder_chains=output_binders, target_chains=output_targets)
    if "target_pdb" in bindings:
        source_hash = bindings["target_pdb"]["sha256"]
        sources = [({k: r[k] for k in ("chain_id", "auth_seq_id", "insertion_code")}
                    if r["insertion_code"] is not None else None) for r in residues]
    else:
        # CSV features have native sequence/chain arrays but no insertion-code
        # authority. Export sequence evidence, not invented author selectors or
        # a generated target pose. Structural comparison remains unmeasured.
        chains = {}
        for residue in residues:
            chain = chains.setdefault(residue["chain_id"], {
                "chain_id": residue["chain_id"], "sequence": "", "native_residues": []})
            chain["sequence"] += residue["amino_acid"]
            chain["native_residues"].append({k: residue[k] for k in (
                "feature_index", "auth_seq_id", "insertion_code")})
        binding = bindings.get(f"csv_row:{source_row}")
        if binding is None:
            return {}
        document = {"schema_version": "ppiflow-independent-target/1",
                    "source_feature_sha256": binding["sha256"],
                    "chains": list(chains.values())}
        raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        source_hash = hashlib.sha256(raw).hexdigest()
        result["independent_target"] = {"sha256": source_hash, "document": document,
                                        "encoding": "canonical-json-sorted-compact-newline"}
        return result
    joins = [{"source": source, "output": observation["output"][r["input_index"]]}
             for r, source in zip(residues, sources)
             if source is not None and r["input_index"] in observation["output"]]
    if joins:
        result["target_residue_mapping"] = {"source_sha256": source_hash, "residues": joins}
    return result


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
    record.update(target_metadata(ctx, identity["source_row_index"], path) or {})
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
    if kind in {"entrypoint", "preprocessing", "dataset", "antibody_dataset", "writer", "pdb_writer", "producer"}:
        class TargetExport(ast.NodeTransformer):
            def __init__(self):
                self.function = None
                self.cls = None

            def visit_ClassDef(self, node):
                previous, self.cls = self.cls, node.name
                self.generic_visit(node)
                self.cls = previous
                return node

            def visit_FunctionDef(self, node):
                previous, self.function = self.function, node.name
                self.generic_visit(node)
                self.function = previous
                return node

            def visit_Assign(self, node):
                self.generic_visit(node)
                text = ast.unparse(node.value)
                extra = None
                if kind in {"entrypoint", "preprocessing"} and text == "dataclasses.asdict(chain_prot)":
                    extra = "if '_bms_export' in globals():\n    _bms_export.capture_chain(chain_dict, chain)"
                elif kind in {"dataset", "antibody_dataset"}:
                    if text == "du.read_pkl(processed_file_path)":
                        extra = "_bms_export.capture_features(processed_feats, du)"
                    elif (isinstance(node.value, ast.Dict)
                          and any(isinstance(t, ast.Name) and t.id == "output_feats" for t in node.targets)
                          and self.function in {"_process_csv_row", "process_csv_row"}):
                        extra = "_bms_export.retain_feature_indices(output_feats, processed_feats, torch)"
                elif kind == "writer" and self.function == "create_full_prot":
                    if text == "atom37.shape[0]":
                        extra = "bms_input_indices = np.arange(n)"
                    elif (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                          and node.value.func.id == "swap_index"):
                        node.targets[0].elts.append(ast.Name(id="bms_input_indices", ctx=ast.Store()))
                        node.value.args[1].elts.append(ast.Name(id="bms_input_indices", ctx=ast.Load()))
                return [node, *ast.parse(extra).body] if extra else node

            def visit_Return(self, node):
                self.generic_visit(node)
                extra = None
                if kind == "writer" and self.function == "create_full_prot":
                    extra = "_bms_export.record_writer_indices(bms_input_indices)"
                elif (kind == "dataset" and self.cls == "PpiTestDataset" and self.function == "__getitem__"):
                    extra = "_bms_export.capture_target(locals())"
                elif (kind == "antibody_dataset" and self.cls == "AntibodyTestDataset" and self.function == "__getitem__"):
                    extra = "_bms_export.capture_target(locals(), antibody=True)"
                return [*ast.parse(extra).body, node] if extra else node

            def visit_Expr(self, node):
                self.generic_visit(node)
                if kind == "pdb_writer" and ast.unparse(node.value) == "pdb_lines.append(atom_line)":
                    return [node, *ast.parse(
                        "_bms_export.record_writer_atom(i, chain_ids[chain_index[i]], residue_index[i], insertion_code)"
                    ).body]
                return node

            def visit_Call(self, node):
                self.generic_visit(node)
                if kind == "producer" and ast.unparse(node.func) == "au.write_prot_to_pdb":
                    node.args = [node.func, ast.Name(id="batch", ctx=ast.Load()),
                                 ast.Name(id="i", ctx=ast.Load()), *node.args]
                    node.func = ast.parse("_bms_export.write_sample", mode="eval").body
                return node
        tree = TargetExport().visit(tree)
    return compile(ast.fix_missing_locations(tree), filename, "exec")


class NativeLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, export_module):
        self.export_module = export_module
        self.files = {}

    def find_spec(self, fullname, path=None, target=None):
        kinds = {"models.flow_module_binder": "producer", "models.flow_module_antibody": "producer",
                 "preprocessing.process_pdb_for_inputs": "preprocessing",
                 "data.datasets": "dataset", "data.datasets_antibody": "antibody_dataset",
                 "analysis.utils": "writer", "data.protein": "pdb_writer"}
        if fullname not in kinds:
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
        kinds = {"preprocessing.process_pdb_for_inputs": "preprocessing",
                 "data.datasets": "dataset", "data.datasets_antibody": "antibody_dataset",
                 "analysis.utils": "writer", "data.protein": "pdb_writer"}
        kind = kinds.get(module.__name__, "producer")
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
                "data/interpolant_binder.py" if mode == "protein_binder" else "data/interpolant_antibody.py",
                "data/parsers.py", "data/protein.py", "data/utils.py", "analysis/utils.py"]
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
