"""Closed standalone request and portable readback contracts (no inference)."""
import json

import pytest
from pydantic import ValidationError

from services.caliby_native import REQUEST, normalize_request, read_native_results, selected_assets


def test_ensemble_order_false_zero_empty_and_round_trip():
    params = {"ensembles": [{"ensemble_id": "group A", "states": [
        {"state_id": "primary", "path": "/one/same.cif", "fixed_pos_seq": "a1-3"},
        {"state_id": "other", "path": "/two/same.cif"}]}],
        "num_workers": 0, "omit_aas": [], "verbose": False,
        "use_primary_res_type": False, "gaussian_noise_std": 0}
    value = normalize_request("ensemble_design", params)
    assert value == normalize_request("ensemble_design", value)
    assert value["ensembles"][0]["states"][0]["state_id"] == "primary"
    assert value["omit_aas"] == [] and value["num_workers"] == 0
    assert value["use_primary_res_type"] is False
    assert REQUEST.validate_json(json.dumps(value)).model_dump() == value


def test_packing_does_not_accept_redesign_controls():
    params = {"structures": [{"state_id": "s", "path": "/s.cif"}]}
    result = normalize_request("sidechain_pack", params)
    assert selected_assets("sidechain_pack", result)["checkpoint"] == "caliby/caliby_packer_010.ckpt"
    for key, value in [("temperature", 0.1), ("omit_aas", []), ("num_seqs_per_pdb", 5),
                       ("model_name", "soluble_caliby_v1"), ("sampling_overrides", {})]:
        with pytest.raises(ValidationError):
            normalize_request("sidechain_pack", {**params, key: value})


def test_legacy_mode_and_conflicting_task_not_reinterpreted():
    with pytest.raises(ValueError):
        normalize_request("design", {"task": "ensemble_design", "conformer_dir": "/old"})
    with pytest.raises(ValueError):
        normalize_request("sidechain_pack", {"task": "ensemble_design"})


def test_state_identity_is_explicit_not_a_basename():
    with pytest.raises(ValidationError):
        normalize_request("ensemble_design", {"ensembles": [{"ensemble_id": "g", "states": [
            {"state_id": "s", "path": "one.cif"}, {"state_id": "s", "path": "two.cif"}]}]})


def test_native_readback_zero_yield_and_missing_metrics(tmp_path):
    output = tmp_path / "native/packed_samples/packed_0.cif"
    output.parent.mkdir(parents=True)
    output.write_text("data_inert\n")
    payload = {"records": [{"structure_path": output.relative_to(tmp_path).as_posix(),
                            "native": {"example_id": "0"}}]}
    (tmp_path / "caliby_results.json").write_text(json.dumps(payload))
    assert read_native_results(tmp_path) == payload
    assert "U" not in read_native_results(tmp_path)["records"][0]["native"]
    payload["records"] = []
    (tmp_path / "caliby_results.json").write_text(json.dumps(payload))
    assert read_native_results(tmp_path)["records"] == []


def test_materialization_and_registry_cover_closed_contract(tmp_path):
    from pathlib import Path
    import yaml
    from services.caliby_native import EnsembleDesign, SidechainPack, materialize_request

    source = tmp_path / "source.cif"
    source.write_text("data_INERT\n")
    directory = tmp_path / "prepared"
    prepared = materialize_request("sidechain_pack", {"structures": [{"state_id": "s", "path": str(source)}], "scn_step_scale": 0}, directory)
    assert prepared["effective"]["scn_step_scale"] == 0
    assert (directory / prepared["effective"]["structures"][0]["path"]).read_bytes() == source.read_bytes()
    registry = yaml.safe_load((Path(__file__).resolve().parents[1] / "config/models/caliby_experimental.yaml").read_text())
    modes = {mode["id"]: mode for mode in registry["modes"]}
    assert set(modes) == {"ensemble_design", "sidechain_pack"}
    for mode, cls in [("ensemble_design", EnsembleDesign), ("sidechain_pack", SidechainPack)]:
        assert set(modes[mode]["params"]) == set(cls.model_fields) - {"schema_version", "task"}


def test_native_readback_rejects_path_escape(tmp_path):
    (tmp_path / "caliby_results.json").write_text(json.dumps({"records": [{"structure_path": "../outside.cif"}]}))
    with pytest.raises(ValueError):
        read_native_results(tmp_path)
