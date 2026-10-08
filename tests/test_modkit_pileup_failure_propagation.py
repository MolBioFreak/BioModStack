"""Exercise the actual pileup task body; native Nextflow proof is separate."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _render_task_body() -> str:
    source = (ROOT / "modules/ngs/modkit_pileup.nf").read_text()
    process = source.split("process ModkitPileup {", 1)[1]
    body = process.split('"""', 2)[1]
    replacements = {
        "${bam}": "inert input.bam",
        "${reference}": "inert reference.fasta",
        "${filterThreshold}": "",
        "${task.cpus}": "1",
    }
    for token, value in replacements.items():
        body = body.replace(token, value)
    assert "${" not in body, "New task interpolation needs an explicit fixture value"
    return body.replace("\\\\", "\\")


@pytest.mark.parametrize("modkit_exit,tee_exit,expected", [(0, 0, 0), (42, 0, 42), (0, 23, 23)])
def test_pileup_propagates_producer_and_log_failures(tmp_path, modkit_exit, tee_exit, expected):
    bash = shutil.which("bash")
    tee = shutil.which("tee")
    assert bash and tee, "This shell-contract test needs bash and tee"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    producer = binaries / "modkit"
    producer.write_text(
        "#!/bin/bash\n"
        "[[ $1 == pileup ]] || exit 90\n"
        "printf 'INERT output, producer exit %s\\n' \"$MODKIT_TEST_EXIT\" > \"$3\"\n"
        "printf 'INERT producer log\\n'\n"
        "exit \"$MODKIT_TEST_EXIT\"\n"
    )
    producer.chmod(0o755)
    logger = binaries / "tee"
    logger.write_text(
        "#!/bin/bash\n"
        '"$REAL_TEE" "$@" || exit $?\n'
        'exit "$TEE_TEST_EXIT"\n'
    )
    logger.chmod(0o755)
    task = tmp_path / "task.sh"
    task.write_text(_render_task_body())
    env = os.environ.copy()
    env.pop("BASH_ENV", None)
    env.pop("SHELLOPTS", None)
    env.update(
        PATH=str(binaries) + os.pathsep + env.get("PATH", ""),
        MODKIT_TEST_EXIT=str(modkit_exit),
        TEE_TEST_EXIT=str(tee_exit),
        REAL_TEE=tee,
    )
    result = subprocess.run([bash, "-ue", str(task)], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == expected, result.stdout + result.stderr
    assert (tmp_path / "methylation.bed").read_text() == f"INERT output, producer exit {modkit_exit}\n"
    assert (tmp_path / "pileup.log").read_text() == "INERT producer log\n"
