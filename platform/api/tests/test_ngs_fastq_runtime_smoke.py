"""Environment-gated runtime smoke test for the ONT FASTQ QC workflow.

This test is intentionally skipped on lightweight CI/dev containers that do not
have the Nextflow/minimap2/samtools runtime installed. On a runtime-capable
BioModStack host it proves the workflow emits the core plasmid-QC artifacts
instead of only passing static file/registry checks.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent


def _require_runtime_command(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} is not installed in this test environment")


def _require_container_engine() -> None:
    """Verify that a container engine (apptainer/singularity) is available."""
    if shutil.which("apptainer") is None and shutil.which("singularity") is None:
        pytest.skip(
            "Nextflow dorado_cpu processes require apptainer or singularity "
            "container engine — neither found in this environment"
        )


def test_ont_fastq_qc_runtime_emits_core_artifacts(tmp_path: Path):
    """Run tiny FASTQ+reference through Nextflow and assert advertised outputs.

    NOTE: ``dorado_cpu`` processes run in a Singularity container that binds only
    ``--bind ${params.code_root}`` (the repo root).  Files in ``/tmp`` are invisible
    to the container, so fixtures live in a dedicated subdirectory of the repo.
    Timestamped filenames avoid collisions across parallel test runs.
    """
    _require_runtime_command("nextflow")
    # The dorado_cpu label runs in dorado.sif which has minimap2/samtools.
    # Verify container engine is available before running.
    _require_container_engine()

    # ── Fixtures inside repo (visible to container) ──────────────────
    fixture_dir = REPO_ROOT / "platform/api/tests/ngs_runtime_fixtures"
    fixture_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ref = fixture_dir / f"ref_{ts}.fasta"
    fastq = fixture_dir / f"reads_{ts}.fastq"
    out_dir = fixture_dir / f"out_{ts}"
    work_dir = fixture_dir / f"work_{ts}"

    seq = "ACGT" * 200
    ref.write_text(f">tiny_plasmid\n{seq}\n")
    fastq.write_text(f"@read_1\n{seq}\n+\n{'I' * len(seq)}\n")

    cmd = [
        "nextflow",
        "run",
        str(REPO_ROOT / "workflows/ngs/ont_fastq_qc.nf"),
        "-profile",
        f"singularity,ont_fastq_qc" if shutil.which("singularity") is not None else "apptainer,ont_fastq_qc",
        "-w",
        str(work_dir),
        "--fastq_path",
        str(fastq),
        "--reference_fasta",
        str(ref),
        "--out_dir",
        str(out_dir),
        "--code_root",
        str(REPO_ROOT),
        "--expected_plasmid_size",
        str(len(seq)),
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
