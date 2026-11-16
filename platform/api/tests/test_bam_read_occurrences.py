"""Reads are linked to BAM records by position, and records are copied whole."""
from __future__ import annotations

import array
import hashlib
from pathlib import Path

import pysam
import pytest

from services import bam_read_occurrences as occurrences


def _bam(path: Path, count: int = 7, repeated: tuple[int, ...] = (1, 4)) -> list[str]:
    header = pysam.AlignmentHeader.from_dict({"HD": {"VN": "1.6", "SO": "unknown"}, "RG": [{"ID": "run_barcode01", "DS": "basecall_model=dna_r10"}]})
    text = []
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        for index in range(count):
            record = pysam.AlignedSegment(header)
            # Two records share one name: the name alone cannot tell them apart.
            record.query_name = "repeated-read" if index in repeated else f"read-{index}"
            record.flag = 4
            record.query_sequence = ("ACGTTGCA" * (index + 2))[: 9 + index]
            record.query_qualities = pysam.qualitystring_to_array("I" * (9 + index))
            record.set_tag("MM", "C+m?,0,1;A+a?,2;", "Z")
            record.set_tag("ML", array.array("B", [200 + index, 13, 255]))
            record.set_tag("MN", 9 + index, "i")
            record.set_tag("RG", "run_barcode01", "Z")
            record.set_tag("qs", 23.122352600097656 + index, "f")
            record.set_tag("mv", array.array("b", [6, 1, 0, 1, 1]))
            out.write(record)
            text.append(record.to_string())
    return text


def _read(path: Path) -> list[str]:
    with pysam.AlignmentFile(str(path), "rb", check_sq=False) as handle:
        return [record.to_string() for record in handle.fetch(until_eof=True)]


def test_fastq_record_n_is_bam_record_n(tmp_path: Path) -> None:
    source = _bam(tmp_path / "unit.bam")
    result = occurrences.bam_to_fastq(tmp_path / "unit.bam", tmp_path / "unit.fastq")
    lines = (tmp_path / "unit.fastq").read_text().splitlines()
    assert result["records"] == len(source) == len(lines) // 4
    assert result["bam_sha256"] == hashlib.sha256((tmp_path / "unit.bam").read_bytes()).hexdigest()
    for index, text in enumerate(source):
        fields = text.split("\t")
        assert lines[4 * index: 4 * index + 4] == [f"@{fields[0]}", fields[9], "+", fields[10]]


def test_each_reference_gets_its_own_records_with_every_tag_intact(tmp_path: Path) -> None:
    source = _bam(tmp_path / "unit.bam")
    first, second = tmp_path / "target_a.bam", tmp_path / "target_b.bam"
    # The two same-named records go to different references.
    result = occurrences.split_bam(tmp_path / "unit.bam", {first: [1, 2, 6], second: [5, 3]})
    assert _read(first) == [source[0], source[1], source[5]]
    assert _read(second) == [source[2], source[4]]
    assert result["written"][first] == {"records": 3, "sha256": hashlib.sha256(first.read_bytes()).hexdigest()}
    assert result["bam_sha256"] == hashlib.sha256((tmp_path / "unit.bam").read_bytes()).hexdigest()
    with pysam.AlignmentFile(str(first), "rb", check_sq=False) as handle:
        assert handle.header.to_dict()["RG"][0]["ID"] == "run_barcode01"
        record = next(handle.fetch(until_eof=True))
        assert record.get_tag("MM") == "C+m?,0,1;A+a?,2;" and list(record.get_tag("ML")) == [200, 13, 255]
    pysam.quickcheck("-u", str(first))  # unaligned reads: no reference list is expected


def test_a_read_cannot_go_to_two_references_and_missing_reads_are_reported(tmp_path: Path) -> None:
    _bam(tmp_path / "unit.bam", count=3)
    with pytest.raises(occurrences.BamRecordError):
        occurrences.split_bam(tmp_path / "unit.bam", {tmp_path / "a.bam": [1], tmp_path / "b.bam": [1]})
    with pytest.raises(occurrences.BamRecordError):
        occurrences.split_bam(tmp_path / "unit.bam", {tmp_path / "c.bam": [4]})
    assert not (tmp_path / "c.bam").exists()
