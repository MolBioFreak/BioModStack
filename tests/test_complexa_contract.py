"""Focused closed-contract, native Hydra composition and publication checks.

Set COMPLEXA_TEST_SOURCE to the pinned upstream checkout for CPU-only composition.
No checkpoint, model import, GPU or scientific inference is performed here.
"""
import csv
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.lib.complexa_contract import Request, TASKS, SOURCE_REVISION
from scripts.bms_complexa.config import compose_request, selected_dependencies
from scripts.bms_complexa.inputs import prepare_source
from scripts.bms_complexa.publication import publish


@pytest.fixture
def upstream():
    value = os.environ.get("COMPLEXA_TEST_SOURCE")
    if not value:
        pytest.skip("COMPLEXA_TEST_SOURCE is required for native Hydra composition")
    return Path(value)


def test_native_wrapper_bounds_jax_before_the_af2_reward_loads():
    """The AF2 reward and the generator share one 16 GB card; JAX must not preallocate."""
    text = (Path(__file__).resolve().parents[1] / "scripts/bms_complexa/run.py").read_text(encoding="utf-8")
    assert 'env.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")' in text
    # The reward path binds AF2_DIR from the launcher; the wrapper must not replace it.
    assert 'env.setdefault("AF2_DIR"' not in text


def test_closed_controls_and_explicit_clear():
    with pytest.raises(ValidationError):
        Request(task="protein_binder", task_name="x", search={"algorithm": "gradient_ascent"})
    with pytest.raises(ValidationError):
        Request(task="protein_binder", task_name="x", ckpt_name="la.ckpt")
    with pytest.raises(ValidationError):
        Request(task="AME", task_name="x", reward={"rf3folding": {"reward_weights": {"min_ipae": 1}}})
    r = Request(task="protein_binder", task_name="x", reward=None, seed=0)
    assert r.model_dump(exclude_unset=True)["reward"] is None
    assert r.seed == 0


@pytest.mark.parametrize("task,task_name", [
    ("protein_binder", "33_TrkA"), ("ligand_binder", "39_7V11_LIGAND"),
    ("AME", "M0024_1nzy_v3"), ("monomer_motif_indexed", "1YCR_AA"),
    ("monomer_motif_unindexed", "1YCR_AA")])
def test_native_profiles_and_searches(upstream, tmp_path, monkeypatch, task, task_name):
    from omegaconf import OmegaConf
    monkeypatch.setenv("DATA_PATH", str(upstream / "assets"))
    monkeypatch.setenv("AF2_DIR", str(tmp_path / "af2"))
    monkeypatch.setenv("RF3_CKPT_PATH", str(tmp_path / "rf3.ckpt"))
    monkeypatch.setenv("RF3_EXEC_PATH", "/opt/rf3")
    request = Request(task=task, task_name=task_name)
    cfg = compose_request(request, upstream, tmp_path / "default", tmp_path / "weights")
    expected = "best-of-n" if task in ("protein_binder", "ligand_binder") else "single-pass"
    assert cfg.generation.search.algorithm == expected
    assert cfg.ckpt_name.startswith("complexa")
    # Force resolution of the actual native feature/nres grammar, not model imports.
    OmegaConf.to_container(cfg.generation.dataloader, resolve=True)
    assert (cfg.generation.dataloader.dataset.nres is None) == (task == "monomer_motif_indexed")
    for algorithm in ("single-pass", "best-of-n", "beam-search", "fk-steering", "mcts"):
        req = Request(task=task, task_name=task_name, nsamples=3, batch_size=2,
                      reward=None, search={"algorithm": algorithm, "mcts": {"n_simulations": 7}})
        out = compose_request(req, upstream, tmp_path / algorithm, tmp_path / "weights")
        assert out.generation.search.algorithm == algorithm
        assert out.generation.search.max_batch_size == 2
        assert out.generation.search.mcts.n_simulations == 7
        assert "af2" not in selected_dependencies(req, out)
        assert "rf3" not in selected_dependencies(req, out)
        ds = out.generation.dataloader.dataset
        assert (ds.conditional_features[0].nsamples if task == "monomer_motif_indexed" else ds.nres.nsamples) == 3


def test_native_rewards_refinement_evaluation(upstream, tmp_path):
    req = Request(task="protein_binder", task_name="33_TrkA", operation="design",
                  reward={"rf3folding": {"reward_weights": {"min_ipAE": -2}}, "rf3_weight": 0.5},
                  refinement={"algorithm": "sequence_hallucination", "refine_targets": "all", "save_pre_refinement": "final"},
                  evaluation={"sequence_types": ["self", "mpnn_fixed"], "keep_folding_outputs": True})
    cfg = compose_request(req, upstream, tmp_path, tmp_path / "weights")
    assert cfg.generation.reward_model.reward_models.rf3folding.reward_weights.min_ipAE == -2
    assert cfg.generation.reward_model.weights.rf3folding == 0.5
    assert cfg.generation.refinement.save_pre_refinement == "final"
    assert cfg.metric.sequence_types == ["self", "mpnn_fixed"]
    assert {"af2", "rf3", "soluble_mpnn"} <= set(selected_dependencies(req, cfg))


