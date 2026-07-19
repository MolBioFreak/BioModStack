from __future__ import annotations

import os
from pathlib import Path
import shutil
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


def _mapped_bam(tmp_path: Path, samtools: Path) -> Path:
    sam = tmp_path / "mapped.sam"
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        "@SQ\tSN:plasmid\tLN:12\n"
        "read1\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tACGTACGTACGT\tIIIIIIIIIIII\n",
        encoding="utf-8",
    )
    bam = tmp_path / "mapped.bam"
    subprocess.run(
        [str(samtools), "view", "-bS", str(sam), "-o", str(bam)],
        check=True,
        text=True,
        capture_output=True,
    )
    return bam


def test_bam_workflow_runs_without_reference_when_modkit_is_disabled(tmp_path: Path) -> None:
    nextflow, samtools = _runtime_tools()
    bam = _mapped_bam(tmp_path, samtools)
    config = tmp_path / "local.config"
    config.write_text(
        "process {\n"
        "  executor = 'local'\n"
        "  withLabel: dorado_cpu { container = null; cpus = 1; memory = '1 GB' }\n"
        "}\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    env = os.environ.copy()
    env["PATH"] = f"{samtools.parent}:{env.get('PATH', '')}"
    for key in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        env.pop(key, None)

    completed = subprocess.run(
        [
            str(nextflow),
            "run",
            str(ROOT / "workflows/ngs/ont_methylation_analysis.nf"),
            "-c",
            str(config),
            "-w",
            str(tmp_path / "work"),
            "--bam_path",
            str(bam),
            "--run_modkit",
            "false",
            "--out_dir",
            str(out_dir),
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

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (out_dir / "align/aligned.bam").is_file()
    assert (out_dir / "align/aligned.bam.bai").is_file()
    prepare_log = out_dir / "align/bam_prepare.log"
    assert prepare_log.is_file()
    assert "mapped_records=1" in prepare_log.read_text(encoding="utf-8")
    assert not (out_dir / "methylation/methylation.bed").exists()


@pytest.mark.parametrize(
    ("records", "expected_success"),
    [
        (
            [
                "read1\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tCCCCCCCCCCCC\tIIIIIIIIIIII\tMM:Z:C+m,0;\tML:B:C,255"
            ],
            True,
        ),
        (
            [
                "read1\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tCCCCCCCCCCCC\tIIIIIIIIIIII\tMM:Z:C+m;\tML:B:C,255"
            ],
            False,
        ),
        (
            [
                "read1\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tCCCCCCCCCCCC\tIIIIIIIIIIII\tMM:Z:C+m,0;",
                "read2\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tCCCCCCCCCCCC\tIIIIIIIIIIII\tML:B:C,255",
            ],
            False,
        ),
        (
            [
                "read1\t4\t*\t0\t0\t*\t*\t0\t0\tCCCCCCCCCCCC\tIIIIIIIIIIII\tMM:Z:C+m,0;\tML:B:C,255",
            ],
            False,
        ),
    ],
    ids=["paired-tags", "malformed-mm", "tags-split-across-records", "tagged-but-unmapped"],
)
def test_modified_base_tag_validation_requires_meaningful_pair_on_same_record(
    tmp_path: Path,
    records: list[str],
    expected_success: bool,
) -> None:
    nextflow = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    samtools = Path(os.environ.get("BMS_SAMTOOLS_BIN", "/home/dalab/micromamba/bin/samtools"))
    if not (nextflow.is_file() and os.access(nextflow, os.X_OK)):
        pytest.skip(f"Nextflow unavailable: {nextflow}")
    if not (samtools.is_file() and os.access(samtools, os.X_OK)):
        pytest.skip(f"samtools unavailable: {samtools}")

    sam = tmp_path / "modified.sam"
    bam = tmp_path / "modified.bam"
    bai = tmp_path / "modified.bam.bai"
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        "@SQ\tSN:plasmid\tLN:12\n"
        + "\n".join(records)
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [str(samtools), "view", "-bS", str(sam), "-o", str(bam)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        [str(samtools), "index", "-o", str(bai), str(bam)],
        check=True,
        text=True,
        capture_output=True,
    )

    harness = tmp_path / "modified_base_harness.nf"
    harness.write_text(
        "nextflow.enable.dsl=2\n"
        "params.bam = null\n"
        "params.bai = null\n"
        "params.out_dir = null\n"
        f"include {{ ValidateModifiedBaseBam }} from '{(ROOT / 'modules/ngs/modkit_pileup.nf').as_posix()}'\n"
        "workflow {\n"
        "  ValidateModifiedBaseBam(Channel.of(tuple(file(params.bam), file(params.bai))))\n"
        "}\n",
        encoding="utf-8",
    )
    config = tmp_path / "local.config"
    config.write_text(
        "process {\n"
        "  executor = 'local'\n"
        "  withLabel: dorado_cpu { container = null; cpus = 1; memory = '1 GB' }\n"
        "}\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    env = os.environ.copy()
    env["PATH"] = f"{samtools.parent}:{nextflow.parent}:{env.get('PATH', '')}"
    for key in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        env.pop(key, None)

    completed = subprocess.run(
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
            str(bai),
            "--out_dir",
            str(out_dir),
            "-ansi-log",
            "false",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )

    if expected_success:
        assert completed.returncode == 0, completed.stdout
        assert (out_dir / "methylation/modified_base_input.bam").is_file()
        assert (out_dir / "methylation/modified_base_input.bam.bai").is_file()
        log_text = (out_dir / "methylation/modified_base_tag_check.log").read_text(encoding="utf-8")
        assert "modified_base_tagged_records=1" in log_text
    else:
        assert completed.returncode != 0
        assert "Error executing process > 'ValidateModifiedBaseBam" in completed.stdout


def test_tagged_bam_runs_real_modkit_pileup_and_summary(tmp_path: Path) -> None:
    nextflow = Path(os.environ.get("BMS_NEXTFLOW_BIN", "/usr/local/bin/nextflow"))
    samtools = Path(os.environ.get("BMS_SAMTOOLS_BIN", "/home/dalab/micromamba/bin/samtools"))
    apptainer = Path(shutil.which("apptainer") or "/usr/bin/apptainer")
    dorado_sif = Path(os.environ.get("BMS_DORADO_SIF", "/mnt/BioModStack/apptainer/dorado.sif"))
    for tool in (nextflow, samtools, apptainer, dorado_sif):
        if not tool.is_file():
            pytest.skip(f"real modkit runtime prerequisite unavailable: {tool}")

    reference = tmp_path / "reference.fasta"
    reference.write_text(">plasmid\nCCCCCCCCCCCC\n", encoding="utf-8")
    subprocess.run([str(samtools), "faidx", str(reference)], check=True, capture_output=True, text=True)

    sam = tmp_path / "tagged.sam"
    bam = tmp_path / "tagged.bam"
    bai = tmp_path / "tagged.bam.bai"
    sam.write_text(
        "@HD\tVN:1.6\tSO:coordinate\n"
        "@SQ\tSN:plasmid\tLN:12\n"
        "read1\t0\tplasmid\t1\t60\t12M\t*\t0\t0\tCCCCCCCCCCCC\tIIIIIIIIIIII"
        "\tMM:Z:C+m,0;\tML:B:C,255\tMN:i:12\n",
        encoding="utf-8",
    )
    subprocess.run(
        [str(samtools), "view", "-bS", str(sam), "-o", str(bam)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(samtools), "index", "-o", str(bai), str(bam)],
        check=True,
        capture_output=True,
        text=True,
    )

    wrapper_dir = tmp_path / "bin"
    wrapper_dir.mkdir()
    modkit_wrapper = wrapper_dir / "modkit"
    modkit_wrapper.write_text(
        "#!/bin/sh\n"
        f"exec {apptainer} exec {dorado_sif} modkit \"$@\"\n",
        encoding="utf-8",
    )
    modkit_wrapper.chmod(0o755)

    harness = tmp_path / "modkit_harness.nf"
    harness.write_text(
        "nextflow.enable.dsl=2\n"
        "params.bam = null\n"
        "params.bai = null\n"
        "params.reference = null\n"
        "params.out_dir = null\n"
        "params.modkit_filter_threshold = null\n"
        f"include {{ ValidateModifiedBaseBam; ModkitPileup }} from '{(ROOT / 'modules/ngs/modkit_pileup.nf').as_posix()}'\n"
        f"include {{ ModkitSummary }} from '{(ROOT / 'modules/ngs/modkit_summary.nf').as_posix()}'\n"
        "workflow {\n"
        "  input_bam = Channel.of(tuple(file(params.bam), file(params.bai)))\n"
        "  ValidateModifiedBaseBam(input_bam)\n"
        "  ModkitPileup(ValidateModifiedBaseBam.out.bam, Channel.of(file(params.reference)))\n"
        "  ModkitSummary(ValidateModifiedBaseBam.out.bam)\n"
        "}\n",
        encoding="utf-8",
    )
    config = tmp_path / "local.config"
    config.write_text(
        "process {\n"
        "  executor = 'local'\n"
        "  withLabel: dorado_cpu { container = null; cpus = 1; memory = '1 GB' }\n"
        "}\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    env = os.environ.copy()
    env["PATH"] = f"{wrapper_dir}:{samtools.parent}:{nextflow.parent}:{env.get('PATH', '')}"
    for key in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
        env.pop(key, None)
    completed = subprocess.run(
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
            str(bai),
            "--reference",
            str(reference),
            "--out_dir",
            str(out_dir),
            "-ansi-log",
            "false",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )

    assert completed.returncode == 0, completed.stdout
    methylation_dir = out_dir / "methylation"
    required = [
        "modified_base_input.bam",
        "modified_base_input.bam.bai",
        "modified_base_tag_check.log",
        "methylation.bed",
        "pileup.log",
        "modkit_summary.tsv",
        "summary.log",
    ]
    for name in required:
        assert (methylation_dir / name).is_file(), name
    assert (methylation_dir / "methylation.bed").stat().st_size > 0
    assert (methylation_dir / "modkit_summary.tsv").stat().st_size > 0
    assert "modified_base_tagged_records=1" in (
        methylation_dir / "modified_base_tag_check.log"
    ).read_text(encoding="utf-8")
