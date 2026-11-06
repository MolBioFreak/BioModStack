"""Resequence loop: fixed/designable positions are selectable and need no region manifest."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PDB = "".join(
    f"ATOM  {i:5d}  CA  {n} A{i:4d}    {3.8 * i:8.3f}   0.000   0.000  1.00 20.00           C\n"
    for i, n in ((1, "ALA"), (2, "GLY"), (3, "SER"))
) + "END\n"


def _run(script, *args):
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)], check=True,
                   capture_output=True, cwd=ROOT / "scripts")


def _src(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "d.pdb").write_text(PDB)
    return tmp_path / "in"


def test_fampnn_fixed_positions_without_manifest(tmp_path):
    _run("prep_fampnn_constraints_from_spec.py", "--input_dir", _src(tmp_path), "--fixed_positions", "A1-2",
         "--out_csv", tmp_path / "f.csv")
    assert (tmp_path / "f.csv").read_text().splitlines()[1] == '"d","A1-2",""'


def test_fampnn_default_fixes_nothing(tmp_path):
    _run("prep_fampnn_constraints_from_spec.py", "--input_dir", _src(tmp_path), "--out_csv", tmp_path / "f.csv")
    assert (tmp_path / "f.csv").read_text().splitlines()[1] == '"d","",""'


def test_fampnn_explicit_positions_win_over_manifest(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"sequence_fixed_positions_spec": "A3"}))
    _run("prep_fampnn_constraints_from_spec.py", "--input_dir", _src(tmp_path), "--manifest", manifest,
         "--fixed_positions", "A1", "--out_csv", tmp_path / "f.csv")
    assert '"A1"' in (tmp_path / "f.csv").read_text().splitlines()[1]


def _fixed(path):
    header = path.read_text().splitlines()[0]
    return json.loads(header.split("REMARK BMS_MPNN_ROLES ", 1)[1])["fixed_positions"]


def test_mpnn_designable_positions_fix_the_rest(tmp_path):
    _run("add_fixed_labels_from_spec.py", "--input_dir", _src(tmp_path), "--output_dir", tmp_path / "o",
         "--designable_positions", "A3")
    assert _fixed(tmp_path / "o" / "d.pdb") == [["A", 1, ""], ["A", 2, ""]]


def test_mpnn_default_designs_every_residue(tmp_path):
    _run("add_fixed_labels_from_spec.py", "--input_dir", _src(tmp_path), "--output_dir", tmp_path / "o")
    assert _fixed(tmp_path / "o" / "d.pdb") == []
