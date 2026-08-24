from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_dorado_image_pins_the_approved_consensus_runtime() -> None:
    image = (ROOT / "apptainer/dorado.def").read_text(encoding="utf-8")

    assert "https://github.com/samtools/samtools/releases/download/1.24/samtools-1.24.tar.bz2" in image
    assert "89b2a440123eeaa400392ce1736e7d60ce9041843027d76819753c5a8246bfdd" in image
    assert 'SAMTOOLS_VERSION="1.24"' in image
    assert "./configure --prefix=/usr/local" in image
    assert 'samtools_version="$(samtools --version)"' in image
    assert 'test "$1" = "samtools"' in image
    assert 'test "$2" = "1.24"' in image
    assert '"igv-reports==${IGV_REPORTS_VERSION}"' in image
    assert 'IGV_REPORTS_VERSION="1.16.3"' in image
    assert "test -x /usr/local/bin/create_report" in image
    assert 'IGV_JS_VERSION="3.5.2"' in image
    assert "0efd638a0997aa90791ce6c83a8b33912d4bc06aed5e28741fc74f32a20998d6" in image
    assert 'https://cdn.jsdelivr.net/npm/igv@${IGV_JS_VERSION}/dist/igv.min.js' in image
    assert "templates/ngs/igv_variant_standalone.html" in image
    assert 'MODKIT_VERSION="0.6.4"' in image
    assert "modkit_v0.6.4_u16_x86_64.tar.gz" in image
    assert "fb332c691431bd336eb0a81cbca17d2a35caf442ac48277ed3e296c2fe061d80" in image
    assert 'test "$(modkit --version)" = "modkit ${MODKIT_VERSION}"' in image
    assert 'MODKIT_VERSION="latest"' not in image


def test_fastq_qc_has_one_authoritative_fail_closed_method() -> None:
    module = (ROOT / "modules/ngs/fastq_plasmid_qc.nf").read_text(encoding="utf-8")
    config = (ROOT / "nextflow.config").read_text(encoding="utf-8")

    assert "withLabel: fastq_qc_cpu" in config
    assert 'container = "${params.container_dir}/dorado.sif"' in config
    assert "apptainer" not in module
    assert "mpileup" not in module
    assert "fallback" not in module.lower()
    assert "bcftools_consensus" not in module
    assert "consensus --mode bayesian -f fasta" in module
    assert "CRITICAL_FAILURE: SAMTOOLS_CONSENSUS_FAILED" in module
    assert "CRITICAL_FAILURE: SAMTOOLS_CONSENSUS_EMPTY" in module
    assert "CRITICAL_FAILURE: IGV_REPORT_CREATE_REPORT_UNAVAILABLE" in module
    assert "CRITICAL_FAILURE: IGV_REPORT_CREATE_REPORT_FAILED" in module
    assert "CRITICAL_FAILURE: IGV_REPORT_VALIDATE_FAILED" in module
    assert "build_small_igv_report_inputs.py" in module
    assert "finalize_small_igv_report.py" not in module
    assert "--reference-index reference_qc.fasta.fai" in module
    assert "stageAs: 'source-aligned.bam'" in module
    assert "stageAs: 'source-aligned.bam.bai'" in module
    assert 'cp -- "${bam}" aligned.bam' in module
    assert 'cp -- "${bai}" aligned.bam.bai' in module
    assert "--out-reference-config igv_reference_config.json" in module
    assert "--fasta reference_qc.fasta" in module
    assert "igv_reference_uri" not in module
    assert "--standalone" in module
    assert "--subsample 0.002" in module
    assert "--no-embed" not in module
    assert "--template /opt/bms/igv-reports/igv_variant_standalone.html" in module
    assert "igv_standalone_track_config.json" in module
    assert "validate_standalone_igv_report.py" in module
    assert "IGV_REPORT_ARTIFACT_OVERSIZED" in module
    assert "67108864" in module
    assert '"url": "\\${bam_local}"' not in module
    assert "/api/files/" not in module
    assert "<!doctype html>" not in module
    assert "IGV Report Fallback" not in module
    assert 'path "fastq_consensus.fasta", optional: true' not in module


def test_portable_igv_template_uses_only_the_pinned_local_runtime_asset() -> None:
    template = (ROOT / "templates/ngs/igv_variant_standalone.html").read_text(encoding="utf-8")

    assert 'src="file:///opt/bms/igv-reports/igv.min.js"' in template
    assert "loadDefaultGenomes: false" in template
    assert "cdn.jsdelivr.net" not in template


def test_dimer_consensus_has_no_reference_or_majority_fallback() -> None:
    dimer = (ROOT / "modules/ngs/fastq_dimer_qc.nf").read_text(encoding="utf-8")
    dominant = (ROOT / "scripts/dominant_dimer_consensus.sh").read_text(encoding="utf-8")

    for text in (dimer, dominant):
        assert "consensus --mode bayesian -f fasta" in text
        assert "mpileup" not in text
        assert "fallback" not in text.lower()
    assert "dimer_reference.fasta" not in dominant
    assert "--fastq" not in dominant
    assert "--dimer-consensus" not in dominant
    assert "--dimer-reference" not in dominant
    assert "most_abundant" not in dominant


def test_demux_rejects_unknown_labels_and_uses_exact_barcode_grammar() -> None:
    demux = (ROOT / "modules/ngs/dorado_basecall.nf").read_text(encoding="utf-8")
    units = (ROOT / "platform/api/services/ont_barcode_units.py").read_text(encoding="utf-8")

    assert "barcode(0[1-9]|[1-8][0-9]|9[0-6])" in demux
    assert "barcode(?:0[1-9]|[1-8][0-9]|9[0-6])" in units
    assert "CRITICAL_FAILURE: UNKNOWN_DEMUX_LABEL" in demux
    assert "printf '%s\\n' unclassified" not in demux
    assert "barcode[0-9]+" not in demux


def test_verification_input_uses_the_samtools_consensus_label() -> None:
    fastq = (ROOT / "modules/ngs/fastq_plasmid_qc.nf").read_text(encoding="utf-8")
    assert "--consensus-method samtools_consensus" in fastq
    assert "bcftools_consensus" not in fastq


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


def test_fastq_qc_requires_exact_job_identity() -> None:
    fastq = (ROOT / "modules/ngs/fastq_plasmid_qc.nf").read_text(encoding="utf-8")
    manifest = (ROOT / "scripts/build_sequence_qc_manifest.py").read_text(encoding="utf-8")
    assert "FASTQ plasmid QC requires an exact job_id" in fastq
    assert "nanopore-fastq-qc" not in fastq
    assert 'parser.add_argument("--job-id", required=True)' in manifest
    assert 'str(job_id or "unknown")' not in manifest


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
    printf '@HD\\tVN:1.6\\tSO:coordinate\\n'
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
