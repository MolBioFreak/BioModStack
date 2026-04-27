from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IndexKeyspaceScan:
    index_path: str
    exists: bool
    count: int
    min_id: int | None
    max_id: int | None
    first_id: int | None
    last_id: int | None
    gap_count: int
    duplicate_or_unsorted_count: int
    non_numeric_lines: int
    strictly_increasing: bool
    contiguous_from_zero: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DbFamilyIntegrityReport:
    family: str
    target: IndexKeyspaceScan
    sequence: IndexKeyspaceScan
    alignment: IndexKeyspaceScan
    target_db_ready: bool
    sequence_db_ready: bool
    alignment_db_ready: bool
    compatible: bool
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = list(self.issues)
        return payload


@dataclass(frozen=True)
class IndexKeyspaceSignature:
    index_path: str
    exists: bool
    first_ids: tuple[int, ...]
    last_id: int | None
    first_ids_contiguous: bool


@dataclass(frozen=True)
class AlignmentIndexKeyspaceValidation:
    compatible: bool
    reason: str
    target: IndexKeyspaceSignature
    alignment: IndexKeyspaceSignature


def _parse_index_id(line: str) -> int | None:
    fields = line.rstrip("\n").split("\t", 1)
    if not fields or not fields[0]:
        return None
    try:
        return int(fields[0])
    except ValueError:
        return None


