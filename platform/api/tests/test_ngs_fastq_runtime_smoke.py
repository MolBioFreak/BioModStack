"""Environment-gated runtime smoke test for the ONT FASTQ QC workflow.

This test is intentionally skipped on lightweight CI/dev containers that do not
have the Nextflow/minimap2/samtools runtime installed. On a runtime-capable
BioModStack host it proves the workflow emits the core plasmid-QC artifacts
instead of only passing static file/registry checks.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent


def _require_runtime_command(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} is not installed in this test environment")


def test_ont_fastq_qc_runtime_emits_core_artifacts(tmp_path: Path):
    """Run tiny FASTQ+reference through Nextflow and assert advertised outputs."""
    for command in ("nextflow", "minimap2", "samtools"):
        _require_runtime_command(command)

    fixture_dir = tmp_path / "fixture"
    out_dir = tmp_path / "out"
    work_dir = tmp_path / "work"
    fixture_dir.mkdir()

    reference = fixture_dir / "reference.fasta"
    fastq = fixture_dir / "reads.fastq"
    sequence = "ACGT" * 200
    reference.write_text(f">tiny_plasmid\n{sequence}\n")
    quality = "I" * len(sequence)
    fastq.write_text(f"@read_1\n{sequence}\n+\n{quality}\n")

    cmd = [
        "nextflow",
        "run",
        str(REPO_ROOT / "workflows/ngs/ont_fastq_qc.nf"),
        "-profile",
        "ont_fastq_qc",
        "-w",
        str(work_dir),
        "--fastq_path",
        str(fastq),
        "--reference_fasta",
        str(reference),
        "--out_dir",
        str(out_dir),
        "--code_root",
        str(REPO_ROOT),
        "--expected_plasmid_size",
        str(len(sequence)),
        "--dimer_output_mode",
        "core",
    ]
    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout

    expected = [
        out_dir / "align/aligned.bam",
        out_dir / "align/aligned.bam.bai",
        out_dir / "fastq_qc/qc_manifest.json",
        out_dir / "fastq_qc/per_base_support.tsv",
        out_dir / "fastq_qc/fastq_consensus.fasta",
        out_dir / "multimer_qc/dimer_breakpoint_call.tsv",
        out_dir / "multimer_qc/dimer_evidence_by_position.tsv",
        out_dir / "multimer_qc/dimer_read_events.tsv",
    ]
    missing = [str(path) for path in expected if not path.exists()]
    assert missing == []

    assert (out_dir / "fastq_qc/per_base_support.tsv").stat().st_size > 0
    assert (out_dir / "fastq_qc/qc_manifest.json").stat().st_size > 0
