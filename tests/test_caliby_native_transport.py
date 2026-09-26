"""Real Nextflow/leaf transport with an explicitly inert native science fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["ensemble_design", "sidechain_pack"])
def test_standalone_real_shell_and_installed_native_api(tmp_path, mode):
    """Real leaf shell + installed API; downstream scientific kernels are inert."""
    import importlib.util
    image = Path(os.environ.get("BMS_TEST_CALIBY_IMAGE", "/mnt/BioModStack/apptainer/caliby.sif"))
    jars = sorted((Path.home() / ".nextflow/framework").glob("*/nextflow-*-one.jar"))
    if not image.is_file() or not shutil.which("apptainer") or not jars:
        pytest.skip("Installed Caliby image and Nextflow required; no downloads")
    spec = importlib.util.spec_from_file_location("standalone_caliby", REPO / "platform/api/services/caliby_native.py")
    contract = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contract
    spec.loader.exec_module(contract)
    native_source = subprocess.check_output([
        "apptainer", "exec", str(image), "python", "-c",
        "from pathlib import Path; print(Path('/opt/caliby/caliby/api.py').read_text())"], text=True)
    fixture = tmp_path / "fixture"
    package = fixture / "caliby"
    (package / "eval/eval_utils").mkdir(parents=True)
    (package / "api.py").write_text(native_source)
    (package / "__init__.py").write_text('''
from .api import CalibyModel
from omegaconf import OmegaConf

def load_model(name, device=None):
    cfg = OmegaConf.load('/opt/caliby/caliby/configs/seq_des/inference.yaml')
    return CalibyModel(model='INERT_NO_SCIENCE', data_cfg={}, sampling_cfg=cfg, device='cpu')
''')
    for directory in [package / "eval", package / "eval/eval_utils"]:
        (directory / "__init__.py").write_text("")
    (package / "eval/eval_utils/seq_des_utils.py").write_text('''
from pathlib import Path
import json
from omegaconf import OmegaConf

def emit(paths, out_dir, cfg, operation, extra):
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'inert_call.json').write_text(json.dumps(dict(operation=operation, cfg=OmegaConf.to_container(cfg, resolve=True), **extra)))
    result = dict(example_id=[], out_pdb=[])
    if operation == 'ensemble_design':
        result.update(seq=[], U=[], input_seq=[])
    for source in paths:
        target = root / (Path(source).stem + '.cif')
        target.write_text('data_EXPLICITLY_INERT_TRANSPORT_FIXTURE\\n')
        result['example_id'].append(Path(source).stem)
        result['out_pdb'].append(str(target))
        if operation == 'ensemble_design':
            result['seq'].append('INERT')
            result['input_seq'].append('INERT')
            result['U'].append(-1.25)
    return result

def run_seq_des_ensemble(*, model, data_cfg, sampling_cfg, pdb_to_conformers, device, out_dir, pos_constraint_df, use_primary_res_type):
    assert model == 'INERT_NO_SCIENCE'
    assert device == 'cpu'
    return emit([paths[0] for paths in pdb_to_conformers.values()], out_dir, sampling_cfg, 'ensemble_design',
                dict(mapping=pdb_to_conformers, primary=use_primary_res_type, constraints=pos_constraint_df.to_dict('records')))

def run_sidechain_packing(*, model, data_cfg, sampling_cfg, pdb_paths, device, out_dir):
    assert model == 'INERT_NO_SCIENCE'
    assert device == 'cpu'
    return emit(pdb_paths, out_dir, sampling_cfg, 'sidechain_pack', dict(paths=pdb_paths))
''')
    source_paths = []
    for directory in ("first", "second"):
        source = tmp_path / directory / "same name.cif"
        source.parent.mkdir()
        source.write_text("data_INERT_INPUT\n")
        source_paths.append(source)
    states = [{"state_id": name, "path": str(path)} for name, path in zip(("primary state", "other state"), source_paths)]
    params = {"num_workers": 0, "batch_size": 2, "scn_num_steps": 7, "scn_step_scale": 0.8}
    if mode == "ensemble_design":
        params.update(ensembles=[{"ensemble_id": "ensemble with spaces", "states": states}],
                      omit_aas=[], verbose=False, use_primary_res_type=False, temperature=0.23,
                      potts_sweeps=11, potts_proposal="chromatic")
    else:
        params.update(structures=states, packer_model_name="caliby_packer_030")
    request = contract.normalize_request(mode, params)
    request_json = tmp_path / "normalized.json"
    request_json.write_text(json.dumps(request))
    prepared = tmp_path / "prepared input"
    subprocess.run([sys.executable, str(REPO / "scripts/prep_caliby_request.py"), "--request", str(request_json), "--output-dir", str(prepared)], check=True)
    relocated = tmp_path / "relocated"
    shutil.move(prepared, relocated)
    for source in source_paths:
        source.unlink()
    checkpoint = tmp_path / "weights/caliby" / (request.get("model_name", request.get("packer_model_name")) + ".ckpt")
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("INERT; no checkpoint loading")
    binary = tmp_path / "bin"
    binary.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    binds = str(cache) + ":/cache," + str(checkpoint.parent.parent) + ":/weights/caliby/model_params"
    (binary / "python3").write_text("#!/bin/sh\nexec apptainer exec --bind " + shlex.quote(binds) + " --env " + shlex.quote("PYTHONPATH=" + str(fixture)) + " " + shlex.quote(str(image)) + ' python "$@"\n')
    (binary / "python3").chmod(0o755)
    config = tmp_path / "fixture.config"
    config.write_text("process.executor = 'local'\napptainer.enabled = false\ndocker.enabled = false\n")
    params_file = tmp_path / "params.json"
    params_file.write_text(json.dumps({"code_root": str(REPO), "out_dir": str(tmp_path / "out"), "caliby_request_dir": str(relocated)}))
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
               MODEL_PARAMS_DIR=str(checkpoint.parent.parent), CALIBY_ALLOW_DOWNLOAD="0",
               NXF_HOME=str(tmp_path / "nxf"), NXF_OFFLINE="true")
    completed = subprocess.run(["java", "-jar", str(jars[-1]), "-C", str(config), "run", str(REPO / "workflows/caliby_native.nf"),
                                "-params-file", str(params_file), "-work-dir", str(tmp_path / "work")],
                               cwd=tmp_path, env=env, text=True, capture_output=True, timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    returned = tmp_path / "returned"
    shutil.copytree(tmp_path / "out/caliby_native", returned)
    shutil.rmtree(tmp_path / "work")
    shutil.rmtree(tmp_path / "out")
    shutil.rmtree(relocated)
    result = contract.read_native_results(returned)
    assert result["request"]["requested"] == request
    assert result["effective_sampling"]["num_workers"] == 0
    assert result["effective_sampling"]["scn_packing_cfg"]["num_steps"] == 7
    call = json.loads((returned / "native/inert_call.json").read_text())
    assert call["operation"] == mode
    if mode == "ensemble_design":
        assert call["primary"] is False
        assert call["cfg"]["omit_aas"] == []
        assert call["cfg"]["potts_sampling_cfg"]["potts_temperature"] == 0.23
        assert len(call["mapping"]["ensemble with spaces"]) == 2
        assert result["records"][0]["source"]["state_id"] == "primary state"
        assert result["records"][0]["native"]["U"] == -1.25
    else:
        assert len(result["records"]) == 2
        assert all("U" not in row["native"] and "seq" not in row["native"] for row in result["records"])
        assert result["runtime"]["model_name"] == "caliby_packer_030"
    assert all((returned / row["structure_path"]).read_text().startswith("data_EXPLICITLY_INERT") for row in result["records"])



def test_standalone_installed_packing_fixes_sequence_mask(tmp_path):
    import ast
    from collections import defaultdict
    from types import SimpleNamespace

    image = Path(os.environ.get("BMS_TEST_CALIBY_IMAGE", "/mnt/BioModStack/apptainer/caliby.sif"))
    if not image.is_file() or not shutil.which("apptainer"):
        pytest.skip("Installed Caliby image required; no downloads")
    sources = json.loads(subprocess.check_output([
        "apptainer", "exec", str(image), "python", "-c",
        "from pathlib import Path; import json; print(json.dumps({p:Path('/opt/caliby/caliby/'+p).read_text() for p in ['eval/eval_utils/seq_des_utils.py','model/seq_denoiser/denoisers/atom_mpnn_denoiser.py','model/seq_denoiser/sd_model.py']}))"], text=True))
    source = sources["eval/eval_utils/seq_des_utils.py"]
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "run_sidechain_packing")
    for arg in function.args.kwonlyargs:
        arg.annotation = None
    function.returns = None
    class Mask:
        def clone(self):
            return "ALL_RESOLVED_SEQUENCE_FIXED"
    batch = {"example_id": ["explicit_source"], "token_resolved_mask": Mask()}
    def pack(actual, sampling_inputs):
        assert actual["seq_cond_mask"] == "ALL_RESOLVED_SEQUENCE_FIXED"
        return ["INERT_ARRAY"]
    namespace = {"defaultdict": defaultdict, "Path": Path,
                 "InferenceDataLoader": lambda *a, **kw: [batch],
                 "initialize_sampling_masks": lambda x: x,
                 "OmegaConf": SimpleNamespace(to_container=lambda x, **kw: x),
                 "tqdm": lambda **kw: SimpleNamespace(update=lambda n: None, close=lambda: None),
                 "to_cif_string": lambda *a, **kw: "data_INERT\n"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "installed-native-packing", "exec"), namespace)
    cfg = SimpleNamespace(batch_size=1, num_workers=0)
    result = namespace["run_sidechain_packing"](model=SimpleNamespace(sidechain_pack=pack), data_cfg=None,
             sampling_cfg=cfg, pdb_paths=["inert.pdb"], device="cpu", out_dir=str(tmp_path))
    assert set(result) == {"example_id", "out_pdb"}
    assert Path(result["out_pdb"][0]).read_text() == "data_INERT\n"
    decoder = sources["model/seq_denoiser/denoisers/atom_mpnn_denoiser.py"]
    method = next(n for n in ast.walk(ast.parse(decoder)) if isinstance(n, ast.FunctionDef) and n.name == "sidechain_pack")
    encoded_seq = [kw.value for n in ast.walk(method) if isinstance(n, ast.Call) for kw in n.keywords if kw.arg == "encoded_seq"]
    assert len(encoded_seq) == 1
    assert ast.unparse(encoded_seq[0]) == "batch['encoded_seq'][bi][token_pad_mask]"
    assert 'self.task not in ["scn_pack"]' in sources["model/seq_denoiser/sd_model.py"]


@pytest.mark.parametrize("process", ["RunCaliby", "RunCalibyBinder"])
def test_real_caliby_shell_preserves_typed_settings_and_returns_native_files(tmp_path, process):
    configured = os.environ.get("BMS_TEST_NEXTFLOW_JAR")
    jars = [Path(configured)] if configured else sorted(
        (Path.home() / ".nextflow/framework").glob("*/nextflow-*-one.jar")
    )
    if not jars or not shutil.which("java"):
        pytest.skip("Installed Nextflow JAR and Java required; no downloads")
    native = tmp_path / "native"
    native.mkdir()
    # Only native cleaning/sampling is replaced. The real constraints script,
    # sequence runner, publication helper and Nextflow shell execute unchanged.
    (native / "caliby.py").write_text('''
import json
import os
from pathlib import Path
import shutil

def clean_pdbs(paths, out_dir, num_workers):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    cleaned = []
    for raw in paths:
        target = Path(out_dir) / Path(raw).name
        shutil.copy2(raw, target)
        cleaned.append(str(target))
    return cleaned

def load_model(name, device=None):
    return InertModel()

class InertModel:
    def sample(self, paths, **kwargs):
        constraints = kwargs.pop("pos_constraint_df")
        record = dict(kwargs, constraints=constraints.fillna("").to_dict("records"))
        Path(os.environ["CALIBY_TEST_CALL"]).write_text(json.dumps(record))
        outputs = {"example_id": [], "out_pdb": []}
        for raw in paths:
            target = Path(kwargs["out_dir"]) / "samples" / (Path(raw).stem + "_sample0.pdb")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw, target)
            outputs["example_id"].append(Path(raw).stem)
            outputs["out_pdb"].append(str(target))
        return outputs
''')
    pdb = tmp_path / "selected.pdb"
    pdb.write_text(
        "ATOM      1  CA  GLY H   1       0.000   0.000   0.000  1.00 50.00           C\n"
        "ATOM      2  CA  SER A   1       0.000   1.000   0.000  1.00 50.00           C\nEND\n"
    )
    source = tmp_path / "source.json"
    source.write_text(json.dumps([{"staged_name": pdb.name, "source_meta": {"id": 17, "parent_job_id": 23}}]))
    params = {
        "code_root": str(REPO), "out_dir": str(tmp_path / "out"),
        "binder_chains": "H", "target_chains": "A", "antibody_chains": "H",
        "antibody_design_mode": "full_design", "seqs_per_design": 2,
        "caliby_num_seqs_per_pdb": 2, "caliby_omit_aas": "", "caliby_temperature": 0,
        "caliby_verbose": False, "caliby_gaussian_n_conformers": 0,
        "caliby_gaussian_noise_std": 0.0, "caliby_potts_rejection_step": False,
        "caliby_potts_only_cond": False, "caliby_potts_sweeps": 11,
        "caliby_scn_step_scale": 0.0, "caliby_scn_num_steps": 7,
        "caliby_potts_regularization": "LCP", "caliby_potts_proposal": "dlmc",
        "caliby_sampling_overrides_json": json.dumps({
            "verbose": True, "potts_sampling_cfg": {"potts_sweeps": 22, "potts_temperature": 0.23},
        }),
    }
    params_file = tmp_path / "params.json"
    params_file.write_text(json.dumps(params))
    config = tmp_path / "fixture.config"
    config.write_text("process.executor = 'local'\napptainer.enabled = false\ndocker.enabled = false\n")
    workflow = tmp_path / "transport.nf"
    channel = (f"tuple([file('{pdb}')], file('{source}'))" if process == "RunCalibyBinder"
               else f"tuple([:], [file('{pdb}')])")
    workflow.write_text(
        f"nextflow.enable.dsl = 2\ninclude {{ {process} }} from '{REPO}/modules/caliby.nf'\n"
        f"workflow {{ {process}(Channel.value({channel})) }}\n"
    )
    model_root = tmp_path / "model_params"
    checkpoint = model_root / "caliby/soluble_caliby_v1.ckpt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"inert test checkpoint; never loaded")
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "python3").write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    (binary / "python3").chmod(0o755)
    call = tmp_path / "native-call.json"
    env = os.environ | {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(native), "MODEL_PARAMS_DIR": str(model_root),
        "HF_HOME": str(tmp_path / "cache/hf"), "XDG_CACHE_HOME": str(tmp_path / "cache/general"),
        "TRITON_CACHE_DIR": str(tmp_path / "cache/triton"), "CALIBY_TEST_CALL": str(call),
        "NXF_OFFLINE": "true", "NXF_HOME": str(tmp_path / "nxf"), "NXF_ANSI_LOG": "false",
    }
    result = subprocess.run(
        ["java", "-jar", str(jars[-1]), "-C", str(config), "run", str(workflow),
         "-params-file", str(params_file), "-work-dir", str(tmp_path / "work")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    jsonl = next((tmp_path / "work").glob("*/*/results/caliby_metadata.jsonl"))
    rows = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["example_id"] == "selected"
    invoked = json.loads(call.read_text())
    assert invoked["num_seqs_per_pdb"] == 2
    assert invoked["temperature"] == 0
    assert invoked["omit_aas"] == []
    overrides = invoked["sampling_overrides"]
    assert overrides["verbose"] is False
    assert overrides["gaussian_conformers_cfg"] == {"n_conformers": 0, "noise_std": 0.0}
    assert overrides["potts_sampling_cfg"] == {
        "potts_sweeps": 11, "potts_temperature": 0.23, "rejection_step": False,
        "potts_only_cond": False, "regularization": "LCP", "potts_proposal": "dlmc",
    }
    assert overrides["scn_packing_cfg"] == {"num_steps": 7, "step_scale": 0.0}
    assert "A1" in invoked["constraints"][0]["fixed_pos_seq"]
    published = tmp_path / "out/collected" / ("binder_generation/caliby" if process == "RunCalibyBinder" else "caliby_raw")
    returned = tmp_path / "returned"
    shutil.copytree(published, returned)
    shutil.rmtree(tmp_path / "work")
    shutil.rmtree(tmp_path / "out")
    record = json.loads((returned / "generator_caliby_0001.json").read_text())
    assert record["effective_settings"]["sampling_overrides"] == overrides
    assert (returned / record["native_output"]["path"]).read_bytes() == pdb.read_bytes()
    assert record["native_output"]["example_id"] == "selected"
    assert record["native_output"]["filename"] == "selected_sample0.pdb"
    assert [p.name for p in returned.glob("*.pdb")] == ["caliby_0001.pdb"]
    if process == "RunCalibyBinder":
        assert record["source_document_id"] == 17
        assert record["source_meta"]["parent_job_id"] == 23
