from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _runtime_tools() -> tuple[Path, Path]:
    nextflow = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    samtools = Path(os.environ.get("BMS_SAMTOOLS_BIN", "/home/dalab/micromamba/bin/samtools"))
    if not (nextflow.is_file() and os.access(nextflow, os.X_OK)):
        pytest.skip(f"real Nextflow launcher unavailable: {nextflow}")
    if not (samtools.is_file() and os.access(samtools, os.X_OK)):
        pytest.skip(f"samtools unavailable: {samtools}")
    return nextflow, samtools


def _mapped_bam_without_m5(tmp_path: Path, samtools: Path) -> Path:
    sam = tmp_path / "input.sam"
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        "@SQ\tSN:plasmid\tLN:12\n"
        "read1\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tACGTACGTACGT\tIIIIIIIIIIII\n",
        encoding="utf-8",
    )
    bam = tmp_path / "input.bam"
    subprocess.run(
        [str(samtools), "view", "-bS", str(sam), "-o", str(bam)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        [str(samtools), "index", str(bam)],
        check=True,
        text=True,
        capture_output=True,
    )
    return bam


def _run_validation(
    tmp_path: Path,
    declared_digest: str,
    *,
    declared_source_digest: str | None = None,
) -> subprocess.CompletedProcess[str]:
    nextflow, samtools = _runtime_tools()
    sequence = "ACGTACGTACGT"
    reference = tmp_path / "reference.fasta"
    reference.write_text(f">plasmid\n{sequence}\n", encoding="utf-8")
    bam = _mapped_bam_without_m5(tmp_path, samtools)
    source_digest = declared_source_digest or hashlib.sha256(bam.read_bytes()).hexdigest()

    harness = tmp_path / "harness.nf"
    harness.write_text(
        "nextflow.enable.dsl=2\n"
        "params.ngs_samtools_sif = null\n"
        f"include {{ ValidateMappedBam }} from '{ROOT / 'modules/ngs/bam_prepare'}'\n"
        "workflow {\n"
        "  bam_ch = Channel.of(tuple(file(params.bam), file(params.bai)))\n"
        "  ref_ch = Channel.of(file(params.reference))\n"
        "  ValidateMappedBam(bam_ch, ref_ch)\n"
        "}\n",
        encoding="utf-8",
    )
    config = tmp_path / "nextflow.config"
    config.write_text(
        "process {\n"
        "  executor = 'local'\n"
        "  withLabel: dorado_cpu { container = null; cpus = 1; memory = '1 GB' }\n"
        f"  withName: ValidateMappedBam {{ publishDir = [path: '{tmp_path / 'published'}', mode: 'copy', overwrite: true] }}\n"
        "}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{samtools.parent}:{env.get('PATH', '')}"
    for key in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        env.pop(key, None)

    return subprocess.run(
        [
            str(nextflow),
            "run",
            str(harness),
            "-c",
            str(config),
            "-w",
            str(tmp_path / "work"),
            "--bam",
            str(bam),
            "--bai",
            str(bam) + ".bai",
            "--reference",
            str(reference),
            "--bam_reference_sha256",
            declared_digest,
            "--bam_source_sha256",
            source_digest,
            "-ansi-log",
            "false",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )


def test_bam_without_m5_accepts_matching_trusted_reference_sequence_digest(tmp_path: Path) -> None:
    sequence_digest = hashlib.sha256(b"ACGTACGTACGT").hexdigest()
    completed = _run_validation(tmp_path, sequence_digest)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    validation_log = tmp_path / "published/bam_mapped_check.log"
    assert validation_log.is_file()
    log_text = validation_log.read_text(encoding="utf-8")
    assert "reference_identity=trusted_source_bam_and_reference_sha256" in log_text
    assert "validated_bam_sha256=" in log_text
    assert "validated_reference_sha256=" in log_text
    assert "mapped_reads=1" in log_text


def test_bam_without_m5_rejects_wrong_trusted_reference_sequence_digest(tmp_path: Path) -> None:
    completed = _run_validation(tmp_path, "0" * 64)

    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "Error executing process > 'ValidateMappedBam" in combined


def test_bam_without_m5_rejects_reference_claim_bound_to_different_source_bam(
    tmp_path: Path,
) -> None:
    sequence_digest = hashlib.sha256(b"ACGTACGTACGT").hexdigest()
    completed = _run_validation(
        tmp_path,
        sequence_digest,
        declared_source_digest="f" * 64,
    )

    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert "Error executing process > 'ValidateMappedBam" in combined
