"""Actual native task-shell size/cohort controls; scientific mapping is not run.

The DEBUG recorder terminates after the unchanged real cohort calculation. This
is deliberately a nonzero Nextflow task, not a claim of full QC completion.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("reference_length,read_length,override,expected,dimer,trimer,cohort,ratio", [
    (3000, 6000, None, 3000, 4500, 7500, 1, 2.0),
    (12000, 12000, None, 12000, 18000, 30000, 0, 1.0),
    (12000, 12000, 7000, 7000, 10500, 17500, 1, 12000 / 7000),
    (3001, 6002, None, 3001, 4502, 7503, 1, 2.0),
])
@pytest.mark.parametrize("process", ["FastqPlasmidQC", "FastqDimerAnalysis"])
def test_native_task_effective_size(tmp_path, process, reference_length, read_length, override, expected, dimer, trimer, cohort, ratio):
    launcher = os.environ.get("BMS_TEST_NEXTFLOW_LAUNCHER")
    jar = os.environ.get("BMS_TEST_NEXTFLOW_JAR")
    if not launcher or not jar:
        pytest.skip("requires explicitly selected offline Nextflow launcher and cached JAR")
    jar = Path(jar)
    version = jar.parent.name
    home = tmp_path / "nxf-home"
    framework = home / "framework" / version
    framework.mkdir(parents=True)
    (framework / jar.name).symlink_to(jar)
    reference = tmp_path / "ref.fasta"
    sequence = "A" * reference_length
    reference.write_text(f">fixture\n{sequence}\n")
    fastq = tmp_path / "reads.fastq"
    fastq.write_text(f"@read-1\n{'A' * read_length}\n+\n{'I' * read_length}\n")
    for name in ("reads.bam", "reads.bam.bai"):
        (tmp_path / name).write_bytes(b"inert: never mapped")
    # Only reference indexing/extraction is needed before the recorder. No fake
    # alignment/consensus result is produced or accepted as scientific output.
    binary = tmp_path / "bin"
    binary.mkdir()
    samtools = binary / "samtools"
    samtools.write_text("""#!/usr/bin/env python3
import pathlib, sys
assert sys.argv[1] == 'faidx', sys.argv
p = pathlib.Path(sys.argv[2]); lines = p.read_text().splitlines()
name = lines[0][1:]; seq = ''.join(lines[1:])
if len(sys.argv) > 3: print('>' + name + '\\n' + seq)
else: pathlib.Path(str(p) + '.fai').write_text(name + '\\t' + str(len(seq)) + '\\t0\\t0\\t0\\n')
""")
    samtools.chmod(0o700)
    record = tmp_path / "observed.tsv"
    hook = tmp_path / "record.sh"
    hook.write_text("""record_size() {
  case "$BASH_COMMAND" in
    mapped_alignment_records=*)
      printf '%s\\t%s\\t%s\\t%s\\t%s\\n' "$expected_size" "$dimer_cutoff" "$trimer_cutoff" "$dimer_like_reads" "$estimated_copy_number_mean" > "$SIZE_RECORD"
      exit 42 ;;
    aligned_reads=0)
      printf '%s\\t%s\\t%s\\t%s\\n' "$expected_size" "$dimer_cutoff" "$trimer_cutoff" "$dimer_count" > "$SIZE_RECORD"
      exit 42 ;;
  esac
}
trap record_size DEBUG
""")
    module = "fastq_plasmid_qc" if process == "FastqPlasmidQC" else "fastq_dimer_qc"
    args = "Channel.of(tuple(file(params.bam),file(params.bai))), " if process == "FastqPlasmidQC" else "Channel.of(file(params.fastq)), "
    args += "Channel.of(file(params.reference))"
    if process == "FastqPlasmidQC":
        args += ", Channel.of(file(params.fastq))"
    harness = tmp_path / "main.nf"
    harness.write_text(f"""nextflow.enable.dsl=2
include {{ {process} }} from '{ROOT}/modules/ngs/{module}.nf'
workflow {{ {process}({args}) }}
""")
    config = tmp_path / "local.config"
    config.write_text(f"""process.executor='local'
process.container=null
process.cpus=1
process.memory='512 MB'
process.errorStrategy='terminate'
process.shell=['/bin/bash','-ue']
process.beforeScript='export PATH="{binary}:$PATH"; export BASH_ENV="{hook}"; export SIZE_RECORD="{record}"'
singularity.enabled=false
docker.enabled=false
""")
    params = dict(reference=str(reference), fastq=str(fastq), bam=str(tmp_path / "reads.bam"),
                  bai=str(tmp_path / "reads.bam.bai"), code_root=str(ROOT), job_id="size-fixture",
                  reference_sequence_sha256=hashlib.sha256(sequence.encode()).hexdigest(),
                  expected_plasmid_size=override, out_dir=str(tmp_path / "out"))
    params_file = tmp_path / "params.json"
    params_file.write_text(json.dumps(params))
    env = dict(os.environ, NXF_HOME=str(home), NXF_VER=version, NXF_OFFLINE="true", NXF_DISABLE_CHECK_LATEST="true", NXF_ANSI_LOG="false")
    completed = subprocess.run([launcher, "-C", str(config), "run", str(harness), "-params-file", str(params_file), "-w", str(tmp_path / "work")],
                               cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    (tmp_path / "nextflow-output.log").write_text(completed.stdout + completed.stderr)
    assert completed.returncode != 0
    assert record.exists(), completed.stdout + completed.stderr
    values = record.read_text().strip().split("\t")
    assert list(map(int, values[:4])) == [expected, dimer, trimer, cohort]
    if process == "FastqPlasmidQC":
        assert float(values[4]) == pytest.approx(ratio, abs=0.00005)
    commands = list((tmp_path / "work").rglob(".command.sh"))
    assert len(commands) == 1
    assert 'expected_size="null"' not in commands[0].read_text()
    assert commands[0].with_name(".exitcode").read_text().strip() == "42"
    if process == "FastqDimerAnalysis":
        candidates = commands[0].parent / "dimer_candidates.fastq"
        assert ("@read-1" in candidates.read_text()) == bool(cohort)
