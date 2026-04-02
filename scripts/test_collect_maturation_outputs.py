from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parent

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from collect_maturation_outputs import collect_files, is_final_ppiflow_pdb


def test_is_final_ppiflow_pdb_accepts_redesign_outputs() -> None:
    assert is_final_ppiflow_pdb(Path("demo_ppiflow_sample0.pdb"))
    assert is_final_ppiflow_pdb(Path("demo_ppiflow_seq_7_sample0.pdb"))
    assert not is_final_ppiflow_pdb(Path("demo_enriched_complex.pdb"))
    assert not is_final_ppiflow_pdb(Path("demo_other_model.pdb"))


def test_collect_files_picks_up_redesign_ppiflow_pdbs(tmp_path: Path, monkeypatch) -> None:
    child_dir = tmp_path / "child"
    results_dir = child_dir / "run" / "ppiflow" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "demo_enriched_complex.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")
    (results_dir / "demo_ppiflow_seq_7_sample0.pdb").write_text("MODEL\nENDMDL\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    collected = collect_files(
        [str(child_dir)],
        patterns=["*ppiflow*.pdb"],
        subdirs=["run/ppiflow/results"],
        predicate=is_final_ppiflow_pdb,
    )

    assert collected == ["demo_ppiflow_seq_7_sample0.pdb"]
    assert (tmp_path / "demo_ppiflow_seq_7_sample0.pdb").exists()
