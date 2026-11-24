"""The plasmid summary restates existing outputs and names the file behind each number."""
import csv
import importlib.util
import json
import xml.dom.minidom
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEADER = ("chrom\tposition_1based\treference_base\tdepth\tforward_depth\treverse_depth\ta_count\tc_count\tg_count\t"
          "t_count\tn_count\tinsertion_count\tdeletion_count\tconsensus_base\tmajor_allele_fraction\tlow_coverage\tambiguous\n")


@pytest.fixture(scope="module")
def summary_module():
    spec = importlib.util.spec_from_file_location("build_plasmid_summary_under_test", ROOT / "scripts/build_plasmid_summary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs(tmp_path):
    consensus = "ACGT" * 500
    (tmp_path / "s.final.fasta").write_text(">s\n" + consensus + "\n")
    rows = [HEADER]
    for position in range(1, 2001):
        depth = 0 if position == 7 else 40 if position == 1500 else 100
        rows.append(f"s\t{position}\tA\t{depth}\t{depth}\t0\t{depth}\t0\t0\t0\t0\t0\t0\tA\t1.0\tfalse\tfalse\n")
    (tmp_path / "per_base_support.tsv").write_text("".join(rows))
    (tmp_path / "alignment_stats.tsv").write_text("metric\tvalue\ntotal_reads\t5\nmapped_reads\t4\nunmapped_reads\t1\n")
    reads = [2000, 2000, 2010, 4000, 150]
    (tmp_path / "reads.fastq").write_text("".join(f"@r{i}\n{'A' * n}\n+\n{'F' * n}\n" for i, n in enumerate(reads)))
    (tmp_path / "s.annotations.bed").write_text("s\t10\t730\tEGFP\t-\ns\t900\t1200\tori\t+\n")
    return tmp_path, consensus, reads


def run(module, root, *extra):
    return module.main(["--sample", "s", "--consensus-fasta", str(root / "s.final.fasta"),
                        "--per-base-support", str(root / "per_base_support.tsv"),
                        "--alignment-stats", str(root / "alignment_stats.tsv"),
                        "--annotations-bed", str(root / "s.annotations.bed"),
                        "--out-dir", str(root / "plasmid_summary"), *extra])


def test_numbers_come_from_the_named_outputs(summary_module, inputs):
    root, consensus, reads = inputs
    assert run(summary_module, root, "--reads-fastq", str(root / "reads.fastq")) == 0
    out = root / "plasmid_summary"
    summary = json.loads((out / "summary.json").read_text())
    assert summary["schema"] == "bms.ngs.plasmid-summary.v1"
    assert summary["expected_sequence_provided"] is False
    assert summary["consensus"] == {"name": "s", "length_bp": 2000, "sequence": consensus, "source": "s.final.fasta"}
    assert summary["reads"] == {"total_reads": 5, "total_bases": sum(reads), "longest_read_bp": 4000, "source": "reads.fastq"}
    assert summary["alignment"] == {"mapped_reads": 4, "unmapped_reads": 1, "source": "alignment_stats.tsv"}
    coverage = summary["coverage"]
    assert coverage["source"] == "per_base_support.tsv"
    assert (coverage["positions"], coverage["positions_with_reads"], coverage["minimum_depth"], coverage["maximum_depth"]) == (2000, 1999, 0, 100)
    assert coverage["mean_depth"] == pytest.approx((1998 * 100 + 40) / 2000)
    # A one-base dip survives the plot's windowing.
    assert min(point["minimum_depth"] for point in coverage["points"]) == 0
    assert any(point["minimum_depth"] == 40 for point in coverage["points"])
    bins = {b["start_bp"]: b for b in summary["read_length_histogram"]["bins"]}
    assert bins[2000] == {"start_bp": 2000, "end_bp_exclusive": 2100, "read_count": 3, "base_count": 6010}
    assert summary["features"] == [{"name": "EGFP", "start": 10, "end": 730, "strand": -1},
                                   {"name": "ori", "start": 900, "end": 1200, "strand": 1}]


def test_files_are_written_and_the_plots_are_well_formed_svg(summary_module, inputs):
    root, _, _ = inputs
    assert run(summary_module, root, "--reads-fastq", str(root / "reads.fastq"), "--expected-sequence-provided") == 0
    out = root / "plasmid_summary"
    assert sorted(p.name for p in out.iterdir()) == ["coverage.svg", "read_length_histogram.svg", "run_summary.csv",
                                                      "sample_summary.txt", "summary.json", "virtual_gel.svg"]
    for name in ("coverage.svg", "read_length_histogram.svg", "virtual_gel.svg"):
        assert xml.dom.minidom.parse(str(out / name)).documentElement.tagName == "svg"
    text = (out / "sample_summary.txt").read_text()
    assert "Expected sequence: given" in text
    assert "Total reads: 5 (reads.fastq)" in text and "Mapped reads: 4 (alignment_stats.tsv)" in text
    rows = list(csv.DictReader((out / "run_summary.csv").open()))
    assert rows == [{"sample": "s", "expected_sequence_provided": "true", "consensus_length_bp": "2000", "total_reads": "5",
                     "total_bases": "10160", "mapped_reads": "4", "mean_depth": "99.92", "minimum_depth": "0",
                     "maximum_depth": "100", "covered_fraction": "0.9995", "annotated_features": "2"}]


def test_read_lengths_table_gives_the_same_reads_and_missing_statistics_are_not_invented(summary_module, inputs):
    root, _, reads = inputs
    (root / "read_lengths.tsv").write_text("read_id\tlength_bp\n" + "".join(f"r{i}\t{n}\n" for i, n in enumerate(reads)))
    assert summary_module.main(["--sample", "s", "--consensus-fasta", str(root / "s.final.fasta"),
                                "--per-base-support", str(root / "per_base_support.tsv"),
                                "--read-lengths", str(root / "read_lengths.tsv"), "--out-dir", str(root / "o")]) == 0
    summary = json.loads((root / "o/summary.json").read_text())
    assert summary["reads"]["total_bases"] == sum(reads) and summary["reads"]["source"] == "read_lengths.tsv"
    assert summary["alignment"] == {"mapped_reads": None, "unmapped_reads": None, "source": None}
    assert summary["features"] == [] and summary["features_source"] is None
    assert "Mapped reads: not available (no alignment statistics)" in (root / "o/sample_summary.txt").read_text()
