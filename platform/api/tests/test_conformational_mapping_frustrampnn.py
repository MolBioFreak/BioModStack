from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import AA_ORDER
from services.conformational_mapping.frustration import finalize_landscape, score_class
from services.conformational_mapping.import_snapshot import build_import_snapshot_from_mmcif
from services.conformational_mapping.structure_normalizer import normalize_conformational_mapping_structure


def _structure_map() -> dict:
    return {
        "target_id": "t", "candidate_id": "c", "rows": [{
            "entity_instance_id": "copy1", "auth_asym_id": "AUTH", "auth_seq_id": 7,
            "insertion_code": "A", "sequence_index": 1, "residue_name": "ALA",
            "pdb_chain_id": "P", "pdb_residue_id": 3, "pdb_insertion_code": "",
            "backbone_atoms": {"N": "1", "CA": "2", "C": "3", "O": "4"}, "status": "mapped",
        }],
    }


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["chain", "position", "insertion_code", "wt", "mutation", "score"])
        writer.writeheader()
        writer.writerows(rows)


def _rows() -> list[dict]:
    return [{"chain": "P", "position": 0, "insertion_code": "", "wt": "A", "mutation": aa, "score": index / 10} for index, aa in enumerate(AA_ORDER)]


def _finalize(path: Path, rows: list[dict] | None = None) -> dict:
    _write(path, rows or _rows())
    return finalize_landscape(path, _structure_map(), checkpoint_id="ckpt", checkpoint_sha256="a" * 64, tool_id="FrustraMPNN", tool_sha256="b" * 64)


def test_real_1ubq_frustrampnn_output_replays_through_authoritative_map(tmp_path: Path) -> None:
    fixture_root = (
        Path(__file__).parent / "fixtures" / "conformational_mapping" / "real_1ubq"
    )
    source = fixture_root / "1UBQ.protein-only-authoritative.cif"
    raw = fixture_root / "frustrampnn.csv"
    snapshot = build_import_snapshot_from_mmcif(
        source.read_bytes(),
        target_id="1ubq-real",
        candidate_id="cm_imp_1ubq_real_000000_deadbeef",
        original_source_path="registered_import/1UBQ.cif",
    )
    structure_map = normalize_conformational_mapping_structure(
        input_path=source,
        output_pdb_path=tmp_path / "1ubq.pdb",
        map_path=tmp_path / "1ubq.cm_structure_map_v1.json",
        target_id="1ubq-real",
        candidate_id="cm_imp_1ubq_real_000000_deadbeef",
        complex_snapshot=snapshot,
    )
    landscape = finalize_landscape(
        raw, structure_map,
        checkpoint_id="megascale.ckpt",
        checkpoint_sha256="a" * 64,
        tool_id="FrustraMPNN",
        tool_sha256="b" * 64,
    )
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == "2084353640cbe5f06847bc78c0787f1062edb2c891d3808adfe2d6aa57b0fa36"
    assert len(landscape["residues"]) == 76
    assert all(len(residue["slots"]) == 20 for residue in landscape["residues"])
    assert landscape["input_issues"] == []


def test_cm7_001_selected_chain_dispatch(tmp_path: Path) -> None:
    result = _finalize(tmp_path / "raw.csv")
    assert result["residues"][0]["auth_asym_id"] == "AUTH"
    assert not result["input_issues"]


def test_cm7_002_exact_twenty_unique_slots(tmp_path: Path) -> None:
    slots = _finalize(tmp_path / "raw.csv")["residues"][0]["slots"]
    assert [slot["mutation_aa"] for slot in slots] == list(AA_ORDER)
    assert len({slot["mutation_aa"] for slot in slots}) == 20


def test_cm7_003_exactly_one_native_slot(tmp_path: Path) -> None:
    slots = _finalize(tmp_path / "raw.csv")["residues"][0]["slots"]
    assert [slot["mutation_aa"] for slot in slots if slot["native"]] == ["A"]


@pytest.mark.parametrize("replacement,status", [
    ([{"chain": "P", "position": 0, "insertion_code": "", "wt": "A", "mutation": "V", "score": "nan"}], "nonfinite_score"),
    ([{"chain": "P", "position": "bad", "insertion_code": "", "wt": "A", "mutation": "V", "score": 1}], "malformed_row"),
])
def test_cm7_004_duplicate_malformed_nonfinite_fail(tmp_path: Path, replacement: list[dict], status: str) -> None:
    result = _finalize(tmp_path / "raw.csv", replacement)
    assert status in {slot["status"] for slot in result["residues"][0]["slots"]} | {issue["status"] for issue in result["input_issues"]}
    duplicate = _rows() + [_rows()[0]]
    result = _finalize(tmp_path / "duplicate.csv", duplicate)
    assert result["residues"][0]["slots"][0]["status"] == "duplicate_row"


def test_cm7_005_missingness_statuses(tmp_path: Path) -> None:
    result = _finalize(tmp_path / "raw.csv", _rows()[:1])
    assert sum(slot["status"] == "missing_row" for slot in result["residues"][0]["slots"]) == 19
    assert all(slot["score"] is None for slot in result["residues"][0]["slots"][1:])


