"""Position handling of scripts/run_resequence_designer.py (no model needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import run_resequence_designer as r  # noqa: E402

PDB = "".join(f"ATOM  {i:5d}  CA  ALA A{i:4d}    {3.8*i:8.3f}   0.000   0.000  1.00 20.00           C\n" for i in range(1, 6))


def test_expand_ranges_and_single_residues():
    assert r.expand("A1-3,B5") == ["A1", "A2", "A3", "B5"]
    with pytest.raises(ValueError):
        r.expand("nonsense")


def test_complement_fixes_everything_not_designable(tmp_path):
    p = tmp_path / "d.pdb"
    p.write_text(PDB)
    assert r.complement(p, r.expand("A2-3")) == ["A1", "A4", "A5"]


import pytest  # noqa: E402
