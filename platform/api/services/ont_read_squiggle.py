"""Per-read squiggle from a job's own POD5 input and move-tagged basecall BAM.

Dorado writes the move table (mv: stride + per-sample-block moves) and the
number of trimmed leading samples (ts). Together with the read's POD5 signal
they give the exact signal segment behind every called base, and MM/ML give
the modification probability at each base. No mapping job is required.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from services.ont_read_report import MOD_CODE_NAMES, OntReadReportError

READ_ID = re.compile(r"^[0-9a-fA-F-]{8,64}$")
MAX_WINDOW_BASES = 400
MAX_READ_LIST = 200


def _open_bam(bam_path: Path):
    try:
        import pysam
    except ImportError as exc:  # pragma: no cover - pinned dependency
        raise OntReadReportError("pysam is unavailable") from exc
    return pysam.AlignmentFile(str(bam_path), "rb", check_sq=False)


def list_reads(bam_path: Path, *, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_READ_LIST))
    reads = []
    with _open_bam(bam_path) as handle:
        for read in handle.fetch(until_eof=True):
            if read.is_secondary or read.is_supplementary:
                continue
            mods: dict[str, int] = {}
            try:
                calls = read.modified_bases_forward or read.modified_bases or {}
            except (ValueError, KeyError):
                calls = {}
            for (canonical, _strand, code), values in calls.items():
                name = f"{canonical}+{MOD_CODE_NAMES.get(str(code), str(code))}"
                mods[name] = sum(1 for _pos, quality in values if (quality + 0.5) / 256 >= 0.5)
            reads.append({
                "read_id": read.query_name,
                "length": read.query_length or 0,
                "qscore": float(read.get_tag("qs")) if read.has_tag("qs") else None,
                "has_moves": read.has_tag("mv"),
                "modified_calls": mods,
            })
    reads.sort(key=lambda item: item["length"], reverse=True)
    return {"reads": reads[:limit], "total_primary_reads": len(reads)}


def _find_bam_read(bam_path: Path, read_id: str):
    with _open_bam(bam_path) as handle:
        for read in handle.fetch(until_eof=True):
            if read.query_name == read_id and not read.is_secondary and not read.is_supplementary:
                return read
    return None


def _pod5_files(pod5_dir: Path) -> list[Path]:
    if pod5_dir.is_file() and pod5_dir.suffix == ".pod5":
        return [pod5_dir]
    files = []
    for parent, directories, names in os.walk(pod5_dir, followlinks=False):
        directories[:] = sorted(name for name in directories if not (Path(parent) / name).is_symlink())
        files.extend(Path(parent) / name for name in sorted(names)
                     if name.endswith(".pod5") and not (Path(parent) / name).is_symlink())
    return files


def _signal_pa(pod5_dir: Path, read_id: str):
    try:
        import pod5
    except ImportError as exc:  # pragma: no cover - pinned dependency
        raise OntReadReportError("pod5 is unavailable") from exc
    for path in _pod5_files(pod5_dir):
        with pod5.Reader(path) as reader:
            for record in reader.reads(selection=[read_id], missing_ok=True):
                return record.signal_pa, path.name
    return None, None


def read_squiggle(bam_path: Path, pod5_dir: Path, read_id: str, *, start_base: int = 0,
                  bases: int = 200) -> dict[str, Any]:
    if not READ_ID.fullmatch(read_id):
        raise OntReadReportError("read id is not a POD5 read UUID")
    bases = max(10, min(int(bases), MAX_WINDOW_BASES))
    read = _find_bam_read(bam_path, read_id)
    if read is None:
        raise OntReadReportError("read is not in this run's basecall BAM")
    if not read.has_tag("mv"):
        raise OntReadReportError("this read has no move table; basecall with move tables enabled")
    moves = list(read.get_tag("mv"))
    stride, moves = int(moves[0]), moves[1:]
    trimmed = int(read.get_tag("ts")) if read.has_tag("ts") else 0
    sequence = read.query_sequence or ""
    signal, source_file = _signal_pa(pod5_dir, read_id)
    if signal is None:
        raise OntReadReportError("read signal is not in this run's POD5 input")
    # Base i starts at the i-th move==1 block; it ends where base i+1 starts.
    starts = [trimmed + index * stride for index, move in enumerate(moves) if move == 1]
    if len(starts) != len(sequence):
        raise OntReadReportError("move table does not match the called sequence length")
    starts.append(trimmed + len(moves) * stride)
    start_base = max(0, min(int(start_base), max(0, len(sequence) - 1)))
    end_base = min(len(sequence), start_base + bases)
    mod_by_position: dict[int, dict[str, float]] = {}
    try:
        calls = read.modified_bases_forward or read.modified_bases or {}
    except (ValueError, KeyError):
        calls = {}
    for (canonical, _strand, code), values in calls.items():
        name = f"{canonical}+{MOD_CODE_NAMES.get(str(code), str(code))}"
        for position, quality in values:
            if start_base <= position < end_base:
                mod_by_position.setdefault(position, {})[name] = round((quality + 0.5) / 256, 4)
    segment_start, segment_end = starts[start_base], starts[end_base]
    samples = [round(float(value), 2) for value in signal[segment_start:segment_end]]
    return {
        "schema": "bms.ngs.read-squiggle.v1",
        "read_id": read_id,
        "pod5_file": source_file,
        "read_length": len(sequence),
        "stride": stride,
        "trimmed_samples": trimmed,
        "start_base": start_base,
        "end_base": end_base,
        "sample_offset": segment_start,
        "signal_pa": samples,
        "bases": [
            {"index": index, "base": sequence[index],
             "sample_start": starts[index] - segment_start,
             "sample_end": starts[index + 1] - segment_start,
             "modifications": mod_by_position.get(index, {})}
            for index in range(start_base, end_base)
        ],
    }
