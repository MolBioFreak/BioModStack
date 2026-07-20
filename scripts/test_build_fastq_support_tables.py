from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_fastq_support_tables.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_fastq_support_tables_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_per_base_support_counts_bases_insertions_and_deletions(tmp_path: Path) -> None:
    module = _load_module()
    reference = tmp_path / "reference.fasta"
    reference.write_text(">plasmid\nACGTAC\n", encoding="utf-8")

    sam_lines = [
        # perfect forward read across positions 1-6
        "read_perfect\t0\tplasmid\t1\t60\t6M\t*\t0\t0\tACGTAC\tIIIIII",
        # reverse-strand read with a T at position 3, insertion after position 3, and deletion at position 5
        "read_mixed\t16\tplasmid\t2\t60\t2M1I1M1D1M\t*\t0\t0\tCTGAC\tIIIII",
    ]

    output = tmp_path / "per_base_support.tsv"
    module.build_per_base_support(
        bam=tmp_path / "aligned.bam",
        reference_fasta=reference,
        output_tsv=output,
        samtools_cmd=[sys.executable, "-c", "import sys; print('\\n'.join(%r))" % sam_lines],
        min_depth=1,
        ambiguous_fraction=0.8,
    )

    rows = list(module.read_tsv(output))
    by_pos = {int(row["position_1based"]): row for row in rows}

    assert by_pos[1]["reference_base"] == "A"
    assert by_pos[1]["depth"] == "1"
    assert by_pos[1]["a_count"] == "1"
    assert by_pos[1]["forward_depth"] == "1"
    assert by_pos[1]["reverse_depth"] == "0"

    assert by_pos[3]["reference_base"] == "G"
    assert by_pos[3]["depth"] == "2"
    assert by_pos[3]["g_count"] == "1"
    assert by_pos[3]["t_count"] == "1"
    assert by_pos[3]["insertion_count"] == "1"
    assert by_pos[3]["major_allele_fraction"] == "0.5000"
    assert by_pos[3]["ambiguous"] == "true"

    assert by_pos[5]["reference_base"] == "A"
    assert by_pos[5]["depth"] == "2"
    assert by_pos[5]["deletion_count"] == "1"
    assert by_pos[5]["low_coverage"] == "false"


def test_deletion_majority_is_the_consensus_allele() -> None:
    module = _load_module()
    item = module.PositionSupport()
    item.base_counts["T"] = 1
    item.deletion_count = 2

    consensus, fraction = module._consensus_and_fraction(item, "T")

    assert consensus == "-"
    assert fraction == 2 / 3


def test_secondary_alignment_with_omitted_sequence_is_not_counted() -> None:
    module = _load_module()
    support = [module.PositionSupport() for _ in range(11)]

    module.consume_sam_record(
        "read-a\t256\tplasmid\t1\t0\t5M1I4M\t*\t0\t0\t*\t*",
        "plasmid",
        10,
        support,
    )

    assert all(item.depth == 0 and item.insertion_count == 0 for item in support)


def test_build_fastq_support_tables_cli_writes_expected_header(tmp_path: Path) -> None:
    reference = tmp_path / "reference.fasta"
    reference.write_text(">plasmid\nAC\n", encoding="utf-8")
    output = tmp_path / "support.tsv"
    sam_lines = ["read1\t0\tplasmid\t1\t60\t2M\t*\t0\t0\tAC\tII"]

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--bam",
            str(tmp_path / "aligned.bam"),
            "--reference-fasta",
            str(reference),
            "--out-per-base-support",
            str(output),
            "--samtools-cmd",
            sys.executable,
            "-c",
            "import sys; print('\\n'.join(%r))" % sam_lines,
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stderr == ""
    header = output.read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert header == [
        "chrom",
        "position_1based",
        "reference_base",
        "depth",
        "forward_depth",
        "reverse_depth",
        "a_count",
        "c_count",
        "g_count",
        "t_count",
        "n_count",
        "insertion_count",
        "deletion_count",
        "consensus_base",
        "major_allele_fraction",
        "low_coverage",
        "ambiguous",
    ]
