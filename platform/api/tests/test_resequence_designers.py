"""Loop designers: templates come from each model owner; caller edits are validated by it."""
import pytest

from services.resequence_designers import build_template


def test_ligandmpnn_template_is_model_owned_and_writes_structures():
    t = build_template("ligandmpnn", mode="metal_aware", design_chains="A", settings={"batch_size": 3})
    assert t["mode"] == "metal_aware" and t["options"]["batch_size"] == 3 and t["write_structures"] is True


def test_ligandmpnn_rejects_fields_the_loop_owns():
    with pytest.raises(ValueError):
        build_template("ligandmpnn", mode=None, design_chains="A", settings={"designed_residues": ["A1"]})
    with pytest.raises(ValueError):
        build_template("ligandmpnn", mode="interface_context", design_chains="A", settings={})


def test_caliby_template_drops_the_per_design_structure_list():
    t = build_template("caliby", mode=None, design_chains="A", settings={"num_seqs_per_pdb": 5})
    assert t["num_seqs_per_pdb"] == 5 and "ensembles" not in t and t["task"] == "ensemble_design"
    with pytest.raises(ValueError):
        build_template("caliby", mode="sidechain_pack", design_chains="A", settings={})


def test_protonpotts_uses_first_chain_as_binder_and_validates_options():
    t = build_template("protonpottsmpnn", mode=None, design_chains="B,C", settings={})
    assert t["options"]["binder_chain"] == "B"
    with pytest.raises(Exception):
        build_template("protonpottsmpnn", mode=None, design_chains="A", settings={"not_an_option": 1})


def test_unknown_engine_is_refused():
    with pytest.raises(ValueError):
        build_template("fampnn", mode=None, design_chains="A", settings={})
