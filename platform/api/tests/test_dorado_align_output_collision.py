from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
NEXTFLOW_IMAGE = "nextflow/nextflow@sha256:613bb4051cfc88f56dde10bba14b090db9a9b6dac164d36fa8e263a9aab78211"


@pytest.mark.runtime_integration
def test_dorado_align_publishes_reference_when_input_has_same_basename(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    image = subprocess.run(
        [docker, "image", "inspect", NEXTFLOW_IMAGE],
        text=True,
        capture_output=True,
        check=False,
    )
    assert image.returncode == 0, image.stdout + image.stderr

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    bam = inputs / "source.bam"
    bam.write_bytes(b"immutable-bam-fixture\n")
    reference = inputs / "reference.fasta"
    reference.write_text(">eGFP\nACGT\n", encoding="utf-8")
    reference.chmod(0o444)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    dorado = fake_bin / "dorado"
    dorado.write_text("#!/usr/bin/env bash\nprintf '@HD\\tVN:1.6\\n'\n", encoding="utf-8")
    dorado.chmod(0o755)
    samtools = fake_bin / "samtools"
    samtools.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  sort)
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == "-o" ]]; then
        cat >/dev/null
        printf 'aligned-bam-fixture\\n' > "$2"
        exit 0
      fi
      shift
    done
    ;;
  index) printf 'index\\n' > "$2.bai" ;;
  faidx) printf 'eGFP\\t4\\t6\\t4\\t5\\n' > "$2.fai" ;;
  view) printf '1\\n' ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    samtools.chmod(0o755)

    out_dir = tmp_path / "results"
    harness = tmp_path / "main.nf"
    harness.write_text(
        f"""nextflow.enable.dsl = 2
include {{ DoradoAlign }} from '/workspace/modules/ngs/dorado_align.nf'
workflow {{
    DoradoAlign(Channel.value(file('/run/inputs/source.bam')), Channel.value(file('/run/inputs/reference.fasta')))
}}
""",
        encoding="utf-8",
    )

    try:
        completed = subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "--network",
                "none",
                "-e",
                "HOME=/run/home",
                "-e",
                "NXF_OFFLINE=true",
                "-e",
                "NXF_DISABLE_CHECK_LATEST=true",
                "-e",
                "PATH=/run/bin:/usr/local/bin:/usr/bin:/bin",
                "-v",
                f"{REPO_ROOT}:/workspace:ro",
                "-v",
                f"{tmp_path}:/run:rw",
                "-v",
                f"{reference}:/run/inputs/reference.fasta:ro",
                "-w",
                "/run",
                NEXTFLOW_IMAGE,
                "nextflow",
                "run",
                "/run/main.nf",
                "--out_dir",
                "/run/results",
                "--bam_min_mapq",
                "0",
                "--bam_source_sha256",
                hashlib.sha256(bam.read_bytes()).hexdigest(),
                "--reference_sequence_sha256",
                hashlib.sha256(b"ACGT").hexdigest(),
                "-offline",
                "-work-dir",
                "/run/work",
            ],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    finally:
        cleanup = subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{tmp_path}:/run:rw",
                "--entrypoint",
                "/bin/chmod",
                NEXTFLOW_IMAGE,
                "-R",
                "a+rwX",
                "/run",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        assert cleanup.returncode == 0, cleanup.stdout + cleanup.stderr
    assert completed.returncode == 0, completed.stdout + completed.stderr
    published_reference = out_dir / "align" / "reference.fasta"
    assert published_reference.read_bytes() == reference.read_bytes()
    assert (out_dir / "align" / "reference.fasta.fai").is_file()
