from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = API_ROOT.parents[1] / "scripts"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from filter_caliby import main as filter_caliby_main
from caliby_runtime import normalize_sampling_results, remap_constraint_dataframe_to_cleaned_paths
from prep_caliby_binder_constraints import build_constraints
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


def test_binder_constraints_lock_exact_nonbinder_chains(tmp_path: Path) -> None:
    _write_minimal_complex_pdb(tmp_path / "candidate.pdb")
    rows = build_constraints(tmp_path, "H", "A", "H2")
    assert len(rows) == 1
    assert rows[0]["fixed_pos_seq"] == "A1-2,H1-1,H3-3"
    assert rows[0]["fixed_pos_scn"] == rows[0]["fixed_pos_seq"]
    assert build_constraints(tmp_path, "H", "A")[0]["fixed_pos_seq"] == "A1-2"
    with pytest.raises(ValueError, match="absent"):
        build_constraints(tmp_path, "B", "A")
    with pytest.raises(ValueError, match="overlap"):
        build_constraints(tmp_path, "H", "H")
    with pytest.raises(ValueError, match="design positions"):
        build_constraints(tmp_path, "H", "A", "H99")
    with pytest.raises(ValueError, match="invalid"):
        build_constraints(tmp_path, "H", "A", "H2,garbage")


def test_binder_constraints_reject_unassigned_or_ambiguous_chain_numbers(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.pdb"
    _write_minimal_complex_pdb(candidate)
    with candidate.open("a", encoding="utf-8") as handle:
        handle.write("ATOM      6  CA  GLY B   1       0.000   2.000   0.000  1.00 50.00           C\n")
    with pytest.raises(ValueError, match="explicit binder or target role"):
        build_constraints(tmp_path, "H", "A")
    assert build_constraints(tmp_path, "H", "A,B", "H2")[0]["fixed_pos_seq"] == "A1-2,B1-1,H1-1,H3-3"
    with pytest.raises(ValueError, match="range"):
        build_constraints(tmp_path, "H", "A,B", "H3-2")
    candidate.write_text(candidate.read_text().replace(" H   ", " 1   "), encoding="utf-8")
    assert build_constraints(tmp_path, "1", "A,B", "12")[0]["fixed_pos_seq"] == "11-1,13-3,A1-2,B1-1"
    candidate.write_text(candidate.read_text().replace("SER 1   2", "SER 1   2A"), encoding="utf-8")
    with pytest.raises(ValueError, match="insertion codes"):
        build_constraints(tmp_path, "1", "A,B", "12")


def test_binder_module_requires_roles_and_does_not_force_off_native_controls() -> None:
    module = (API_ROOT.parents[1] / "modules/caliby.nf").read_text(encoding="utf-8")
    binder = module.split("process RunCalibyBinder {", 1)[1].split("process FilterCaliby {", 1)[0]
    assert "explicit binder_chains and target_chains" in binder
    assert "--design-positions" in binder
    assert "--run-self-consistency-eval false" not in binder
    assert "--self-consistency-use-multimer" in binder
    assert "--sampling-overrides-json" in binder
    assert "set -euo pipefail" in binder
    assert 'path("results/native_outputs/**/*"), emit: native_outputs, optional: true' in binder
    assert 'pattern: "results/native_outputs/**/*"' in binder


def test_caliby_cardinality_and_chain_rename_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "candidate.pdb"
    _write_minimal_complex_pdb(source)
    kwargs = dict(output_pdb_dir=tmp_path, output_meta_dir=tmp_path,
                  prefix="caliby", source="caliby", stage_mode="sequence_design")
    with pytest.raises(ValueError, match="paired"):
        normalize_sampling_results(results={"example_id": ["candidate"], "out_pdb": []}, **kwargs)
    with pytest.raises(ValueError, match="cardinality"):
        normalize_sampling_results(results={"example_id": ["candidate"], "out_pdb": [str(source)],
                                            "seq": ["A", "B"]}, **kwargs)
    with pytest.raises(ValueError, match="missing output structure"):
        normalize_sampling_results(results={"example_id": ["candidate"],
                                            "out_pdb": [str(tmp_path / "absent.pdb")]}, **kwargs)
    with pytest.raises(ValueError, match="reused an output structure"):
        normalize_sampling_results(results={"example_id": ["candidate_1", "candidate_2"],
                                            "out_pdb": [str(source), str(source)]}, **kwargs)
    cleaned = tmp_path / "cleaned"
    cleaned.mkdir()
    changed = cleaned / "candidate.pdb"
    changed.write_text(source.read_text().replace(" GLY H", " GLY B").replace(" SER H", " SER B")
                       .replace(" TYR H", " TYR B"))
    import pandas as pd
    constraints = pd.DataFrame([{"pdb_key": "candidate", "fixed_pos_seq": "A1-2"}])
    with pytest.raises(ValueError, match="changed chain IDs"):
        remap_constraint_dataframe_to_cleaned_paths(constraints, original_paths=[str(source)],
                                                     cleaned_paths=[str(changed)])


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


def test_normalized_caliby_sidecar_owns_sequence_design_review_and_lineage(tmp_path: Path) -> None:
    source_pdb = tmp_path / "source.pdb"
    source_pdb.write_text("END\n", encoding="utf-8")

    manifest = normalize_sampling_results(
        results={
            "example_id": ["rfantibody_0007"],
            "out_pdb": [str(source_pdb)],
            "seq": ["QVQLV"],
            "U": [-14.2],
            "input_seq": ["XXXXX"],
        },
        output_pdb_dir=tmp_path / "pdbs",
        output_meta_dir=tmp_path / "metadata",
        prefix="caliby",
        source="caliby",
        stage_mode="sequence_design",
        extra_metadata={"caliby_model": "soluble_caliby_v1"},
    )

    metadata = json.loads(Path(manifest[0]["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["source_backbone_id"] == "rfantibody_0007"
    assert metadata["artifact_class"] == "sequence_designed_complex"
    assert metadata["result_set"] == "sequence_designs"
    assert metadata["review_profile_id"] == "sequence_design_v1"
    assert metadata["review_contract_source"] == "producer"
    assert metadata["review_artifact_manifest"]["schema"] == "bms.review-artifacts.v1"
    assert metadata["score_family"] == "caliby"
    assert metadata["selection_metric"] == "caliby_potts_energy"
    assert metadata["selection_direction"] == "lower_is_better"
    assert metadata["af3score_used"] is False
    native = metadata["native_output"]
    assert native == {
        "producer": "caliby", "example_id": "rfantibody_0007",
        "filename": "source.pdb", "path": "native_outputs/caliby_0001/source.pdb",
    }
    import shutil
    returned = tmp_path / "returned"
    shutil.copytree(tmp_path / "metadata", returned)
    source_pdb.unlink()
    shutil.rmtree(tmp_path / "metadata")
    assert (returned / native["path"]).read_text() == "END\n"
    # Native PDB originals must not become additional candidate Designs.
    assert list(returned.glob("*.pdb")) == []
