def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

process ConstructVerify {
    label 'fastq_qc_cpu'
    publishDir "${params.out_dir}", mode: 'copy'
    tag "construct_verify"

    input:
    path reference
    path verification_input
    path per_base_support
    tuple path(aligned_bam), path(aligned_bai)
    path alignment_stats
    path dimer_breakpoint_call
    path dimer_secondary_summary

    output:
    path "verification", emit: verification_dir
    path "verification/qc_manifest.json", emit: manifest
    path "verification/verification_summary.tsv", emit: summary
    path "verification/variants.vcf", emit: variants
    path "verification/per_base_metrics.tsv", emit: per_base_metrics
    path "verification/evidence.html", emit: evidence_html

    script:
    def codeRoot = params.code_root ?: projectDir
    def topologyScript = shellQuote("${codeRoot}/scripts/build_construct_topology_evidence.py")
    def verifierScript = shellQuote("${codeRoot}/scripts/verify_construct.py")
    def profileConfig = shellQuote("${codeRoot}/config/ngs/construct_verify_profiles.json")
    def referenceDigest = shellQuote(params.reference_sequence_sha256 ?: '')
    def profileId = shellQuote(params.construct_verify_profile ?: 'plasmid_strict_v1')
    def containerDir = params.container_dir ?: ''
    def doradoImage = shellQuote("${containerDir}/dorado.sif")
    """
    set -euo pipefail

    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD=(python3)
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD=(python)
    else
        echo "python not found" >&2
        exit 127
    fi

    if command -v samtools >/dev/null 2>&1; then
        SAMTOOLS_ARGS=(--samtools-command samtools)
    elif command -v apptainer >/dev/null 2>&1 && [[ -f ${doradoImage} ]]; then
        SAMTOOLS_ARGS=(
            --samtools-command apptainer
            --samtools-command exec
            --samtools-command ${doradoImage}
            --samtools-command samtools
        )
    else
        echo "samtools not found on host and no fallback dorado container available" >&2
        exit 127
    fi

    mkdir -p verification

    "\${PYTHON_CMD[@]}" ${topologyScript} \\
        --reference-fasta "${reference}" \\
        --alignment-bam "${aligned_bam}" \\
        --breakpoint-call "${dimer_breakpoint_call}" \\
        --secondary-summary "${dimer_secondary_summary}" \\
        "\${SAMTOOLS_ARGS[@]}" \\
        --out verification/topology_evidence.json

    "\${PYTHON_CMD[@]}" ${verifierScript} \\
        --reference-fasta "${reference}" \\
        --expected-reference-sha256 ${referenceDigest} \\
        --observed-state "${verification_input}/observed_state.json" \\
        --observed-fasta "${verification_input}/observed_consensus.fasta" \\
        --per-base-support "${per_base_support}" \\
        --alignment-stats "${alignment_stats}" \\
        --topology-evidence verification/topology_evidence.json \\
        --breakpoint-call "${dimer_breakpoint_call}" \\
        --secondary-summary "${dimer_secondary_summary}" \\
        --alignment-bam "${aligned_bam}" \\
        --alignment-index "${aligned_bai}" \\
        "\${SAMTOOLS_ARGS[@]}" \\
        --profile-config ${profileConfig} \\
        --profile ${profileId} \\
        --out-dir verification
    """
}
