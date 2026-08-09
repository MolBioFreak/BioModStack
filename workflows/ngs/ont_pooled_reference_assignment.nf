#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// FASTQ + one server-staged pooled reference-set snapshot → competitive read
// dispositions and review artifacts.  This entrypoint deliberately ends at
// operator review and has no downstream sequence-inference stages.

process ONTPooledReferenceAssignment {
    label 'pooled_assignment_cpu'
    publishDir "${params.out_dir}/pooled_reference_assignment", mode: 'copy'
    tag "pooled_reference_assignment"

    input:
    path fastq
    path snapshot_root
    val manifest_name

    output:
    path "assignment_summary.json"
    path "per_read_assignment.tsv"
    path "fastq_preflight.json"
    path "occurrence_map.json"
    path "combined_intended_reference.fasta"
    path "combined_intended_reference.fasta.fai"
    path "pooled_assignment.bam"
    path "pooled_assignment.bam.bai"
    path "pooled_assignment.minimap2.log"
    path "target_*.read_ids.txt"
    path "target_*.fastq"
    path "ambiguous.read_ids.txt"
    path "ambiguous.fastq"
    path "unclassified.read_ids.txt"
    path "unclassified.fastq"
    path "intended_pool.igv_session.json"

    script:
    def codeRoot = params.code_root ?: projectDir
    def assignmentScript = "${codeRoot}/scripts/pooled_ont_reference_assignment.py"
    def minMapq = params.pooled_assignment_min_mapq
    def scoreMargin = params.pooled_assignment_min_alignment_score_margin
    if (minMapq == null || scoreMargin == null) {
        error("pooled assignment profile must provide pooled_assignment_min_mapq and pooled_assignment_min_alignment_score_margin")
    }
    """
    set -euo pipefail

    command -v python3 >/dev/null 2>&1 || { echo "python3 is required; no fallback is available" >&2; exit 127; }
    command -v minimap2 >/dev/null 2>&1 || { echo "minimap2 is required; no fallback is available" >&2; exit 127; }
    command -v samtools >/dev/null 2>&1 || { echo "samtools is required; no fallback is available" >&2; exit 127; }
    test -d "${snapshot_root}"
    test -f "${snapshot_root}/${manifest_name}"

    python3 "${assignmentScript}" preflight \\
        --manifest "${snapshot_root}/${manifest_name}" \\
        --snapshot-root "${snapshot_root}" \\
        --fastq "${fastq}" \\
        --out-dir .

    samtools faidx combined_intended_reference.fasta

    minimap2 -a -x map-ont --secondary=yes -t ${task.cpus} \\
        combined_intended_reference.fasta valid_reads.fastq \\
        2>pooled_reference_assignment.minimap2.log \\
        | samtools sort -@ ${task.cpus} -o pooled_assignment.bam -

    samtools quickcheck -v pooled_assignment.bam
    samtools index -@ ${task.cpus} pooled_assignment.bam
    test -s pooled_assignment.bam.bai

    python3 "${assignmentScript}" classify \\
        --manifest "${snapshot_root}/${manifest_name}" \\
        --snapshot-root "${snapshot_root}" \\
        --source-fastq "${fastq}" \\
        --fastq valid_reads.fastq \\
        --preflight fastq_preflight.json \\
        --bam pooled_assignment.bam \\
        --samtools samtools \\
        --combined-fasta combined_intended_reference.fasta \\
        --out-dir . \\
        --min-mapq ${minMapq} \\
        --min-alignment-score-margin ${scoreMargin}
    """
}

workflow ONT_POOLED_REFERENCE_ASSIGNMENT {
    main:
    if (!params.fastq_path || !params.fastq_path.toString().trim()) {
        error("pooled reference assignment requires --fastq_path")
    }
    if (!params.reference_set_manifest || !params.reference_set_manifest.toString().trim()) {
        error("pooled reference assignment requires --reference_set_manifest")
    }

    def fastq_input = file(params.fastq_path)
    if (!fastq_input.exists() || !fastq_input.isFile()) {
        error("FASTQ input is not an existing regular file: ${params.fastq_path}")
    }
    def manifest_input = file(params.reference_set_manifest)
    if (!manifest_input.exists() || !manifest_input.isFile()) {
        error("reference-set manifest is not an existing regular file: ${params.reference_set_manifest}")
    }
    def snapshot_root = file(manifest_input.parent)

    ONTPooledReferenceAssignment(
        Channel.of(fastq_input),
        Channel.of(snapshot_root),
        manifest_input.name,
    )
}

workflow {
    ONT_POOLED_REFERENCE_ASSIGNMENT()
}
