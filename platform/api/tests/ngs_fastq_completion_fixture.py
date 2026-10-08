"""Tiny real BAM/QC/ConstructVerify package, not a scientific cloud run."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pysam

from ngs_producer_fixtures import ROOT, producer_receipt


def native_fastq_package(root, job, tmp_path):
    """Seed one known alignment; run production table/manifest/verification tools."""
    align, qc, multi, verify = (root / name for name in ("align", "fastq_qc", "multimer_qc", "verification"))
    for directory in (align, qc, multi, verify):
        directory.mkdir(parents=True, exist_ok=True)
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    reference = align / "reference.fasta"
    reference.write_text(">ref\nACGTACGT\n")
    pysam.faidx(str(reference))
    reads = tmp_path / "input.fastq"
    reads.write_text("@read-1\nACGTACGT\n+\nIIIIIIII\n")
    job.params.update(fastq_path=str(reads), reference_fasta=str(reference),
                      reference_sequence_sha256=hashlib.sha256(b"ACGTACGT").hexdigest())
    bam = align / "aligned.bam"
    with pysam.AlignmentFile(str(bam), "wb", header={"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "ref", "LN": 8}]}) as output:
        read = pysam.AlignedSegment(output.header)
        read.query_name, read.query_sequence = "read-1", "ACGTACGT"
        read.query_qualities = pysam.qualitystring_to_array("IIIIIIII")
        read.reference_id, read.reference_start = 0, 0
        read.mapping_quality, read.cigarstring = 60, "8M"
        output.write(read)
    pysam.index(str(bam))
    receipts = {
        "source_sha256_before": sha(reads), "source_sha256_after": sha(reads),
        "source_immutable": "true", "reference_immutable": "true",
        "reference_raw_sha256_before": sha(reference), "reference_raw_sha256_after": sha(reference),
        "reference_sequence_sha256": job.params["reference_sequence_sha256"],
        "fastq_minimap2_preset": "map-ont", "fastq_minimap2_allow_secondary": "false",
    }
    (align / "fastq_align.log").write_text("".join(f"{key}={value}\n" for key, value in receipts.items())
        + producer_receipt(align / "fixture-producer", ("modules/ngs/fastq_align.nf",), ("minimap2", "samtools")))
    for source, target in (("aligned.bam", "aligned.bam"), ("aligned.bam.bai", "aligned.bam.bai"),
                           ("reference.fasta", "reference_qc.fasta"), ("reference.fasta.fai", "reference_qc.fasta.fai")):
        (qc / target).write_bytes((align / source).read_bytes())
    # Real bundled samtools, including binary output, not successful-command stubs.
    samtools = tmp_path / "fixture-samtools"
    samtools.write_text(f"#!{sys.executable}\nimport sys,pysam\n"
        "try: result=getattr(pysam,sys.argv[1])(*sys.argv[2:])\n"
        "except pysam.SamtoolsError as e: print(e,file=sys.stderr);sys.exit(1)\n"
        "if isinstance(result,bytes): sys.stdout.buffer.write(result)\n"
        "elif result: sys.stdout.write(result)\n")
    samtools.chmod(0o755)
    def run(script, *args):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    run("build_fastq_support_tables.py", "--bam", bam, "--reference-fasta", reference,
        "--out-per-base-support", qc / "per_base_support.tsv", "--samtools-cmd", samtools)
    consensus = qc / "fastq_consensus.fasta"
    consensus.write_text(pysam.consensus("-f", "fasta", str(bam)))
    pysam.faidx(str(consensus))
    (qc / "fastq_consensus.log").write_text("fixture: real bundled samtools consensus\n")
    (qc / "read_lengths.tsv").write_text("read_id\tlength_bp\nread-1\t8\n")
    (qc / "fastq_coverage.tsv").write_text("reference\tposition\tdepth\n" + "".join(f"ref\t{pos}\t1\n" for pos in range(1, 9)))
    settings = {"expected_plasmid_size": 7000, "min_fastq_read_length": 0,
        "fastq_minimap2_preset": "map-ont", "fastq_minimap2_allow_secondary": "false",
        "igv_track_window_bp": 100, "igv_report_max_sites": 40, "igv_report_flanking_bp": 200,
        "total_reads": 1, "mapped_reads": 1, "unmapped_reads": 0}
    (qc / "fastq_alignment_stats.tsv").write_text("metric\tvalue\n" + "".join(f"{key}\t{value}\n" for key, value in settings.items()))
    (qc / "fastq_qc_summary.tsv").write_text("metric\tvalue\nreads\t1\n")
    from services.ont_ngs_native_plasmid import _QC_FILES
    for filename in _QC_FILES:
        path = qc / filename
        if filename.endswith(".bedgraph"):
            path.write_text("ref\t0\t8\t0\n")
        elif filename.endswith(".bed"):
            path.write_text("")
    (qc / "igv_report_sites.tsv").write_text("reference\tposition\n")
    (qc / "igv_track_config.json").write_text(json.dumps([{"url": "igv_coverage_depth.bedgraph"}] * 8))
    (qc / "igv_report.html").write_text("<html><body>tiny fixture alignment</body></html>")
    (qc / "igv_report.log").write_text("")
    (multi / "summary-input.tsv").write_text("metric\tvalue\n")
    (multi / "events-input.tsv").write_text("read_id\tstart\tend\n")
    run("build_dimer_canonical_outputs.py", "--summary", multi / "summary-input.tsv",
        "--events", multi / "events-input.tsv", "--reference-fasta", reference,
        "--out-call", multi / "dimer_breakpoint_call.tsv", "--out-evidence", multi / "dimer_evidence_by_position.tsv",
        "--out-read-events", multi / "dimer_read_events.tsv", "--out-breakpoint-sequences", multi / "dimer_breakpoint_sequences.tsv",
        "--out-secondary-anomalies", multi / "dimer_secondary_anomalies.tsv", "--out-secondary-summary", multi / "dimer_secondary_summary.tsv")
    producer_specs = [
        (qc / "fastq_qc.log", ('modules/ngs/fastq_plasmid_qc.nf', 'scripts/build_fastq_igv_tracks.py', 'scripts/build_fastq_support_tables.py', 'scripts/build_small_igv_report_inputs.py', 'scripts/validate_standalone_igv_report.py', 'scripts/build_construct_verification_input.py', 'scripts/build_sequence_qc_manifest.py'), ("python3", "samtools", "create_report")),
        (multi / "dimer_producer.log", ("modules/ngs/fastq_dimer_qc.nf", "scripts/build_dimer_canonical_outputs.py"), ("python3",)),
        (multi / "dimer_analysis.log", ('modules/ngs/fastq_dimer_qc.nf', 'scripts/init_fastq_dimer_outputs.sh', 'scripts/dimer_single_ref_split_events.awk', 'scripts/dominant_dimer_consensus.sh', 'scripts/build_alignment_session_manifest.sh'), ("samtools", "minimap2", "awk")),
    ]
    for path, sources, tools in producer_specs:
        path.write_text(producer_receipt(path.parent / ("fixture-" + path.stem), sources, tools))
    observed = qc / "construct_verification_input"
    run("build_construct_verification_input.py", "--reference-fasta", reference,
        "--expected-reference-sha256", job.params["reference_sequence_sha256"], "--source-reads", reads,
        "--consensus-fasta", consensus, "--consensus-method", "samtools_consensus", "--out-dir", observed)
    run("build_construct_topology_evidence.py", "--reference-fasta", reference, "--alignment-bam", bam,
        "--breakpoint-call", multi / "dimer_breakpoint_call.tsv", "--secondary-summary", multi / "dimer_secondary_summary.tsv",
        "--samtools-command", samtools, "--out", verify / "topology_evidence.json")
    run("verify_construct.py", "--reference-fasta", reference,
        "--expected-reference-sha256", job.params["reference_sequence_sha256"],
        "--observed-state", observed / "observed_state.json", "--observed-fasta", observed / "observed_consensus.fasta",
        "--per-base-support", qc / "per_base_support.tsv", "--alignment-bam", bam,
        "--alignment-index", align / "aligned.bam.bai", "--samtools-bin", samtools,
        "--alignment-stats", qc / "fastq_alignment_stats.tsv", "--topology-evidence", verify / "topology_evidence.json",
        "--breakpoint-call", multi / "dimer_breakpoint_call.tsv", "--secondary-summary", multi / "dimer_secondary_summary.tsv",
        "--profile-config", ROOT / "config/ngs/construct_verify_profiles.json", "--profile", "plasmid_strict_v1", "--out-dir", verify)
    manifest_path = verify / "qc_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["execution"]["producer_receipt"] = producer_receipt(verify / "fixture-producer",
        ("modules/ngs/construct_verify.nf", "scripts/build_construct_topology_evidence.py", "scripts/verify_construct.py", "config/ngs/construct_verify_profiles.json"), ("python3", "samtools"))
    manifest_path.write_text(json.dumps(manifest))
    artifact_options = {
        "summary": "fastq_qc_summary.tsv", "read-lengths": "read_lengths.tsv",
        "alignment-stats": "fastq_alignment_stats.tsv", "coverage": "fastq_coverage.tsv",
        "per-base-support": "per_base_support.tsv", "consensus": "fastq_consensus.fasta",
        "consensus-index": "fastq_consensus.fasta.fai", "consensus-log": "fastq_consensus.log",
        "alignment-bam": "aligned.bam", "alignment-bai": "aligned.bam.bai",
        "reference-index": "reference_qc.fasta.fai",
        **{name.replace("_", "-"): name + ".bedgraph" for name in (
            "igv_coverage_depth", "igv_position_gradient", "igv_gc_content", "igv_gc_zscore",
            "igv_split_read_density", "igv_softclip_density")},
        "igv-junction-hotspots": "igv_junction_hotspots.bed",
        "igv-report-sites-bed": "igv_report_sites.bed", "igv-report-sites-tsv": "igv_report_sites.tsv",
        "igv-track-config": "igv_track_config.json", "igv-report": "igv_report.html",
        "igv-report-log": "igv_report.log", "log": "fastq_qc.log",
    }
    run("build_sequence_qc_manifest.py", "--out", qc / "qc_manifest.json", "--job-id", job.id,
        "--workflow-id", "ont_fastq_qc", "--input-mode", "fastq", "--reference-fasta", qc / "reference_qc.fasta",
        "--expected-sha256", job.params["reference_sequence_sha256"],
        "--consensus-status", "samtools_1.24_bayesian_consensus",
        *(value for option, filename in artifact_options.items() for value in ("--" + option, qc / filename)))
    return reads
