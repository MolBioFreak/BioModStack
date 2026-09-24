"""Initial-generation request and producer transport tests; no model imports/GPU.

Set BMS_TEST_PPIFLOW_SOURCE to a stdlib-exported pinned source tree to exercise
native argparse/configuration and source-binding differentials.
"""
import argparse
import ast
import copy
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import types
import typing

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


api = load("ppiflow_generation_authority_test", ROOT / "platform/api/services/ppiflow_generation.py")
runner = load("ppiflow_generation_runner_test", ROOT / "scripts/run_ppiflow_generation.py")
MODES = tuple(api.MODES)


def request(tmp_path, mode="protein_binder"):
    # Byte-only fixtures: never represented as native scientific execution.
    target = tmp_path / "target" / "same.pdb"
    framework = tmp_path / "framework" / "same.pdb"
    target.parent.mkdir(exist_ok=True)
    framework.parent.mkdir(exist_ok=True)
    target.write_text("REMARK transport target\nEND\n")
    framework.write_text("REMARK transport framework\nEND\n")
    params = {"target_pdb": str(target), "specified_hotspots": "R2", "samples_per_target": 2}
    if mode == "protein_binder":
        params.update(target_chain="R", binder_chain="B")
    else:
        params.update(antigen_chain="R", heavy_chain="H", framework_pdb=str(framework))
        if mode == "antibody_binder":
            params["light_chain"] = "L"
    return params


@pytest.fixture
def native():
    value = os.environ.get("BMS_TEST_PPIFLOW_SOURCE")
    if not value:
        pytest.skip("Pinned native source not exported: set BMS_TEST_PPIFLOW_SOURCE (no model imports)")
    path = Path(value)
    assert path.is_dir(), "Explicit native-source override must exist"
    return path