def test_cm7_006_threshold_boundaries() -> None:
    assert score_class(-1.0) == "high"
    assert score_class(-0.999) == "neutral"
    assert score_class(0.579) == "neutral"
    assert score_class(0.58) == "minimally_frustrated"


def test_cm7_007_raw_csv_retained(tmp_path: Path) -> None:
    raw = tmp_path / "raw.csv"
    result = _finalize(raw)
    assert result["raw_csv_sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()


def test_cm7_008_mapping_join_to_source(tmp_path: Path) -> None:
    residue = _finalize(tmp_path / "raw.csv")["residues"][0]
    assert (residue["entity_instance_id"], residue["auth_asym_id"], residue["auth_seq_id"], residue["sequence_index"]) == ("copy1", "AUTH", 7, 1)


def test_cm7_009_semantic_limit_metadata(tmp_path: Path) -> None:
    result = _finalize(tmp_path / "raw.csv")
    assert result["checkpoint_id"] == "ckpt"
    assert result["checkpoint_sha256"] == "a" * 64
    assert result["tool_id"] == "FrustraMPNN"
    assert result["threshold_policy_id"] == "frustrampnn_class_v1"


def test_cm7_010_1ubq_zero_based_positions_map_to_authoritative_residues_1_through_76(
    tmp_path: Path,
) -> None:
    sequence = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
    three = {
        "A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE",
        "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS", "L": "LEU",
        "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG",
        "S": "SER", "T": "THR", "V": "VAL", "W": "TRP", "Y": "TYR",
    }
    structure_map = {
        "target_id": "1UBQ",
        "candidate_id": "cm_imp_1ubq",
        "rows": [
            {
                "entity_instance_id": "A",
                "auth_asym_id": "A",
                "auth_seq_id": index + 1,
                "insertion_code": "",
                "sequence_index": index + 1,
                "residue_name": three[wt],
                "pdb_chain_id": "A",
                "pdb_residue_id": index + 1,
                "pdb_insertion_code": "",
                "backbone_atoms": {"N": "1", "CA": "2", "C": "3", "O": "4"},
                "status": "mapped",
            }
            for index, wt in enumerate(sequence)
        ],
    }
    raw_rows = [
        {
            "chain": "A",
            "position": index,
            "insertion_code": "",
            "wt": wt,
            "mutation": mutation,
            "score": (mutation_index - 10) / 10,
        }
        for index, wt in enumerate(sequence)
        for mutation_index, mutation in enumerate(AA_ORDER)
    ]
    raw = tmp_path / "1ubq.raw.csv"
    _write(raw, raw_rows)
    result = finalize_landscape(
        raw,
        structure_map,
        checkpoint_id="installed-megascale",
        checkpoint_sha256="a" * 64,
        tool_id="FrustraMPNN",
        tool_sha256="b" * 64,
    )

    assert len(result["residues"]) == 76
    assert [row["auth_seq_id"] for row in result["residues"]] == list(range(1, 77))
    assert sum(len(row["slots"]) for row in result["residues"]) == 76 * 20
    assert all(slot["status"] == "ok" for row in result["residues"] for slot in row["slots"])
    assert result["input_issues"] == []


def test_zero_based_positions_do_not_shift_after_unscoreable_rows_or_across_chains(
    tmp_path: Path,
) -> None:
    def mapped_row(chain: str, residue: int, wt: str, *, status: str = "mapped") -> dict:
        return {
            "entity_instance_id": chain,
            "auth_asym_id": chain,
            "auth_seq_id": residue,
            "insertion_code": "",
            "sequence_index": residue,
            "residue_name": {"A": "ALA", "G": "GLY", "V": "VAL"}[wt],
            "pdb_chain_id": chain,
            "pdb_residue_id": residue,
            "pdb_insertion_code": "",
            "backbone_atoms": {"N": "1", "CA": "2", "C": "3", "O": "4"},
            "status": status,
        }

    structure_map = {
        "target_id": "multi",
        "candidate_id": "candidate",
        "rows": [
            mapped_row("A", 1, "A", status="mapping_failed"),
            mapped_row("A", 2, "G"),
            mapped_row("B", 10, "V"),
        ],
    }
    raw_rows = [
        {"chain": chain, "position": position, "insertion_code": "", "wt": wt,
         "mutation": mutation, "score": mutation_index / 10}
        for chain, position, wt in (("A", 1, "G"), ("B", 0, "V"))
        for mutation_index, mutation in enumerate(AA_ORDER)
    ]
    raw = tmp_path / "multi.raw.csv"
    _write(raw, raw_rows)
    result = finalize_landscape(
        raw,
        structure_map,
        checkpoint_id="checkpoint",
        checkpoint_sha256="a" * 64,
        tool_id="FrustraMPNN",
        tool_sha256="b" * 64,
    )

    assert [(row["auth_asym_id"], row["auth_seq_id"]) for row in result["residues"]] == [
        ("A", 2),
        ("B", 10),
    ]
    assert all(slot["status"] == "ok" for row in result["residues"] for slot in row["slots"])
    assert result["input_issues"] == []
