from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pdb_line(chain: str, number: int, icode: str, residue: str, alt: str = " ") -> str:
    return f"ATOM      1  CA {alt}{residue:3s} {chain}{number:4d}{icode}   0.000   0.000   0.000  1.00 10.00           C\n"


def test_sequence_identity_and_model_boundary(tmp_path):
    path = tmp_path / "complex.pdb"
    path.write_text("MODEL        1\n" + pdb_line("H", 100, " ", "ALA")
                    + pdb_line("H", 100, "A", "GLY") + pdb_line("H", 100, "A", "GLY", "A")
                    + pdb_line("T", 1, " ", "TYR") + "ENDMDL\nMODEL        2\n"
                    + pdb_line("H", 100, " ", "CYS") + "ENDMDL\n")
    result = load("extract_antibody_pdb_sequence").extract(path)["chains"]
    assert [(c["chain"], c["sequence"]) for c in result] == [("H", "AG"), ("T", "Y")]
    assert result[0]["residues"][1] == {"number": 100, "insertion_code": "A", "aa": "G"}
    roles = load("validate_ppiflow_roles")
    assert roles.roles(path, "H", "", "T", "T1") == "T"
    for args in [("X", "", "T", ""), ("H", "L", "T", ""),
                 ("H", "", "H", ""), ("H", "", "T", "H100A"),
                 ("H", "", "T", "T2")]:
        with pytest.raises(ValueError):
            roles.roles(path, *args)


def test_corrupt_and_empty_refuse_without_synthetic_sequence(tmp_path):
    extractor = load("extract_antibody_pdb_sequence")
    path = tmp_path / "bad.pdb"
    for text in ("", "ATOM  truncated\n", pdb_line("H", 1, " ", "UNK")):
        path.write_text(text)
        with pytest.raises(ValueError):
            extractor.extract(path)
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "extract_antibody_pdb_sequence.py"), str(path)], capture_output=True)
        assert run.returncode != 0
    workflow = (ROOT / "workflows" / "antibody_denovo.nf").read_text()
    assert 'return "AAAA"' not in workflow
    assert "extract_antibody_pdb_sequence.py" in workflow


def test_movable_identity_keeps_insertion_codes_distinct():
    # Import only the pure parser; PyRosetta is a runtime-only dependency.
    source = (ROOT / "scripts" / "prepare_ppiflow_maturation.py").read_text()
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_parse_position_spec")
    namespace: dict = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "prepare_ppiflow_maturation.py", "exec"), namespace)
    positions = namespace["_parse_position_spec"]("H100,H100A,T-1,T4-5")
    assert positions == {("H", 100, ""), ("H", 100, "A"), ("T", -1, ""),
                         ("T", 4, ""), ("T", 5, "")}
    with pytest.raises(ValueError):
        namespace["_parse_position_spec"]("H100?")


def test_ambiguous_antigen_and_role_wiring(tmp_path):
    path = tmp_path / "complex.pdb"
    path.write_text("".join(pdb_line(chain, 1, " ", "ALA") for chain in "HLTX"))
    with pytest.raises(ValueError, match="ambiguous"):
        load("validate_ppiflow_roles").roles(path, "H", "L", "")
    assert load("validate_ppiflow_roles").roles(path, "H", "L", "T", "T1") == "T"
    module = (ROOT / "modules" / "ppiflow.nf").read_text()
    assert "validate_ppiflow_roles.py" in module
    assert "chain roles disagree with requested antibody_chains" in module
    assert "antigen chain disagrees with requested antigen_chains" in module
    assert "inferredHeavy" not in module
    prepare = (ROOT / "scripts" / "prepare_ppiflow_maturation.py").read_text()
    assert "detected_chains[0]" not in prepare
