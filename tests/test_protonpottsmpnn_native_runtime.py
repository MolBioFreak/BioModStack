"""Native runtime qualification; set PROTONPOTTSMPNN_ROOT + HBPLUS_PATH.

Integration tests deliberately use the shipped checkpoint and PD-L1 structure.
No fake engine/output or scientific implementation is used.
"""
import dataclasses
import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_protonpottsmpnn_design.py"
spec = importlib.util.spec_from_file_location("proton_runtime", SCRIPT)
assert spec is not None and spec.loader is not None
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_stable_identifiers_distinguish_criteria_and_sources():
    first = runtime.design_identifier("a" * 64, 0, "native_s0")
    assert first == runtime.design_identifier("a" * 64, 0, "native_s0")
    assert first != runtime.design_identifier("a" * 64, 1, "native_s0")
    assert first != runtime.design_identifier("b" * 64, 0, "native_s0")


@pytest.fixture(scope="module")
def native():
    root = os.environ.get("PROTONPOTTSMPNN_ROOT")
    if not root:
        pytest.skip("Set PROTONPOTTSMPNN_ROOT to exercise the real shipped checkpoint")
    from mpnn.inference_engines.potts_mpnn_ph import PHDesignCriteria, PottsMPNNPHEngine
    return Path(root), PHDesignCriteria, PottsMPNNPHEngine


def criterion(cls):
    return cls(method="block_descent", backend="potts", seed_source="native",
               center_types=["HIS-P", "ASP-P", "GLU-P"],
               dep_map={"HIS-P": ["HIS-S"], "ASP-P": ["ASP-D"], "GLU-P": ["GLU-D"]},
               forbidden_tokens=["HIS-A", "ASP-A", "GLU-A", "UNK"],
               temperature=0.05, samples_per_site=1, block_size=3,
               combined_lambda=0.3, neighbour_k=16, max_mutations=20,
               repetitive_window_parents=["ARG", "LYS", "HIS", "ASP", "GLU"],
               repetitive_window_weight=1.0, record_trajectory=True)


def test_ev6_full_preparation_and_model_folds(native):
    from mpnn.transforms.ev6.predictor import EV6Predictor
    root, _, _ = native
    predictor = EV6Predictor()
    assert len(predictor._his_folds) == len(predictor._acid_folds) == 5
    calls = predictor.predict(runtime.load_structure(root / "inference/examples/pdl1_seed_binder.pdb"))
    assert len(calls) == 21
    assert calls.p_protonated.notna().all()
    assert calls.sd.notna().all()


def test_real_checkpoint_redesign_serial_parallel_and_seed_energies(native, tmp_path):
    root, cls, engine_cls = native
    pdb = root / "inference/examples/pdl1_seed_binder.pdb"
    criteria = criterion(cls)
    engine = engine_cls(checkpoint_path=str(root / runtime.CHECKPOINT_RELATIVE),
                        extended_vocab="v6", device="cpu", out_directory=None,
                        write_fasta=False, write_structures=False)
    atoms = runtime.load_structure(pdb)
    single = engine.run_ph_redesign(atom_array=atoms, binder_chain="A",
                                   criteria_list=[criteria], seed=0)
    # Repeated identical criteria used to collapse on the same native design_id.
    # The new index retains both outputs, without touching their native ids.
    serial = engine.run_ph_redesign(atom_array=atoms, binder_chain="A",
                                   criteria_list=[criteria, criteria], seed=0, n_jobs=1)
    parallel = engine.run_ph_redesign(atom_array=atoms, binder_chain="A",
                                     criteria_list=[criteria, criteria], seed=0, n_jobs=2)
    assert len(single) == 1 and len(serial) == len(parallel) == 2
    assert {d.criteria_index for d in serial} == {0, 1}
    assert len({d.design_id() for d in serial}) == 1
    assert [dataclasses.asdict(d) for d in serial] == [dataclasses.asdict(d) for d in parallel]
    assert dataclasses.asdict(single[0]) == dataclasses.asdict(next(d for d in serial if d.criteria_index == 0))
    assert all(len(d.canonical_sequence) == len(d.extended_tokens) == 114 for d in serial)
    assert all(d.energy_trajectory for d in serial)
    request = {"contract": runtime.CONTRACT, "source": {"requested_path": str(pdb), "sha256": runtime.sha256(pdb)},
               "options": {"binder_chain": "A", "seed": 0, "criteria": [dataclasses.asdict(criteria)],
                           "initial_sequences": [single[0].canonical_sequence],
                           "engine_options": {"extended_vocab": "v6", "field_source": "self_edge",
                                              "etab_source": None, "etab_hidden": None, "field_hidden": None},
                           "write_fasta": True, "write_states_fasta": True, "write_structures": False}}
    request["options"]["criteria"][0]["seed_source"] = "inverse"
    result = runtime.run(request, pdb, tmp_path, root, n_jobs=1, device="cpu")
    readback = json.loads((tmp_path / "protonpottsmpnn_design/manifest.json").read_text())
    assert readback == result
    assert readback["request"] == request
    assert readback["seed_energies"][0]["potts_energy"] is not None
    assert readback["designs"][0]["native"]["seed_idx"] == 0
    assert set(readback["designs"][0]["native"]) == set(single[0].__dataclass_fields__)
    assert not readback["runtime"]["structure_output"]["refolded_or_validated"]
    for artifact in readback["artifacts"]:
        assert (tmp_path / "protonpottsmpnn_design" / artifact).is_file()


def test_cif_native_input(native):
    root, _, _ = native
    atoms = runtime.load_structure(root / "inference/examples/pdl1_design_fold.cif")
    assert atoms.array_length() > 0
    assert "A" in atoms.chain_id