@pytest.mark.parametrize("member,expected", [
    ("sample_binder.py", "cfb0ad829d45ea6f34580eaaacab7828989a3cdd7a7764cf12d0b0611eb634ff"),
    ("sample_antibody_nanobody.py", "1447bfabc32adbe39ceb7a63ddd1e010fa98f4c91cbe482a7f2d4d2e826fdec6"),
    ("configs/inference_binder.yaml", "9adf525016c9f139776dd7ad7862c90da2b5cd66b05232893b38be2bf3b12499"),
    ("configs/inference_nanobody.yaml", "e6867a3a8807d56be1545500ac82e766686da10e105b6051e3ec239e6b2fb031"),
    ("models/flow_module_binder.py", "da8445cbf8e39cce1c7c28258b97e58a7caa491c229834ef8cf412a9333842eb"),
    ("models/flow_module_antibody.py", "9d58d0a91c2e4bcd781c4728422c62eba3dca9ad79195e3904f6beba9a388752"),
    ("data/interpolant_binder.py", "dbc1bb0f29960eff7149df6187343140476bf272f123a78d9e99e5433d7b1742"),
    ("data/interpolant_antibody.py", "54b4a9b0b9ca118b1d72e8d41fee0a6582417ab8eeeaf75d37c36726333a34a5"),
])
def test_source_differentials_are_bound_to_inspected_native_bytes(native, member, expected):
    import hashlib
    assert hashlib.sha256((native / member).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("mode", MODES)
def test_defaults_assets_and_mode_separation(tmp_path, mode):
    params = request(tmp_path, mode)
    normalized = api.normalize_ppiflow_generation_params(mode, params)
    assert normalized["samples_per_target"] == 2
    assert normalized["num_timesteps"] == 100
    inventory = api.ppiflow_generation_inventory(mode)
    assets = inventory["assets"]
    assert assets["weights"] == [f"ppiflow/{api.MODES[mode][2]}"]
    assert "partial" not in assets["entrypoint"]
    assert inventory["profile"]["checkpoint"] == api.MODES[mode][2]
    assert inventory["profile"]["fixed_model_architecture"]["node_embed_size"] == 256
    assert "start_t" not in normalized
    assert "seed_structures" not in normalized


@pytest.mark.parametrize("mode", MODES)
def test_materialize_preserves_duplicate_basename_sources_and_readback(tmp_path, mode):
    params = request(tmp_path, mode)
    params.update(self_condition=False, rotation_exp_rate=0)
    if mode == "protein_binder":
        params["translation_corrupt"] = False
    source = {"target": {"artifact_id": "target-artifact", "target_state": "state-A"}, "framework": {"artifact_id": "framework-artifact"}}
    result = api.materialize_ppiflow_generation_request(mode, params, tmp_path / "prepared", source)
    payload = json.loads((Path(result["ppiflow_generation_request"]) / "request.json").read_text())
    assert payload["requested_settings"] == params
    assert payload["source_identity"] == source
    assert payload["effective_settings"]["self_condition"] is False
    assert payload["effective_settings"]["rotation_exp_rate"] == 0
    assert payload["transport_settings"]["target_pdb"] == "inputs/target_pdb.pdb"
    if mode != "protein_binder":
        assert payload["transport_settings"]["framework_pdb"] == "inputs/framework_pdb.pdb"
        assert payload["source_bindings"][0]["sha256"] != payload["source_bindings"][1]["sha256"]
    assert params["target_pdb"].endswith("same.pdb")


@pytest.mark.parametrize("change", [
    {"start_t": 0.8}, {"num_timesteps": True}, {"self_condition": "false"},
    {"rotation_sample_schedule": "unknown"}, {"samples_max_length": 50},
    {"samples_per_target": 3, "samples_batch_size": 2}, {"specified_hotspots": "R2A"},
    {"specified_hotspots": ""}, {"target_pdb": None}, {"binder_chain": None},
    {"sample_hotspot_rate_min": 0.1, "specified_hotspots": None},
    {"rotation_exp_rate": float("nan")}, {"num_timesteps": None}, {"self_condition": None},
])
def test_native_invalid_settings_not_silently_dropped(tmp_path, change):
    params = request(tmp_path)
    params.update(change)
    with pytest.raises(ValueError):
        api.normalize_ppiflow_generation_params("protein_binder", params)


@pytest.mark.parametrize("mode,change", [
    ("antibody_binder", {"light_chain": None}),
    ("nanobody_binder", {"light_chain": "L"}),
    ("nanobody_binder", {"antigen_chain": "RS"}),
    ("antibody_binder", {"cdr_length": "CDRH1,2-1"}),
    ("nanobody_binder", {"specified_hotspots": "A2"}),
])
def test_antibody_inputs_remain_native_not_partial(tmp_path, mode, change):
    params = request(tmp_path, mode)
    params.update(change)
    with pytest.raises(ValueError):
        api.normalize_ppiflow_generation_params(mode, params)


def test_native_chain_case_preserved_not_coerced(tmp_path):
    params = request(tmp_path)
    params.update(target_chain="r", binder_chain="b", specified_hotspots="r2")
    normalized = api.normalize_ppiflow_generation_params("protein_binder", params)
    assert normalized["target_chain"] == "r"
    assert normalized["binder_chain"] == "b"


def test_random_hotspot_missing_native_default_is_explicit(tmp_path):
    params = request(tmp_path)
    params["specified_hotspots"] = None
    # Missing native metadata is not a new blanket gate. Only the native
    # nonempty random-hotspot branch reads this key; empty masks do not.
    assert api.normalize_ppiflow_generation_params("protein_binder", params)["samples_min_hotspots"] is None
    params["samples_min_hotspots"] = 0
    assert api.normalize_ppiflow_generation_params("protein_binder", params)["samples_min_hotspots"] == 0


def test_csv_snapshot_dependency_transport_without_pickle_load(tmp_path, native):
    data = tmp_path / "source"
    data.mkdir()
    (data / "features.pkl").write_bytes(b"not a pickle; byte transport only")
    (data / "source.csv").write_text("pdb_name,processed_path,num_chains\ntarget-id,features.pkl,2\n")
    params = {"input_csv": str(data / "source.csv"), "specified_hotspots": "R2"}
    staged = api.materialize_ppiflow_generation_request("protein_binder", params, tmp_path / "request")
    relocated = tmp_path / "relocated"
    shutil.move(staged["ppiflow_generation_request"], relocated)
    shutil.rmtree(data)
    context, argv = runner.prepare_invocation(relocated, tmp_path / "result", native)
    assert "--input_csv" in argv and "--input_pdb" not in argv
    with (tmp_path / "result/native_input.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert Path(row["processed_path"]).read_bytes() == b"not a pickle; byte transport only"
    assert context["request"]["source_rows"][0]["native_target_name"] == "target-id"


def test_existing_source_authority_covers_csv_dependencies(tmp_path):
    (tmp_path / "features.pkl").write_bytes(b"transport-only")
    source = tmp_path / "source.csv"
    source.write_text("pdb_name,processed_path\ntarget,features.pkl\n")
    authorized = []
    api.materialize_ppiflow_generation_request("protein_binder", {"input_csv": str(source)},
        tmp_path / "request", authorize_source=authorized.append)
    assert authorized == [source.resolve(), (tmp_path / "features.pkl").resolve()]


def test_native_csv_names_cannot_escape_owned_output(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("pdb_name,processed_path\n../../outside,features.pkl\n")
    with pytest.raises(ValueError, match="owned output"):
        api.materialize_ppiflow_generation_request("protein_binder", {"input_csv": str(source)}, tmp_path / "request")


def native_namespace(path, names):
    """Execute only dependency-light native definitions, never native imports."""
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    namespace = dict(argparse=argparse, os=os, shutil=shutil, yaml=yaml, time=time,
                     Dict=typing.Dict, Any=typing.Any, Optional=typing.Optional)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.mark.parametrize("mode", MODES)
def test_actual_native_cli_defaults_and_effective_pipeline_settings(tmp_path, native, mode):
    params = request(tmp_path, mode)
    params.update(num_timesteps=23, self_condition=False, translation_var_scale=7.25)
    staged = api.materialize_ppiflow_generation_request(mode, params, tmp_path / "request")
    context, argv = runner.prepare_invocation(staged["ppiflow_generation_request"], tmp_path / "output", native)
    namespace = native_namespace(Path(argv[0]), {"ConfigManager", "get_parser", "run_pipeline"})
    parser = namespace["get_parser"]()
    # Every native scientific CLI flag has a typed field, or a documented
    # runtime owner. Both original argparse defaults and BMS defaults are checked.
    flags = {a.dest for a in parser._actions} - {"help", "config", "model_weights", "output_dir", "name"}
    mapped = set()
    for field in api.parameter_contract(mode):
        flag = field.get("native_cli")
        if isinstance(flag, dict):
            flag = flag[mode]
        if flag:
            mapped.add(flag)
            action = next(a for a in parser._actions if a.dest == flag)
            if "default" in field:
                assert field["default"] == action.default
    # light_chain belongs to antibody mode; native heavy-only defaults to None.
    assert flags - mapped == ({"light_chain"} if mode == "nanobody_binder" else set())
    args = parser.parse_args(argv[1:])
    captured = {}
    namespace["preprocess_csv_and_pkl"] = lambda *a, **kw: "non-science-input.csv"
    namespace["OmegaConf"] = types.SimpleNamespace(create=lambda cfg: cfg)
    class NoScienceExperiment:
        def __init__(self, cfg):
            captured.update(copy.deepcopy(cfg))
        def test(self):
            captured["test_invoked"] = True
    namespace["Experiment"] = NoScienceExperiment
    namespace["run_pipeline"](args)
    assert captured["test_invoked"] is True
    assert captured["interpolant"]["sampling"]["num_timesteps"] == 23
    assert captured["interpolant"]["self_condition"] is False
    assert captured["interpolant"]["trans"]["var_scale"] == 7.25
    assert captured["experiment"]["testing_model"]["ckpt_path"].endswith(api.MODES[mode][2])
    assert captured["ppi_dataset"]["samples_per_target"] == (2 if mode == "protein_binder" else 1)
    assert context["receipt"]["native_source"]["expected_revision"] == api.SOURCE_REVISION
    assert context["receipt"]["native_source"]["sha256"]


@pytest.mark.parametrize("family", ["binder", "antibody"])
def test_instrumentation_is_export_only_at_native_row_append(native, family):
    path = native / f"models/flow_module_{family}.py"
    source = path.read_text()
    code = runner.instrument(source, str(path), "producer")
    assert code
    original = ast.parse(source)
    # Inspect disassembly rather than importing Torch/model classes.
    import dis
    def names(code):
        result = set(code.co_names)
        for constant in code.co_consts:
            if isinstance(constant, types.CodeType):
                result |= names(constant)
        return result
    assert "record_sample" in names(code)
    assert "record_sample" not in names(compile(original, str(path), "exec"))


def test_native_preprocessing_metadata_first_use_repair(native):
    source = (native / "preprocessing/process_pdb_for_inputs.py").read_text()
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "process_file")
    original = ast.unparse(function)
    assert original.index("generate_virtual_binder_feats") < original.index("complex_feats['target_interface_residues'] =")
    # Run the instrumented function until the virtual helper, using scalar
    # stand-ins. This reproduces and repairs the KeyError without importing numpy.
    class StopAtBoundary(Exception):
        pass
    def virtual(feats, binder):
        assert feats["target_interface_residues"] == [2]
        raise StopAtBoundary
    fake = types.SimpleNamespace
    chain = fake(id="R")
    namespace = dict(os=os, dataclasses=fake(asdict=lambda _: {}),
        PDB=fake(PDBParser=lambda **kw: fake(get_structure=lambda *a: fake(get_chains=lambda: [chain]))),
        parsers=fake(process_chain=lambda *a: {}),
        du=fake(chain_str_to_int=lambda x: 0, parse_chain_feats=lambda *a, **kw: {'aatype': []}, concat_np_features=lambda *a: {}),
        generate_virtual_binder_feats=virtual)
    exec(runner.instrument(ast.unparse(ast.Module(body=[function], type_ignores=[])), "preprocess", "preprocessing"), namespace)
    with pytest.raises(StopAtBoundary):
        namespace["process_file"]({"pdbfile": "unused", "PDBID": "target", "chain1_id": "R", "chain2_id": "B", "chain1_residues": [2]}, "unused")


@pytest.mark.parametrize("mode", MODES)
def test_explicit_producer_identity_not_filename_or_rank(tmp_path, native, mode):
    staged = api.materialize_ppiflow_generation_request(mode, request(tmp_path, mode), tmp_path / "request")
    context, _ = runner.prepare_invocation(staged["ppiflow_generation_request"], tmp_path / "results", native)
    runner._CONTEXT = context
    path = tmp_path / "results/completely-unrelated-name.pdb"
    path.write_text("REMARK transport fixture, not native science\n")
    if mode == "protein_binder":
        values = dict(batch={"pdb_name": ["native-key"], "original_index": [0]}, i=0,
                      sample_ids=[37], saved_path=str(path), test_metric={"native_metric": 4.2})
    else:
        runner.record_preprocessed({"pdb_name": "explicit-input-key", "id": "native-preprocessed-id"}, 37)
        values = dict(batch={"pdb_name": ["explicit-input-key"]}, i=0, attempt=3,
                      pdb_path=str(path), test_metric={"native_metric": 4.2})
    runner.record_sample(values)
    runner.record_config({"exact_effective": False})
    data = api.read_ppiflow_generation_result(tmp_path / "results")
    assert data["records"][0]["candidate_key"] == "source:0/sample:37"
    assert data["records"][0]["native_path"] == "completely-unrelated-name.pdb"
    assert data["records"][0]["path"] == "candidates/source-0/sample-37.pdb"
    assert data["records"][0]["metrics"] == {"native_metric": 4.2}
    assert data["receipt"]["native_effective_config"] == {"exact_effective": False}
    assert data["receipt"]["emitted_samples"] == 1


def test_real_script_invocation_with_explicit_non_science_native_fixture(tmp_path, native):
    """Exercise subprocess/argv/settings/producer hooks, not scientific inference."""
    params = request(tmp_path, "nanobody_binder")
    prepared = api.materialize_ppiflow_generation_request("nanobody_binder", params, tmp_path / "request")
    fixture = tmp_path / "native-fixture"
    shutil.copytree(native, fixture)
    # This fixture imports no scientific dependencies and is explicitly not a
    # substituted acceptance output; it tests the production invocation path.
    (fixture / "sample_antibody_nanobody.py").write_text('''import argparse
import json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--output_dir')
a, other = p.parse_known_args()
Path(a.output_dir, 'fixture_transport.json').write_text(json.dumps(other))
''')
    result = subprocess.run([sys.executable, str(ROOT / "scripts/run_ppiflow_generation.py"),
        "--request", prepared["ppiflow_generation_request"], "--output", str(tmp_path / "result"),
        "--native-root", str(fixture)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    readback = api.read_ppiflow_generation_result(tmp_path / "result")
    assert readback["records"] == []
    assert readback["receipt"]["status"] == "completed"
    assert readback["receipt"]["requested_samples"] == 2
    assert readback["receipt"]["emitted_samples"] == 0
    assert readback["receipt"]["unemitted_samples"] == 2
    assert readback["receipt"]["native_effective_config"] is None
    args = json.loads((tmp_path / "result/fixture_transport.json").read_text())
    assert "--antigen_pdb" in args and "--framework_pdb" in args
    assert "--complex_pdb" not in args


@pytest.mark.parametrize("mode", MODES)
def test_every_reachable_sampler_setting_is_typed(native, mode):
    family = "binder" if mode == "protein_binder" else "antibody"
    tree = ast.parse((native / f"data/interpolant_{family}.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Interpolant")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    queue = ["sample" if family == "binder" else "sample_antibody"]
    seen, paths = set(), set()
    aliases = {"self._cfg.": "interpolant.", "self._rots_cfg.": "interpolant.rots.",
               "self._trans_cfg.": "interpolant.trans.", "self._sample_cfg.": "interpolant.sampling."}
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(methods[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "self" and node.func.attr in methods:
                    queue.append(node.func.attr)
            if isinstance(node, ast.Attribute):
                text = ast.unparse(node)
                for prefix, mapped in aliases.items():
                    if text.startswith(prefix):
                        paths.add(mapped + text.removeprefix(prefix))
    exposed = {p["native_path"] for p in api.parameter_contract(mode)
               if p.get("native_path", "").startswith("interpolant.")}
    assert paths == exposed


def test_snapshot_preserves_explicit_samples_under_native_filename_reuse(tmp_path, native):
    prepared = api.materialize_ppiflow_generation_request("protein_binder", request(tmp_path), tmp_path / "request")
    context, _ = runner.prepare_invocation(prepared["ppiflow_generation_request"], tmp_path / "result", native)
    runner._CONTEXT = context
    path = tmp_path / "result/reused-native-filename.pdb"
    for sample, text in [(0, "first"), (1, "second")]:
        path.write_text(text)
        runner.record_sample(dict(batch={"pdb_name": ["same-key"], "original_index": [0]}, i=0,
            sample_ids=[sample], saved_path=str(path), test_metric={"metric": float("nan")}))
    assert (tmp_path / "result/candidates/source-0/sample-0.pdb").read_text() == "first"
    assert (tmp_path / "result/candidates/source-0/sample-1.pdb").read_text() == "second"
    records = [json.loads(x) for x in (tmp_path / "result/samples.jsonl").read_text().splitlines()]
    assert records[0]["metrics"]["metric"] == {"native_nonfinite": "nan"}
    assert len({r["candidate_key"] for r in records}) == 2


def test_producer_hook_executes_at_append(tmp_path, native):
    prepared = api.materialize_ppiflow_generation_request("protein_binder", request(tmp_path), tmp_path / "request")
    context, _ = runner.prepare_invocation(prepared["ppiflow_generation_request"], tmp_path / "result", native)
    runner._CONTEXT = context
    source = '''class FlowModule:
    def __init__(self):
        self.test_epoch_metrics = []
    def test_step(self, batch, batch_idx):
        i = 0
        sample_ids = [19]
        saved_path = batch['output']
        test_metric = {'fixture_metric': 0.25}
        self.test_epoch_metrics.append(test_metric)
'''
    namespace = {"_bms_export": runner}
    exec(runner.instrument(source, "non-science-producer-fixture", "producer"), namespace)
    path = tmp_path / "result/native-file.pdb"
    path.write_text("REMARK NON-SCIENCE\n")
    namespace["FlowModule"]().test_step({"pdb_name": ["source-key"], "original_index": [0], "output": str(path)}, 900)
    record = json.loads((tmp_path / "result/samples.jsonl").read_text())
    assert record["sample_index"] == 19  # neither filename nor batch_idx/rank
    assert record["metrics"] == {"fixture_metric": 0.25}


def test_missing_source_observation_does_not_become_a_proof_gate(tmp_path, native):
    prepared = api.materialize_ppiflow_generation_request("protein_binder", request(tmp_path), tmp_path / "request")
    runtime = tmp_path / "runtime"
    (runtime / "configs").mkdir(parents=True)
    shutil.copyfile(native / "configs/inference_binder.yaml", runtime / "configs/inference_binder.yaml")
    context, argv = runner.prepare_invocation(prepared["ppiflow_generation_request"], tmp_path / "result", runtime)
    assert context["receipt"]["native_source"]["observed_ref"] is None
    assert context["receipt"]["native_source"]["sha256"]["models/flow_module_binder.py"] is None
    assert context["receipt"]["status"] == "prepared"  # not a native execution claim


def test_route_is_distinct_and_old_process_is_retained():
    workflow = (ROOT / "workflows/ppiflow_generation.nf").read_text()
    module = (ROOT / "modules/ppiflow.nf").read_text()
    assert "RunPPIFlowGeneration" in workflow
    assert "RunPartialFlow" not in workflow
    assert "process RunPartialFlow" in module
    assert "sample_antibody_nanobody_partial.py" in module
    assert "generation_request" in workflow
