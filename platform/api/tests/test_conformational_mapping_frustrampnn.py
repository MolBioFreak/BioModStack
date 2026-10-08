from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from services.conformational_mapping.contracts import AA_ORDER
from services.conformational_mapping.frustration import finalize_landscape, score_class


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
    return [{"chain": "P", "position": 3, "insertion_code": "", "wt": "A", "mutation": aa, "score": index / 10} for index, aa in enumerate(AA_ORDER)]


def _finalize(path: Path, rows: list[dict] | None = None) -> dict:
    _write(path, rows or _rows())
    return finalize_landscape(path, _structure_map(), checkpoint_id="ckpt", checkpoint_sha256="a" * 64, tool_id="FrustraMPNN", tool_sha256="b" * 64)


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
    ([{"chain": "P", "position": 3, "insertion_code": "", "wt": "A", "mutation": "V", "score": "nan"}], "nonfinite_score"),
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
