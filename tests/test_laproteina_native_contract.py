"""CPU native composition/feature/writer checks; no weights or GPU inference.

LAPROTEINA_TEST_SOURCE must be the official pinned checkout. These checks use
real Hydra, upstream feature tensors and the native PDB writer, not mock files.
"""
import ast
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from lib.laproteina_contract import CONTROLS, PROFILES, apply_runtime_patches, resolve_profile
from run_laproteina_inference import _configure_laproteina, collect_outputs


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    source = os.environ.get("LAPROTEINA_TEST_SOURCE")
    if not source:
        pytest.skip("Set LAPROTEINA_TEST_SOURCE to the pinned official source for CPU native checks")
    destination = tmp_path_factory.mktemp("native") / "source"
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    apply_runtime_patches(destination)
    sys.path.insert(0, str(destination))
    return destination


def request(preset):
    return {"job_name": "test", "task": resolve_profile(preset)["task"],
            "target_lengths": [100, 200], "num_designs": 3,
            "laproteina": {"preset": preset, "samples_per_length": 2, "num_steps": 400,
                            "checkpoint_dir": "/read-only/weights", "motif_task_name": "1YCR_AA"}}


def load_native_function(path, name, namespace):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace.update({"__file__": str(path), "__name__": "__main__"})
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("preset", list(PROFILES))
def test_complete_private_native_composition(native, tmp_path, monkeypatch, preset):
    import hydra
    import numpy as np
    import argparse
    from loguru import logger
    from typing import Dict, Tuple
    monkeypatch.setenv("LAPROTEINA_REPO", str(native))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (native / "configs").rglob("*.yaml")}
    req = request(preset)
    first = _configure_laproteina(req, tmp_path)
    second = _configure_laproteina(req, tmp_path)
    assert first["attempt"] != second["attempt"]
    assert first["inference_root"] != second["inference_root"]
    # Run upstream's real CLI config_subdir parser with its original filename.
    parse = load_native_function(native / "proteinfoundation/generate.py", "parse_args_and_cfg",
                                 dict(os=os, sys=sys, hydra=hydra, np=np, argparse=argparse,
                                      logger=logger, Dict=Dict, Tuple=Tuple))
    monkeypatch.setattr(sys, "argv", first["command"][1:])
    _, cfg, _ = parse()
    assert cfg.ckpt_name == PROFILES[preset][0]
    assert cfg.autoencoder_ckpt_path == "/read-only/weights/" + PROFILES[preset][1]
    assert cfg.seed == 5
    assert cfg.generation.args.nsteps == 400
    assert cfg.generation.args.self_cond is True
    assert cfg.generation.dataset.nsamples == (2 if preset.startswith("ucond") else 3)
    assert cfg.generation.model.bb_ca.simulation_step_params.sc_scale_noise == (0.15 if preset.endswith("long") else 0.1)
    assert cfg.generation.model.local_latents.simulation_step_params.sc_scale_noise == (0.05 if preset.endswith("long") else 0.1)
    assert cfg.generation.model.bb_ca.simulation_step_params.center_every_step == preset.startswith("ucond")
    assert (first["attempt"] / "configs/inference_base.yaml").exists()
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (native / "configs").rglob("*.yaml")}


def test_explicit_controls_and_atom_selection(native, tmp_path, monkeypatch):
    monkeypatch.setenv("LAPROTEINA_REPO", str(native))
    req = request("motif_idx_tip")
    req["laproteina"].update(seed=0, self_cond=False, max_nsamples_per_batch=2,
        bb_ca_schedule_mode="power", bb_ca_schedule_p=3.25, bb_ca_gt_mode="tan",
        bb_ca_gt_clamp_val=None, bb_ca_sc_scale_noise=0.27, local_latents_sc_scale_score=1.7,
        local_latents_sampling_mode="vf_ss_sc_sn", save_trajectory_every=25,
        motif_task_name="", motif_pdb="/retained/source.pdb", motif_atom_spec="A64: [O, CG]; A65: [N, CA]",
        contig_string="A64-65", segment_order="A", atom_selection_mode="tip_atoms")
    inv = _configure_laproteina(req, tmp_path)
    cfg = inv["receipt"]["effective_config"]
    assert cfg["seed"] == 0 and cfg["generation"]["args"]["self_cond"] is False
    assert cfg["generation"]["model"]["bb_ca"]["schedule"] == {"mode": "power", "p": 3.25}
    assert cfg["generation"]["model"]["bb_ca"]["gt"]["mode"] == "tan"
    assert cfg["generation"]["model"]["local_latents"]["simulation_step_params"]["sc_scale_score"] == 1.7
    assert cfg["generation"]["dataset"]["motif_dict_cfg"]["BMS_CUSTOM"]["motif_atom_spec"] == req["laproteina"]["motif_atom_spec"]
    assert inv["env"]["LAPROTEINA_MOTIF_FEATURE"] == "indexed_tip"
    assert len({s["native"] for s in CONTROLS.values()}) == len(CONTROLS)
    with pytest.raises(KeyError):
        resolve_profile("not_a_preset")


