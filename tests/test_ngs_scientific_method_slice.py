from pathlib import Path
import hashlib
import json
import os
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_primary_alignment_manifest_builder_binds_exact_artifacts(tmp_path: Path) -> None:
    artifacts = {
        "aligned.bam": b"bam",
        "aligned.bam.bai": b"bai",
        "reference.fasta": b">ref\nACGT\n",
        "reference.fasta.fai": b"ref\t4\t5\t4\t5\n",
    }
    for name, content in artifacts.items():
        (tmp_path / name).write_bytes(content)
    reference_sha256 = hashlib.sha256(b"ACGT").hexdigest()
    script = ROOT / "scripts/build_primary_alignment_session_manifest.sh"

    completed = subprocess.run(
        [
            "bash",
            str(script),
            "job-primary-1",
            reference_sha256,
            "ont_plasmid_qc",
            "bam",
            "qc_manifest.json",
            "circular",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((tmp_path / "qc_manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["reference_topology"] == "circular"
    assert manifest["alignment_session"] == {
        "mode": "primary",
        "reference_sequence_sha256": reference_sha256,
        "source_reference_sequence_sha256": reference_sha256,
        "binding": "authorized source reference binds exact primary BAM, index, FASTA, and index digests",
    }
    declared = {item["kind"]: item for item in manifest["artifacts"]}
    assert set(declared) == {"alignment_bam", "alignment_bai", "reference", "reference_index"}
    assert declared["alignment_bam"]["path"] == "align/aligned.bam"
    assert declared["reference"]["sha256"] == hashlib.sha256(artifacts["reference.fasta"]).hexdigest()
    assert all(item["required"] is True and item["state"] == "present" for item in declared.values())


def test_fastq_qc_uses_primary_logical_read_accounting() -> None:
    fastq = (ROOT / "modules/ngs/fastq_plasmid_qc.nf").read_text(encoding="utf-8")
    assert "source_total_reads" in fastq
    assert "reads_passing_length_filter" in fastq
    assert 'view -c -F 2308 "${bam}"' in fastq
    assert 'view -c -f 4 -F 2304 "${bam}"' in fastq
    assert "logical_read_records=\\$((mapped_reads + unmapped_reads))" in fastq
    assert "CRITICAL_FAILURE: FASTQ_BAM_READ_ACCOUNTING_MISMATCH" in fastq
    assert 'total_alignment_records=\\$((mapped_alignment_records + unmapped_alignment_records))' in fastq
    assert 'mapping_rate_pct=\\$(awk -v mapped="\\${mapped_reads}" -v total="\\${source_total_reads}"' in fastq


def test_fastq_qc_manifest_uses_persisted_workflow_and_input_authority() -> None:
    fastq = (ROOT / "modules/ngs/fastq_plasmid_qc.nf").read_text(encoding="utf-8")
    assert "params.workflow_id ?: params.ont_workflow_id" in fastq
    assert "['ont_fastq_qc', 'ont_plasmid_qc', 'ont_construct_screening', 'wf_clone_validation']" in fastq
    assert "params.input_mode ?: params.ont_input_mode" in fastq
    assert "['fastq', 'bam', 'pod5']" in fastq
    assert "--workflow-id ${workflowIdArg}" in fastq
    assert "--input-mode ${inputModeArg}" in fastq
    assert "--workflow-id ont_fastq_qc" not in fastq


def test_dimer_manifest_binds_exact_job_identity_and_canonical_schema() -> None:
    dimer = (ROOT / "modules/ngs/fastq_dimer_qc.nf").read_text(encoding="utf-8")
    manifest = (ROOT / "scripts/build_alignment_session_manifest.sh").read_text(encoding="utf-8")
    python_manifest = (ROOT / "scripts/build_alignment_session_manifest.py").read_text(encoding="utf-8")

    assert "manifestJobId" in dimer
    assert 'build_alignment_session_manifest.sh" \\' in dimer
    assert "${manifestJobIdArg}" in dimer
    assert "declaredReferenceSha256" in dimer
    assert "REFERENCE_DIGEST_MISMATCH" in dimer
    assert "${referenceSequenceSha256Arg}" in dimer
    assert "${workflowIdArg}" in dimer
    assert 'job_id="${1:?exact job_id is required}"' in manifest
    assert 'expected_source_reference_sha256="${2:?authorized source reference SHA-256 is required}"' in manifest
    assert 'workflow_id="${3:?canonical workflow_id is required}"' in manifest
    assert 'input_mode="${4:?canonical input_mode is required}"' in manifest
    assert 'schema:"sequence_qc.manifest.v1"' in manifest
    assert 'workflow_id:$workflow_id' in manifest
    assert 'input_mode:$input_mode' in manifest
    assert 'analysis_status:"completed"' in manifest
    assert 'job_id:$job_id' in manifest
    assert 'parser.add_argument("--job-id", required=True)' in python_manifest
    assert 'parser.add_argument("--expected-source-reference-sha256", required=True)' in python_manifest
    assert 'parser.add_argument("--workflow-id", required=True)' in python_manifest
    assert 'parser.add_argument("--input-mode", choices=("fastq", "bam", "pod5"), required=True)' in python_manifest
    assert '"schema": "sequence_qc.manifest.v1"' in python_manifest
    assert '"workflow_id": args.workflow_id' in python_manifest
    assert '"input_mode": args.input_mode' in python_manifest
    assert '"analysis_status": "completed"' in python_manifest
    assert '"job_id": args.job_id' in python_manifest


def test_nextflow_launcher_binds_canonical_ont_workflow_identity() -> None:
    import sys

    api_root = ROOT / "platform/api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    from services.nextflow import build_nextflow_command  # type: ignore[import-not-found]

    command = build_nextflow_command(
        "nanopore",
        "ont_fastq_qc",
        {
            "fastq_path": "/tmp/reads.fastq",
            "reference_fasta": "/tmp/reference.fasta",
            "reference_sequence_sha256": "a" * 64,
        },
        "/tmp/results/job-1",
        job_id="job-1",
    )
    assert command[command.index("--workflow_id") + 1] == "ont_fastq_qc"


def test_dominant_dimer_consensus_fails_without_fabricating_output(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "samtools").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  view)
    if [[ "$2" == "-h" ]]; then printf '@HD\\tVN:1.6\\tSO:coordinate\\n'; fi
    printf 'r1\\t0\\tplasmid\\t1\\t60\\t4M\\t*\\t0\\t0\\tACGT\\tIIII\\n'
    ;;
  sort)
    output=""
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == "-o" ]]; then output="$2"; shift 2; else shift; fi
    done
    printf 'synthetic bam\\n' > "$output"
    ;;
  index)
    exit 0
    ;;
  consensus)
    echo 'synthetic samtools consensus failure' >&2
    exit 42
    ;;
  *)
    exit 2
    ;;
esac
""",
        encoding="utf-8",
    )
    (fake_bin / "samtools").chmod(0o755)
    events = tmp_path / "events.tsv"
    events.write_text("read_id\tstart\tend\tposition_mod_ref\tcrosses_junction\nr1\t1\t2\t5\t1\n", encoding="utf-8")
    bam = tmp_path / "candidates.bam"
    bam.write_bytes(b"synthetic bam")
    output = tmp_path / "dominant.fasta"
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/dominant_dimer_consensus.sh"),
            "--events", str(events),
            "--bam", str(bam),
            "--dimer-count", "1",
            "--screened-pos", "5",
            "--screened-support", "1",
            "--out-consensus", str(output),
            "--out-log", str(tmp_path / "dominant.log"),
            "--out-metadata", str(tmp_path / "dominant.tsv"),
        ],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 86
    assert "CRITICAL_FAILURE: SAMTOOLS_CONSENSUS_FAILED" in result.stderr
    assert not output.exists()
