"""Poly(A) tail length: the option reaches Dorado, and its per-read values are reported as written."""
from __future__ import annotations

from pathlib import Path

import pysam
import pytest

from services.ont_ngs_contract import normalize_ont_launch_params
from services.ont_read_report import build_read_report, poly_a_tsv_text

REPO = Path(__file__).resolve().parents[3]


def test_the_option_is_off_unless_asked_for_and_carries_its_context() -> None:
    assert normalize_ont_launch_params("ont_basecall_rna", {})["estimate_poly_a"] is False
    rna = normalize_ont_launch_params("ont_basecall_rna", {"estimate_poly_a": True})
    assert rna["estimate_poly_a"] is True and rna["poly_a_context"] == "molecule_end"
    plasmid = normalize_ont_launch_params("ont_basecall_dna", {
        "estimate_poly_a": True, "poly_a_context": "plasmid",
        "poly_a_front_flank": " gctagc aagctt ", "poly_a_rear_flank": "GGATCCGAATTC"})
    assert plasmid["poly_a_front_flank"] == "GCTAGCAAGCTT" and plasmid["poly_a_rear_flank"] == "GGATCCGAATTC"
    # Flanks without the option are dropped, not launched.
    off = normalize_ont_launch_params("ont_basecall_dna", {"poly_a_context": "plasmid", "poly_a_front_flank": "ACGTAC"})
    assert off["estimate_poly_a"] is False and "poly_a_context" not in off and "poly_a_front_flank" not in off


@pytest.mark.parametrize("params, message", [
    ({"estimate_poly_a": True, "poly_a_context": "plasmid", "poly_a_front_flank": "ACGTAC"}, "just before and just after"),
    ({"estimate_poly_a": True, "poly_a_context": "plasmid", "poly_a_front_flank": "ACGTNN", "poly_a_rear_flank": "ACGTAC"}, "just before and just after"),
    ({"estimate_poly_a": True, "poly_a_context": "elsewhere"}, "molecule_end or plasmid"),
    ({"estimate_poly_a": "yes"}, "boolean"),
])
def test_incomplete_poly_a_settings_are_named(params: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_ont_launch_params("ont_basecall_dna", params)


def test_the_basecall_step_passes_the_native_option_and_the_plasmid_flanks() -> None:
    text = (REPO / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    assert "command+=(--estimate-poly-a)" in text
    assert 'command+=(--poly-a-config "\\$PWD/poly_a_config.toml")' in text
    assert "plasmid_front_flank" in text and "plasmid_rear_flank" in text
    assert "estimate_poly_a = false" in (REPO / "nextflow.config").read_text(encoding="utf-8")


def _bam(path: Path, tails: list[int | None]) -> None:
    header = pysam.AlignmentHeader.from_dict({"HD": {"VN": "1.6", "SO": "unknown"}})
    with pysam.AlignmentFile(str(path), "wb", header=header) as handle:
        for index, tail in enumerate(tails):
            read = pysam.AlignedSegment(header)
            read.query_name = f"read-{index}"
            read.flag = 4
            read.query_sequence = "ACGT" * 5
            read.query_qualities = pysam.qualitystring_to_array("I" * 20)
            if tail is not None:
                read.set_tag("pt", tail, "i")
            handle.write(read)


def test_report_counts_no_estimate_apart_from_zero_and_from_untagged_reads(tmp_path: Path) -> None:
    _bam(tmp_path / "calls.bam", [-1, 0, 12, 18, 95, -1, None])
    poly_a = build_read_report(tmp_path / "calls.bam", artifact={})["poly_a"]
    assert poly_a["reads"] == 7 and poly_a["estimated_reads"] == 4
    assert poly_a["no_estimate_reads"] == 2 and poly_a["zero_length_reads"] == 1 and poly_a["reads_without_tag"] == 1
    assert poly_a["median_tail_nt"] == 15 and poly_a["longest_tail_nt"] == 95 and poly_a["mean_tail_nt"] == 31.25
    assert poly_a["histogram"]["bins"] == [
        {"start_nt": 0, "end_nt_exclusive": 10, "read_count": 1},
        {"start_nt": 10, "end_nt_exclusive": 20, "read_count": 2},
        {"start_nt": 90, "end_nt_exclusive": 100, "read_count": 1},
    ]
    assert poly_a_tsv_text(tmp_path / "calls.bam").splitlines() == [
        "read_id\tpoly_a_tail_nt\testimate", "read-0\t-1\tno_estimate", "read-1\t0\testimated", "read-2\t12\testimated",
        "read-3\t18\testimated", "read-4\t95\testimated", "read-5\t-1\tno_estimate", "read-6\t\tnot_tagged"]


def test_a_run_without_the_option_reports_no_poly_a_block(tmp_path: Path) -> None:
    _bam(tmp_path / "calls.bam", [None, None])
    assert build_read_report(tmp_path / "calls.bam", artifact={})["poly_a"] is None