def _read_first_ids(index_path: Path, *, limit: int) -> tuple[int, ...]:
    ids: list[int] = []
    with index_path.open("rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parsed = _parse_index_id(line)
            if parsed is None:
                continue
            ids.append(parsed)
            if len(ids) >= limit:
                break
    return tuple(ids)


def _read_last_id(index_path: Path, *, block_size: int = 65536) -> int | None:
    if not index_path.exists() or index_path.stat().st_size == 0:
        return None
    with index_path.open("rb") as handle:
        handle.seek(0, 2)
        end = handle.tell()
        buffer = b""
        position = end
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            buffer = handle.read(read_size) + buffer
            lines = buffer.splitlines()
            if len(lines) > 1 or position == 0:
                for raw_line in reversed(lines):
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    parsed = _parse_index_id(line)
                    if parsed is not None:
                        return parsed
                return None
    return None


def summarize_index_keyspace(
    index_path: str | Path,
    *,
    sample_limit: int = 4096,
) -> IndexKeyspaceSignature:
    path = Path(index_path)
    if not path.exists():
        return IndexKeyspaceSignature(
            index_path=str(path),
            exists=False,
            first_ids=(),
            last_id=None,
            first_ids_contiguous=False,
        )
    first_ids = _read_first_ids(path, limit=max(1, int(sample_limit)))
    first_ids_contiguous = bool(first_ids) and all(
        value == first_ids[0] + offset for offset, value in enumerate(first_ids)
    )
    return IndexKeyspaceSignature(
        index_path=str(path),
        exists=True,
        first_ids=first_ids,
        last_id=_read_last_id(path),
        first_ids_contiguous=first_ids_contiguous,
    )


def scan_index_keyspace(index_path: str | Path) -> IndexKeyspaceScan:
    """Scan an MMseqs `*.index` file for keyspace shape.

    This is intentionally a streaming full scan so the validator can catch the
    concrete RepA failure mode: an `_aln.index` with the right row count but a
    remapped/gapped numeric ID domain. It does not materialize IDs in memory.
    """
    path = Path(index_path)
    if not path.exists():
        return IndexKeyspaceScan(
            index_path=str(path),
            exists=False,
            count=0,
            min_id=None,
            max_id=None,
            first_id=None,
            last_id=None,
            gap_count=0,
            duplicate_or_unsorted_count=0,
            non_numeric_lines=0,
            strictly_increasing=False,
            contiguous_from_zero=False,
        )

    count = 0
    min_id: int | None = None
    max_id: int | None = None
    first_id: int | None = None
    last_id: int | None = None
    previous_id: int | None = None
    duplicate_or_unsorted_count = 0
    non_numeric_lines = 0
    with path.open("rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parsed = _parse_index_id(line)
            if parsed is None:
                non_numeric_lines += 1
                continue
            if first_id is None:
                first_id = parsed
            if previous_id is not None and parsed <= previous_id:
                duplicate_or_unsorted_count += 1
            previous_id = parsed
            last_id = parsed
            min_id = parsed if min_id is None else min(min_id, parsed)
            max_id = parsed if max_id is None else max(max_id, parsed)
            count += 1

    gap_count = 0
    if count > 0 and min_id is not None and max_id is not None:
        expected_span = max_id - min_id + 1
        gap_count = max(0, expected_span - count)
    strictly_increasing = count > 0 and duplicate_or_unsorted_count == 0
    contiguous_from_zero = (
        count > 0
        and min_id == 0
        and max_id == count - 1
        and gap_count == 0
        and strictly_increasing
    )
    return IndexKeyspaceScan(
        index_path=str(path),
        exists=True,
        count=count,
        min_id=min_id,
        max_id=max_id,
        first_id=first_id,
        last_id=last_id,
        gap_count=gap_count,
        duplicate_or_unsorted_count=duplicate_or_unsorted_count,
        non_numeric_lines=non_numeric_lines,
        strictly_increasing=strictly_increasing,
        contiguous_from_zero=contiguous_from_zero,
    )


def validate_db_family_integrity(db_root: str | Path, family: str) -> DbFamilyIntegrityReport:
    """Validate target/_seq/_aln keyspace compatibility for one ColabFold DB family."""
    root = Path(db_root)
    family = str(family)
    target_db = root / family
    seq_db = root / f"{family}_seq"
    aln_db = root / f"{family}_aln"
    target = scan_index_keyspace(Path(str(target_db) + ".index"))
    seq = scan_index_keyspace(Path(str(seq_db) + ".index"))
    aln = scan_index_keyspace(Path(str(aln_db) + ".index"))
    target_ready = _db_prefix_ready(target_db)
    seq_ready = _db_prefix_ready(seq_db)
    aln_ready = _db_prefix_ready(aln_db)

    issues: list[str] = []
    if not target_ready:
        issues.append("target DB prefix or dbtype is missing")
    if not seq_ready:
        issues.append("sequence DB prefix or dbtype is missing")
    if not aln_ready:
        issues.append("alignment DB prefix or dbtype is missing")
    for label, scan in (("target", target), ("sequence", seq), ("alignment", aln)):
        if not scan.exists:
            issues.append(f"{label} index is missing")
        elif scan.count == 0:
            issues.append(f"{label} index contains no numeric IDs")
        elif scan.non_numeric_lines:
            issues.append(f"{label} index contains {scan.non_numeric_lines} non-numeric ID line(s)")
        elif not scan.strictly_increasing:
            issues.append(f"{label} index is not strictly increasing")
        elif scan.gap_count:
            issues.append(f"{label} index has {scan.gap_count} gap(s) in numeric ID span")

    comparable = target.count > 0 and seq.count > 0 and aln.count > 0
    if comparable:
        target_alignment_tuple = (target.count, target.min_id, target.max_id, target.gap_count, target.contiguous_from_zero)
        aln_tuple = (aln.count, aln.min_id, aln.max_id, aln.gap_count, aln.contiguous_from_zero)
        if aln_tuple != target_alignment_tuple:
            issues.append("alignment index keyspace differs from target index keyspace")
        if seq.min_id != 0:
            issues.append("sequence index does not start at numeric ID 0")
        if target.max_id is not None and (seq.max_id is None or seq.max_id < target.max_id):
            issues.append("sequence index does not cover the target DB numeric ID range")

    compatible = not issues
    return DbFamilyIntegrityReport(
        family=family,
        target=target,
        sequence=seq,
        alignment=aln,
        target_db_ready=target_ready,
        sequence_db_ready=seq_ready,
        alignment_db_ready=aln_ready,
        compatible=compatible,
        issues=tuple(issues),
    )


def _db_prefix_ready(db_path: Path) -> bool:
    return db_path.exists() and Path(str(db_path) + ".dbtype").exists()


def validate_alignment_index_keyspace(
    target_db: str | Path,
    alignment_db: str | Path,
    *,
    sample_limit: int = 4096,
) -> AlignmentIndexKeyspaceValidation:
    """Validate that an MMseqs alignment DB is usable with the target DB.

    `expandaln` expects hits from the target/search result DB to address records in
    the alignment DB by the same numeric target IDs. A bad rebuild/remap can leave
    the alignment index with the right row count but a different/gapped keyspace;
    RepA then fails with `Missing alignments for sequence ...` and
    `Invalid alignment result record.`

    This validator is intentionally lightweight for runtime use: it checks that
    both DB prefixes are materialized, then compares the sampled prefix and last
    numeric IDs without scanning multi-GB indexes on every MSA request. A separate
    forensic validator can still do a full gap/count scan.
    """
    target_db = Path(target_db)
    alignment_db = Path(alignment_db)
    target_sig = summarize_index_keyspace(Path(str(target_db) + ".index"), sample_limit=sample_limit)
    aln_sig = summarize_index_keyspace(Path(str(alignment_db) + ".index"), sample_limit=sample_limit)

    if not _db_prefix_ready(target_db):
        return AlignmentIndexKeyspaceValidation(False, "target DB prefix or dbtype is missing", target_sig, aln_sig)
    if not _db_prefix_ready(alignment_db):
        return AlignmentIndexKeyspaceValidation(False, "alignment DB prefix or dbtype is missing", target_sig, aln_sig)
    if not target_sig.exists:
        return AlignmentIndexKeyspaceValidation(False, "target DB index is missing", target_sig, aln_sig)
    if not aln_sig.exists:
        return AlignmentIndexKeyspaceValidation(False, "alignment DB index is missing", target_sig, aln_sig)
    if not target_sig.first_ids or not aln_sig.first_ids:
        return AlignmentIndexKeyspaceValidation(False, "could not read numeric IDs from index files", target_sig, aln_sig)
    if target_sig.first_ids != aln_sig.first_ids:
        return AlignmentIndexKeyspaceValidation(False, "sampled index prefix differs between target and alignment DB", target_sig, aln_sig)
    if not target_sig.first_ids_contiguous:
        return AlignmentIndexKeyspaceValidation(False, "target DB sampled prefix is not contiguous", target_sig, aln_sig)
    if not aln_sig.first_ids_contiguous:
        return AlignmentIndexKeyspaceValidation(False, "alignment DB sampled prefix is not contiguous", target_sig, aln_sig)
    if target_sig.last_id != aln_sig.last_id:
        return AlignmentIndexKeyspaceValidation(
            False,
            f"last id differs between target and alignment DB ({target_sig.last_id} != {aln_sig.last_id})",
            target_sig,
            aln_sig,
        )
    return AlignmentIndexKeyspaceValidation(
        True,
        "alignment DB index keyspace matches target DB index sample",
        target_sig,
        aln_sig,
    )