def test_indexed_tip_uses_real_native_feature(native, monkeypatch):
    import torch
    from proteinfoundation.nn.feature_factory import FeatureFactory, XmotifBulkTipSeqFeat, BulkAllAtomXmotifSeqFeat
    monkeypatch.setenv("LAPROTEINA_MOTIF_FEATURE", "indexed_tip")
    feature = FeatureFactory(["x_motif"], 8, False, "seq").feat_creators[0]
    assert isinstance(feature, XmotifBulkTipSeqFeat)
    batch = {"x_motif": torch.ones(1, 2, 37, 3), "motif_mask": torch.zeros(1, 2, 37),
             "seq_motif": torch.zeros(1, 2, dtype=torch.long)}
    batch["motif_mask"][0, 0, 4] = 1
    result = feature(batch)
    assert result.shape == (1, 2, feature.dim)
    assert result[0, 0].abs().sum() > 0
    assert result[0, 1].abs().sum() == 0
    monkeypatch.setenv("LAPROTEINA_MOTIF_FEATURE", "all_atom")
    other = FeatureFactory(["x_motif"], 8, False, "seq").feat_creators[0]
    assert isinstance(other, BulkAllAtomXmotifSeqFeat)
    assert other.dim != feature.dim


def test_native_trajectory_observation_preserves_sampling(native, tmp_path, monkeypatch):
    import torch
    from omegaconf import OmegaConf
    from proteinfoundation.flow_matching.product_space_flow_matcher import ProductSpaceFlowMatcher
    monkeypatch.setenv("LAPROTEINA_REPO", str(native))
    inv = _configure_laproteina(request("ucond_notri_long"), tmp_path)
    model_args = inv["receipt"]["effective_config"]["generation"]["model"]
    matcher = ProductSpaceFlowMatcher(OmegaConf.create({"product_flowmatcher": {
        "bb_ca": {"dim": 3}, "local_latents": {"dim": 4}}}))

    # Deliberately synthetic NN, real upstream schedules, stochastic integrator
    # and trajectory seam. This checks observational neutrality, not design yield.
    def velocity(batch, mode):
        return {key: {"v": torch.zeros_like(value)} for key, value in batch["x_t"].items()}

    def run(every):
        torch.manual_seed(17)
        return matcher.full_simulation({}, velocity, 6, 2, 3, True, model_args,
                                       torch.device("cpu"), save_trajectory_every=every)

    plain, plain_info = run(0)
    observed, observed_info = run(2)
    assert "trajectory" not in plain_info
    assert [r["step"] for r in observed_info["trajectory"]] == [2, 4, 6]
    for key in plain:
        assert torch.equal(plain[key], observed[key])
        assert torch.equal(observed[key], observed_info["trajectory"][-1]["state"][key])
        assert observed[key].data_ptr() != observed_info["trajectory"][-1]["state"][key].data_ptr()


@pytest.mark.parametrize("motif", [False, True])
def test_native_writer_exact_receiving(native, tmp_path, motif):
    import torch
    import numpy as np
    from typing import List, Tuple
    from proteinfoundation.utils.pdb_utils import write_prot_to_pdb
    path = native / "proteinfoundation/generate.py"
    save = load_native_function(path, "save_motif_predictions" if motif else "save_predictions",
        dict(torch=torch, np=np, os=os, json=json, defaultdict=defaultdict, List=List, Tuple=Tuple,
             write_prot_to_pdb=write_prot_to_pdb))
    output = tmp_path / "output"
    attempt = output / "invocation_test"
    native_root = attempt / "inference/samples"
    native_root.mkdir(parents=True)
    predictions = [[(torch.ones(3, 37, 3), torch.tensor([0, 1, 2]))],
                   [(torch.ones(3, 37, 3), torch.tensor([3, 4, 5]))]]
    save(str(native_root), predictions, **({"motif_pdb_name": "TEST"} if motif else {}))
    index = [json.loads(line) for line in (native_root / "sample_index.jsonl").read_text().splitlines()]
    # A decoy PDB sorts first; the native index, not directory order, owns pairing.
    for row in index:
        (native_root / row["structure_path"]).parent.joinpath("000_decoy.pdb").write_text("DECOY")
    result = collect_outputs(request("motif_idx_aa" if motif else "ucond_tri"),
                             {"attempt": attempt, "inference_root": native_root}, output)
    assert [row["sequence"] for row in result] == ["ARN", "DCQ"]
    assert [row["native_sample_id"] for row in result] == [row["native_sample_id"] for row in index]
    assert [(r["batch_index"], r["sample_index"]) for r in index] == [(0, 0), (1, 0)]
    assert all("ATOM" in (output / row["structure_path"]).read_text() for row in result)
    assert all("DECOY" not in (output / row["structure_path"]).read_text() for row in result)
    # Missing coordinate bytes remain an indexed sequence observation, not a
    # different PDB borrowed from the sample directory.
    (native_root / index[0]["structure_path"]).unlink()
    partial = collect_outputs(request("ucond_tri"), {"attempt": attempt, "inference_root": native_root}, output)
    assert partial[0]["structure_path"] is None
    assert partial[0]["sequence"] == "ARN"
