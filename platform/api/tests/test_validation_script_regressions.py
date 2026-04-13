from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
PREP_BOLTZ_BATCH = REPO_ROOT / "scripts" / "prep_boltz_batch.py"
NO_MSA = REPO_ROOT / "lib" / "NO_MSA"


def _write_minimal_complex_pdb(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "ATOM      1  CA  ALA H   1      11.104  13.207   9.599  1.00 20.00           C",
                "ATOM      2  CA  GLY H   2      12.204  13.807  10.199  1.00 20.00           C",
                "ATOM      3  CA  TYR T   1      18.104  16.207  12.599  1.00 20.00           C",
                "ATOM      4  CA  SER T   2      19.204  16.807  13.199  1.00 20.00           C",
                "TER",
                "END",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_prep_boltz_batch_marks_all_chains_empty_when_no_msa(tmp_path: Path) -> None:
    pdb_path = tmp_path / "candidate_001.pdb"
    out_dir = tmp_path / "yamls"
    _write_minimal_complex_pdb(pdb_path)

    subprocess.run(
        [
            sys.executable,
            str(PREP_BOLTZ_BATCH),
            "--pdb_files",
            str(pdb_path),
            "--msa_path",
            str(NO_MSA),
            "--out_dir",
            str(out_dir),
            "--binder_chains",
            "H",
            "--target_chains",
            "T",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = yaml.safe_load((out_dir / "candidate_001.yaml").read_text(encoding="utf-8"))
    proteins = [entry["protein"] for entry in payload["sequences"]]

    assert proteins
    assert all(protein.get("msa") == "empty" for protein in proteins)
