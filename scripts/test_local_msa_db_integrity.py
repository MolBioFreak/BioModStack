from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_msa.db_integrity import validate_alignment_index_keyspace  # noqa: E402


def _write_index(path: Path, ids: list[int]) -> None:
    path.write_text("".join(f"{idx}\t{idx * 10}\t5\n" for idx in ids), encoding="utf-8")


def _touch_db(path: Path) -> None:
    path.write_text("payload", encoding="utf-8")
    Path(str(path) + ".dbtype").write_text("dbtype", encoding="utf-8")


def test_validate_alignment_index_keyspace_accepts_matching_contiguous_ids(tmp_path: Path) -> None:
    target = tmp_path / "uniref30_2302_db"
    aln = tmp_path / "uniref30_2302_db_aln"
    _touch_db(target)
    _touch_db(aln)
    _write_index(Path(str(target) + ".index"), [0, 1, 2, 3])
    _write_index(Path(str(aln) + ".index"), [0, 1, 2, 3])

    result = validate_alignment_index_keyspace(target, aln)

    assert result.compatible is True
    assert result.reason == "alignment DB index keyspace matches target DB index sample"


def test_validate_alignment_index_keyspace_rejects_remapped_alignment_keyspace(tmp_path: Path) -> None:
    target = tmp_path / "uniref30_2302_db"
    aln = tmp_path / "uniref30_2302_db_aln"
    _touch_db(target)
    _touch_db(aln)
    _write_index(Path(str(target) + ".index"), [0, 1, 2, 3])
    _write_index(Path(str(aln) + ".index"), [0, 1, 2, 350])

    result = validate_alignment_index_keyspace(target, aln, sample_limit=3)

    assert result.compatible is False
    assert "last id differs" in result.reason
    assert result.target.last_id == 3
    assert result.alignment.last_id == 350


def test_validate_alignment_index_keyspace_rejects_missing_alignment_dbtype(tmp_path: Path) -> None:
    target = tmp_path / "uniref30_2302_db"
    aln = tmp_path / "uniref30_2302_db_aln"
    _touch_db(target)
    aln.write_text("payload", encoding="utf-8")
    _write_index(Path(str(target) + ".index"), [0, 1, 2, 3])
    _write_index(Path(str(aln) + ".index"), [0, 1, 2, 3])

    result = validate_alignment_index_keyspace(target, aln)

    assert result.compatible is False
    assert result.reason == "alignment DB prefix or dbtype is missing"
