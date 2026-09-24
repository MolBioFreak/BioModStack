import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ppiflow_coordinate_changes.py"
spec = importlib.util.spec_from_file_location("ppiflow_coordinate_changes", SCRIPT)
changes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(changes)


def test_repack_off_does_not_report_shell_as_changed():
    atoms = {("H", 10, "A", "TYR", "CA"): (1., 2., 3.),
             ("T", 10, "", "ALA", "CA"): (3., 2., 1.)}
    assert changes.changed_residues(atoms, dict(atoms)) == []


def test_only_actual_changed_chain_residue_and_insertion_code_reported():
    before = {("H", 10, "A", "TYR", "CA"): (1., 2., 3.),
              ("H", 10, "B", "GLY", "CA"): (4., 5., 6.),
              ("T", 10, "", "ALA", "CA"): (7., 8., 9.)}
    after = dict(before)
    after[("H", 10, "B", "GLY", "CA")] = (4.1, 5., 6.)
    assert changes.changed_residues(before, after) == [
        {"chain": "H", "number": 10, "insertion_code": "B"}
    ]


@pytest.mark.parametrize("after", [{}, {("L", 10, "", "TYR", "CA"): (1., 2., 3.)}])
def test_role_or_atom_inventory_change_rejected(after):
    before = {("H", 10, "", "TYR", "CA"): (1., 2., 3.)}
    with pytest.raises(ValueError, match="identity"):
        changes.changed_residues(before, after)


def test_post_validation_maturation_never_inherits_validation():
    workflow = (SCRIPT.parents[1] / "workflows" / "antibody_denovo.nf").read_text()
    block = workflow[workflow.index('validated_structures = CollectValidatedMaturationOutputs.out.manifest'):]
    assert "meta.validation_status = 'unvalidated'" in block
    assert "meta.terminal_producer = 'ppiflow_maturation_post_validation'" in block
    assert "meta.source_meta = sourceMeta" in block


def test_post_flow_sequence_redesign_requires_explicit_selection():
    core = (SCRIPT.parents[1] / "workflows" / "maturation_child_core.nf").read_text()
    child = (SCRIPT.parents[1] / "workflows" / "maturation_child.nf").read_text()
    assert "def runRedesign = (params.maturation_redesign_enabled == true)" in core
    assert "def redesign_enabled = params.maturation_redesign_enabled == true" in core
    assert "params.maturation_redesign_enabled = false" in child