def atom(serial, name, component, chain, number, xyz, record="ATOM", insertion=" "):
    return f"{record:<6}{serial:5d} {name:>4} {component:3} {chain}{number:4d}{insertion}   {xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}{1:6.2f}{20:6.2f}          {'C':>2}  \n"


def test_ame_source_geometry_chemistry_and_native_config(upstream, tmp_path):
    raw = (atom(1, "CA", "ALA", "X", 10, (1, 2, 3), insertion="A") +
           atom(2, "C1", "BCA", "Z", 3, (4, 5, 6), "HETATM") +
           atom(3, "C2", "BCA", "Z", 3, (7, 8, 9), "HETATM") + "CONECT    2    3\nEND\n")
    src = tmp_path / "input.pdb"
    src.write_text(raw)
    req = Request(task="AME", task_name="custom", min_length=50, max_length=60, source={
        "document_id": "source:original", "motif_atoms": [{"chain": "X", "residue": 10, "insertion_code": "A", "atoms": ["CA"]}],
        "ligand_atoms": [{"chain": "Z", "residue": 3, "atoms": ["C1", "C2"]}]})
    mapping = prepare_source(req.source, src, tmp_path / "source", ame=True)
    assert Path(mapping["original"]).read_text() == raw
    derived = Path(mapping["native_path"]).read_text()
    assert "CONECT    2    3" in derived
    assert len(mapping["atom_map"]) == 3
    assert mapping["atom_map"][0]["source"]["insertion_code"] == "A"
    assert mapping["atom_map"][1]["source"]["component"] == "BCA"
    for row in mapping["atom_map"]:
        assert row["coordinate_fields"] in derived
    cfg = compose_request(req, upstream, tmp_path / "compose", tmp_path / "weights", prepared_source=mapping)
    ds = cfg.generation.dataloader.dataset
    assert ds.conditional_features[0].motif_atom_spec == "B1: [CA]"
    assert ds.conditional_features[1].ligand == "L:0"
    assert ds.conditional_features[1].ligand_only is False
    assert cfg.generation.motif_target_dict_cfg.custom.contig_atoms == "B1: [CA]"
    assert ds.nres.low == 50 and ds.nres.high == 60


def test_publication_keeps_native_identity_missingness_and_no_design(tmp_path):
    native = tmp_path / "native"
    native.mkdir()
    pdb = native / "job_0_n_1_id_0_bon_orig0_r1.pdb"
    pdb.write_text(atom(1, "CA", "ALA", "B", 1, (1, 2, 3)))
    with (native / "rewards.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["pdb_path", "metadata_tag", "total_reward", "sample_type", "aatype"])
        writer.writeheader()
        writer.writerow({"pdb_path": str(pdb), "metadata_tag": "bon_orig0_r1", "total_reward": "nan", "sample_type": "final", "aatype": "0"})
    result = publish(tmp_path, {"execution": "fixture"})
    assert result["documents"][0]["kind"] == "native_document"
    assert result["documents"][0]["chains"][0]["sequence"] == "A"
    assert result["documents"][0]["native_observations"] == [{"artifact": "native/rewards.csv", "row": 0}]
    assert result["observations"][0]["missing_fields"] == ["total_reward"]
    assert result["observations"][0]["values"]["total_reward"] == "nan"
    assert json.loads((tmp_path / "complexa-results.json").read_text())["source_revision"] == SOURCE_REVISION


def test_prepare_only_and_api_discovery(upstream, tmp_path):
    import importlib.util
    from scripts.bms_complexa.run import run
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("complexa_api", root / "platform/api/services/complexa.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    view = api.discovery()
    assert set(view["profiles"]) == set(TASKS)
    assert view["native_profile_defaults"]["tasks"]["AME"]["reward_model"] is None
    assert view["schema"]["additionalProperties"] is False
    with pytest.raises(ValueError):
        api.normalize_job_request("evaluate", {"task": "AME", "task_name": "M0024_1nzy_v3"})
    request = Request(task="AME", task_name="M0024_1nzy_v3", reward=None)
    receipt = run(request, upstream=upstream, weights=tmp_path / "weights", output=tmp_path / "result", prepare_only=True)
    assert receipt["execution"] == "not_started"
    assert receipt["selected_dependencies"] == ["complexa_ame"]
    assert receipt["commands"][0][:2] == ["complexa", "generate"]
    assert not (tmp_path / "result/complexa-results.json").exists()
    assert (tmp_path / "result/native/configs/bms_complexa.yaml").exists()
