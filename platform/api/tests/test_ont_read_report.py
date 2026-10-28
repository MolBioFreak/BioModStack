from __future__ import annotations

from pathlib import Path

import pysam
import pytest

from services.ont_read_report import build_read_report, select_reads_bam


def _write_bam(path: Path, *, aligned: bool) -> None:
    header = {"HD": {"VN": "1.6", "SO": "unknown"},
              "PG": [{"ID": "basecaller", "PN": "dorado", "VN": "2.1.2", "CL": "dorado basecaller hac --modified-bases 6mA"}],
              "RG": [{"ID": "rg1", "DS": "runid=r basecall_model=dna_r10.4.1_e8.2_400bps_hac@v6.0.0 modbase_models=dna_r10.4.1_e8.2_400bps_hac@v6.0.0_6mA@v1"}]}
    if aligned:
        header["SQ"] = [{"SN": "plasmid", "LN": 1000}]
    with pysam.AlignmentFile(str(path), "wb", header=header) as handle:
        for index, (sequence, mods) in enumerate([
            ("AAAACCCCGG" * 10, ("A+a?,0,0,0,0;", [250, 250, 10, 10])),  # 4 A sites called, 2 modified
            ("ACGT" * 50, ("A+a?,0,0;", [5, 5])),
            ("GGGG" * 5, None),
        ]):
            read = pysam.AlignedSegment(handle.header)
            read.query_name = f"r{index}"
            read.query_sequence = sequence
            read.query_qualities = pysam.qualitystring_to_array("5" * len(sequence))  # Q20
            if aligned and index < 2:
                read.reference_id = 0
                read.reference_start = 0
                read.cigarstring = f"{len(sequence)}M"
                read.mapping_quality = 60
            else:
                read.is_unmapped = True
            read.set_tag("qs", 20.0 + index)
            if index == 0:
                read.set_tag("mv", [6, 1, 0, 1])
            if mods:
                read.set_tag("MM", mods[0])
                read.set_tag("ML", mods[1])
            handle.write(read)
        secondary = pysam.AlignedSegment(handle.header)
        secondary.query_name = "r0"
        secondary.query_sequence = "A" * 10
        secondary.is_secondary = True
        secondary.is_unmapped = not aligned
        if aligned:
            secondary.reference_id = 0
            secondary.reference_start = 5
            secondary.cigarstring = "10M"
        handle.write(secondary)


@pytest.mark.parametrize("aligned", [False, True])
def test_read_report_summarises_yield_quality_mapping_and_modifications(tmp_path: Path, aligned: bool) -> None:
    bam = tmp_path / "calls.bam"
    _write_bam(bam, aligned=aligned)
    report = build_read_report(bam, artifact={"filename": "calls.bam"})
    assert report["schema"] == "bms.ngs.read-report.v1"
    assert report["reads"] == 3
    assert report["total_bases"] == 100 + 200 + 20
    assert report["n50_read_length_bp"] == 200
    assert report["mean_qscore"] == 21.0
    assert report["reads_q20_or_better"] == 3
    assert report["move_tagged_reads"] == 1
    assert report["alignment"]["secondary_records"] == 1
    assert report["alignment"]["aligned"] is aligned
    assert report["alignment"]["mapped_reads"] == (2 if aligned else 0)
    (sixma,) = report["modifications"]["by_code"]
    assert sixma["code"] == "A+6mA"
    assert (sixma["called_sites"], sixma["modified_sites"], sixma["reads"]) == (6, 2, 2)
    assert sixma["modified_fraction"] == round(2 / 6, 6)
    assert report["programs"][0]["program"] == "dorado"
    assert "modbase_models=" in report["read_groups"][0]["basecall_model"]
    assert sum(bin_["read_count"] for bin_ in report["read_length_histogram"]["bins"]) == 3


def test_select_reads_bam_prefers_alignment_and_skips_dimer_subsets() -> None:
    artifacts = [
        {"state": "present", "kind": "basecall_bam", "filename": "calls.bam", "artifact_id": "b"},
        {"state": "present", "kind": "alignment_bam", "relative_path": "x/dimer_reads.bam", "artifact_id": "d"},
        {"state": "present", "kind": "alignment_bam", "filename": "aligned.bam", "artifact_id": "a"},
        {"state": "present", "kind": "alignment_index", "filename": "aligned.bam.bai", "artifact_id": "i"},
    ]
    assert select_reads_bam(artifacts)["artifact_id"] == "a"
    assert select_reads_bam(artifacts[:1])["artifact_id"] == "b"
    assert select_reads_bam([{"state": "missing", "filename": "calls.bam"}]) is None
    native = [
        {"state": "present", "kind": "alignment_bam", "relative_path": "basecall/calls.bam", "artifact_id": "c"},
        {"state": "present", "kind": "alignment_bam", "relative_path": "align/sample.sorted.bam", "artifact_id": "s"},
    ]
    assert select_reads_bam(native)["artifact_id"] == "s"
