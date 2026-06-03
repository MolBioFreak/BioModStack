from __future__ import annotations

import pickle
from pathlib import Path

from services.rfantibody_metadata import load_rfantibody_trb_summary


def _atom_line(serial: int, chain: str, residue: int) -> str:
    return (
        f"ATOM  {serial:5d}  CA  ALA {chain}{residue:4d}    "
        f"{float(serial):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 20.00           C\n"
    )


def test_rfantibody_trb_summary_reports_modifiable_residue_confidence_scope(tmp_path: Path) -> None:
    pdb_path = tmp_path / "design_0.pdb"
    pdb_path.write_text(
        "".join(
            [
                "REMARK PDBinfo-LABEL: 2 H1\n",
                "REMARK PDBinfo-LABEL: 3 H1\n",
                _atom_line(1, "T", 1),
                _atom_line(2, "T", 2),
                _atom_line(3, "H", 1),
                _atom_line(4, "H", 2),
                _atom_line(5, "H", 3),
                _atom_line(6, "H", 4),
                "END\n",
            ]
        ),
        encoding="utf-8",
    )
    trb_path = pdb_path.with_suffix(".trb")
    with trb_path.open("wb") as handle:
        pickle.dump(
            {
                "plddt": [100.0, 100.0, 100.0, 91.0, 93.0, 100.0],
                "config": {"antibody": {"design_loops": ["H1"]}},
            },
            handle,
        )

    summary = load_rfantibody_trb_summary(pdb_path)

    assert summary["rfa_plddt_all_residue"] == 97.33333333333333
    assert summary["rfa_plddt_modifiable"] == 92.0
    assert summary["rfa_plddt_primary"] == 92.0
    assert summary["rfa_plddt_selected"] == 92.0
    assert summary["rfa_plddt_nonmodifiable"] == 100.0
    assert summary["rfa_plddt_framework"] == 100.0
    assert summary["rfa_plddt_target"] == 100.0
    assert summary["plddt_overall"] == 97.33333333333333
    assert summary["rfa_plddt_final"] == 97.33333333333333
    assert summary["rfa_modifiable_residues"] == [
        {"chain_id": "H", "residue_number": 2, "insertion_code": "", "loop_id": "H1"},
        {"chain_id": "H", "residue_number": 3, "insertion_code": "", "loop_id": "H1"},
    ]
    assert summary["rfa_modifiable_ranges"] == [
        {"chain_id": "H", "start_residue_number": 2, "end_residue_number": 3, "label": "H1"},
    ]
    scope = summary["rfa_confidence_scope"]
    assert scope["primary_scope"] == "modifiable_residues"
    assert scope["status"] == "ok"
    assert scope["counts"] == {
        "all_residue_count": 6,
        "modifiable_residue_count": 2,
        "nonmodifiable_residue_count": 4,
        "framework_residue_count": 2,
        "target_residue_count": 2,
    }
    assert scope["plddt"]["primary"] == 92.0
    assert scope["plddt"]["all_residue"] == 97.33333333333333


def test_rfantibody_trb_summary_falls_back_to_all_residue_scope_when_no_design_loop_mapping(tmp_path: Path) -> None:
    pdb_path = tmp_path / "design_no_scope.pdb"
    pdb_path.write_text(
        "".join([_atom_line(1, "H", 1), _atom_line(2, "H", 2), "END\n"]),
        encoding="utf-8",
    )
    with pdb_path.with_suffix(".trb").open("wb") as handle:
        pickle.dump({"plddt": [88.0, 92.0], "config": {"antibody": {}}}, handle)

    summary = load_rfantibody_trb_summary(pdb_path)

    assert summary["rfa_plddt_all_residue"] == 90.0
    assert summary["rfa_plddt_primary"] == 90.0
    assert summary["rfa_plddt_modifiable"] is None
    assert summary["rfa_confidence_scope"]["primary_scope"] == "all_residues"
    assert summary["rfa_confidence_scope"]["status"] == "no_modifiable_scope"
