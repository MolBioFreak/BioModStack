from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _nextflow_env(tmp_path: Path) -> dict[str, str]:
    """Use an installed framework only; never reuse historical task/cache state."""
    # Parse the unchanged root config directly: Nextflow's legacy parser
    # cannot resolve script methods when this file is nested via includeConfig.
    shutil.copyfile(ROOT / "nextflow.config", tmp_path / "nextflow.config")
    env = os.environ.copy()
    version = env.get("NXF_VER", "25.10.0")
    home = tmp_path / "nxf-home"
    jar_name = f"nextflow-{version}-one.jar"
    distribution = Path(env.get("NXF_DIST", str(Path.home() / ".nextflow/framework")))
    jar = distribution / version / jar_name
    if not jar.is_file():
        pytest.skip(f"installed offline Nextflow framework unavailable: {jar}")
    destination = home / "framework" / version / jar_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(jar, destination)
    env.update(NXF_HOME=str(home), NXF_DIST=str(home / "framework"),
               NXF_VER=version, NXF_OFFLINE="true", NXF_DISABLE_CHECK_LATEST="true")
    for name in ("NXF_TEMP", "NXF_CACHE_DIR", "APPTAINER_CACHEDIR", "APPTAINER_TMPDIR",
                 "SINGULARITY_CACHEDIR", "SINGULARITY_TMPDIR", "XDG_CACHE_HOME", "TMPDIR"):
        directory = tmp_path / name.lower()
        directory.mkdir(exist_ok=True)
        env[name] = str(directory)
    return env


def _ngs_container_config() -> str:
    """Run native tools in the image with the genuine API Python installation.

    The old image supplies samtools/modkit but not the Python path expected by
    the current validator. Bind the actual interpreter and environment, never a
    fake command, alternate parser, or synthetic producer identity.
    """
    image = Path(os.environ.get("BMS_DORADO_SIF", "/mnt/BioModStack/apptainer/dorado.sif"))
    if not shutil.which("apptainer") or not image.is_file():
        pytest.skip(f"real NGS container runtime unavailable: {image}")
    python_base = Path(sys.executable).resolve().parents[2]
    binds = f"--bind {ROOT} --bind {sys.prefix}:/opt/igv-reports:ro --bind {python_base}:{python_base}:ro"
    return (
        "apptainer.enabled = true\n"
        "apptainer.autoMounts = true\n"
        "process {\n"
        "  executor = 'local'\n"
        "  withLabel: dorado_cpu {\n"
        f"    container = '{image}'\n"
        f"    containerOptions = '{binds}'\n"
        "    cpus = 1; memory = '1 GB'\n"
        "  }\n"
        "}\n"
    )


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
    env = _nextflow_env(tmp_path)
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
        cwd=tmp_path,
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
            True,
        ),
    ],
    ids=["paired-tags", "cardinality-mismatch", "tags-split-across-records", "tagged-but-unmapped"],
)
def test_modified_base_tag_validation_enforces_sam_semantics(
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
    config.write_text(_ngs_container_config(), encoding="utf-8")
    out_dir = tmp_path / "out"
    env = _nextflow_env(tmp_path)
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
        cwd=tmp_path,
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
        errors = completed.stdout + "".join(
            path.read_text() for path in (tmp_path / "work").rglob(".command.err")
        )
        assert "malformed_modified_base_tags" in errors, errors
        assert not (out_dir / "methylation/modified_base_input.bam").exists()


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
    config.write_text(_ngs_container_config(), encoding="utf-8")
    out_dir = tmp_path / "out"
    env = _nextflow_env(tmp_path)
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
            "--reference",
            str(reference),
            "--out_dir",
            str(out_dir),
            "-ansi-log",
            "false",
        ],
        cwd=tmp_path,
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
    rows = [line.split("\t") for line in (methylation_dir / "methylation.bed").read_text().splitlines()]
    modified = next(row for row in rows if row[:4] == ["plasmid", "0", "1", "m"])
    assert modified[5] == "+"
    assert modified[9:13] == ["1", "100.00", "1", "0"]
    assert (methylation_dir / "modkit_summary.tsv").stat().st_size > 0
    assert "modified_base_tagged_records=1" in (
        methylation_dir / "modified_base_tag_check.log"
    ).read_text(encoding="utf-8")
