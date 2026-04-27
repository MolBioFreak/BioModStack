from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
VALIDATOR = SCRIPT_DIR / "validate_colabfold_db_integrity.py"


def _touch_db(path: Path) -> None:
    path.write_text("payload", encoding="utf-8")
    Path(str(path) + ".dbtype").write_text("dbtype", encoding="utf-8")


def _write_index(path: Path, ids: list[int]) -> None:
    path.write_text("".join(f"{idx}\t{idx * 10}\t5\n" for idx in ids), encoding="utf-8")


def _write_db_family(root: Path, stem: str, *, target_ids: list[int], seq_ids: list[int], aln_ids: list[int]) -> None:
    for suffix, ids in (("", target_ids), ("_seq", seq_ids), ("_aln", aln_ids)):
        prefix = root / f"{stem}{suffix}"
        _touch_db(prefix)
        _write_index(Path(str(prefix) + ".index"), ids)


def _run_validator(db_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--db-root",
            str(db_root),
            "--family",
            "uniref30_2302_db",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )


def test_validate_colabfold_db_integrity_accepts_matching_alignment_and_sequence_superset(tmp_path: Path) -> None:
    _write_db_family(
        tmp_path,
        "uniref30_2302_db",
        target_ids=[0, 1, 2, 3],
        seq_ids=[0, 1, 2, 3, 4, 5, 6],
        aln_ids=[0, 1, 2, 3],
    )

    result = _run_validator(tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["compatible"] is True
    family = payload["families"][0]
    assert family["family"] == "uniref30_2302_db"
    assert family["target"]["count"] == 4
    assert family["sequence"]["count"] == 7
    assert family["alignment"]["count"] == 4
    assert family["target"]["gap_count"] == 0
    assert family["sequence"]["gap_count"] == 0
    assert family["alignment"]["gap_count"] == 0


def test_validate_colabfold_db_integrity_rejects_remapped_alignment_family(tmp_path: Path) -> None:
    _write_db_family(
        tmp_path,
        "uniref30_2302_db",
        target_ids=[0, 1, 2, 3],
        seq_ids=[0, 1, 2, 3],
        aln_ids=[0, 1, 2, 350],
    )

    result = _run_validator(tmp_path)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["compatible"] is False
    family = payload["families"][0]
    assert family["compatible"] is False
    assert family["alignment"]["max_id"] == 350
    assert family["alignment"]["gap_count"] > 0
    assert any("alignment index keyspace differs from target" in issue for issue in family["issues"])


def test_validate_colabfold_db_integrity_rejects_missing_dbtype(tmp_path: Path) -> None:
    _write_db_family(
        tmp_path,
        "uniref30_2302_db",
        target_ids=[0, 1, 2, 3],
        seq_ids=[0, 1, 2, 3],
        aln_ids=[0, 1, 2, 3],
    )
    Path(str(tmp_path / "uniref30_2302_db_aln") + ".dbtype").unlink()

    result = _run_validator(tmp_path)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["compatible"] is False
    assert any("alignment DB prefix or dbtype is missing" in issue for issue in payload["families"][0]["issues"])
