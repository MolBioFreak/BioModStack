from __future__ import annotations

import csv
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = API_ROOT.parents[1] / "scripts"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from filter_caliby import main as filter_caliby_main
from prep_caliby_antibody_constraints import main as prep_caliby_constraints_main


def _write_minimal_complex_pdb(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "ATOM      1  CA  GLY H   1       0.000   0.000   0.000  1.00 50.00           C",
                "ATOM      2  CA  SER H   2       1.000   0.000   0.000  1.00 50.00           C",
                "ATOM      3  CA  TYR H   3       2.000   0.000   0.000  1.00 50.00           C",
                "ATOM      4  CA  GLY A   1       0.000   1.000   0.000  1.00 50.00           C",
                "ATOM      5  CA  SER A   2       1.000   1.000   0.000  1.00 50.00           C",
                "END",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_prep_caliby_antibody_constraints_emits_native_columns(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    pdb_path = input_dir / "candidate_0001.pdb"
    _write_minimal_complex_pdb(pdb_path)

    output_csv = tmp_path / "caliby_constraints.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prep_caliby_antibody_constraints.py",
            "--input_dir",
            str(input_dir),
            "--out_csv",
            str(output_csv),
            "--antibody_chains",
            "H",
            "--fixed_pos_override_seq",
            "H2:A",
            "--pos_restrict_aatype",
            "H3:WY",
            "--symmetry_pos",
            "H1:H3",
        ],
    )

    prep_caliby_constraints_main()

    with output_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    row = rows[0]
    assert row["pdb_key"] == "candidate_0001"
    assert "A1-2" in row["fixed_pos_seq"]
    assert row["fixed_pos_override_seq"] == "H2:A"
    assert row["pos_restrict_aatype"] == "H3:WY"
    assert row["symmetry_pos"] == "H1:H3"


def test_filter_caliby_drops_missing_required_metrics(tmp_path: Path, monkeypatch) -> None:
    json_dir = tmp_path / "jsons"
    pdb_dir = tmp_path / "pdbs"
    out_dir = tmp_path / "filtered"
    json_dir.mkdir()
    pdb_dir.mkdir()

    (pdb_dir / "caliby_0001.pdb").write_text("END\n", encoding="utf-8")
    (json_dir / "generator_caliby_0001.json").write_text(
        '{"caliby_potts_energy": -12.0}',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "filter_caliby.py",
            "--jsons",
            str(json_dir),
            "--pdbs",
            str(pdb_dir),
            "--output-dir",
            str(out_dir),
            "--min-sc-plddt",
            "80",
        ],
    )

    filter_caliby_main()

    assert list(out_dir.glob("*.pdb")) == []
    assert list(out_dir.glob("generator_*.json")) == []


def test_filter_caliby_accepts_canonicalized_self_consistency_metrics(tmp_path: Path, monkeypatch) -> None:
    json_dir = tmp_path / "jsons"
    pdb_dir = tmp_path / "pdbs"
    out_dir = tmp_path / "filtered"
    json_dir.mkdir()
    pdb_dir.mkdir()

    (pdb_dir / "caliby_0002.pdb").write_text("END\n", encoding="utf-8")
    (json_dir / "generator_caliby_0002.json").write_text(
        '{"caliby_potts_energy": -15.0, "self_consistency": {"avg_plddt": 88.2, "ca_rmsd": 0.84}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "filter_caliby.py",
            "--jsons",
            str(json_dir),
            "--pdbs",
            str(pdb_dir),
            "--output-dir",
            str(out_dir),
            "--max-potts-energy",
            "-10",
            "--min-sc-plddt",
            "80",
            "--max-sc-rmsd",
            "1.0",
        ],
    )

    filter_caliby_main()

    assert (out_dir / "caliby_0002.pdb").exists()
    assert (out_dir / "generator_caliby_0002.json").exists()
