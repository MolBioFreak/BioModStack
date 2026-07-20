/** Immutable P3 wrapper and adapter for the pinned wf-clone-validation runtime. */

def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

process RunCloneValidation {
    label 'wf_clone'
    publishDir "${params.out_dir}/assembly", mode: 'copy'
    tag "clone_validation"

    input:
    tuple path(bam), val(reference_fasta)

    output:
    path "wf_clone_out", emit: out
    path "wf_clone.log", emit: log
    path "runtime_provenance.json", emit: runtime_provenance
    path "wf_clone_out/wf-clone-validation-report.html", emit: report
    path "wf_clone_out/sample_status.txt", emit: sample_status

    script:
    def sampleName = (params.wf_clone_sample ?: params.name ?: (params.job_id ? "nanopore_${params.job_id}" : "nanopore"))
        .toString()
        .replaceAll(/[^A-Za-z0-9._-]/, "_")
    def approxSize = (params.wf_clone_approx_size ?: 7000) as Integer
    def assmCoverage = (params.wf_clone_assm_coverage ?: 60) as Integer
    def minQuality = (params.wf_clone_min_quality != null ? params.wf_clone_min_quality : (params.min_qscore ?: 9)) as Integer
    def trimLength = (params.wf_clone_trim_length ?: 0) as Integer
    def assemblyTool = (params.wf_clone_assembly_tool ?: 'flye').toString().trim()
    def basecallerModel = (params.wf_clone_basecaller_model ?: 'dna_r10.4.1_e8.2_400bps_hac@v5.0.0').toString().trim()
    def allowedAssemblyTools = ['flye', 'canu'] as Set
    def allowedModels = ['dna_r10.4.1_e8.2_400bps_hac@v5.0.0'] as Set
    if (!allowedAssemblyTools.contains(assemblyTool)) {
        error("Unsupported --wf_clone_assembly_tool '${assemblyTool}'. Supported exact values: ${allowedAssemblyTools.join(', ')}")
    }
    if (!allowedModels.contains(basecallerModel)) {
        error("Unsupported --wf_clone_basecaller_model '${basecallerModel}'. Supported exact identities: ${allowedModels.join(', ')}")
    }
    def largeConstruct = params.wf_clone_large_construct ? '--large_construct' : ''
    def referencePath = reference_fasta ? reference_fasta.toString().trim() : ''
    if (!referencePath) {
        error("wf_clone_validation requires an authoritative full reference for P3 construct verification")
    }
    def codeRoot = params.code_root ?: projectDir
    def validator = shellQuote("${codeRoot}/scripts/validate_wf_clone_runtime.py")
    def lock = shellQuote("${codeRoot}/config/ngs/wf_clone_validation_v1.8.4.lock.json")
    def wfCloneSingularityCache = '/mnt/BioModStack/apptainer/singularity_cache'
    def wfCloneNxfHome = '/mnt/BioModStack/nextflow/wf-clone'
    """
    set -euo pipefail
    export NXF_OFFLINE=true
    export NXF_DISABLE_CHECK_LATEST=true
    export NXF_DOCKER_ENABLED=false
    export NXF_SINGULARITY_ENABLED=true
    export NXF_SINGULARITY_CACHEDIR="${wfCloneSingularityCache}"
    export NXF_HOME="${wfCloneNxfHome}"
    mkdir -p "\${NXF_HOME}"

    python3 ${validator} \
        --lock ${lock} \
        --model "${basecallerModel}" \
        --output runtime_provenance.json

    mkdir -p wf_clone_out
    set +e
    /usr/local/bin/nextflow -log wf_clone.log run /mnt/BioModStack/ngs/wf-clone-validation/v1.8.4-bms.1 \
        -offline \
        -profile singularity \
        -w wf_clone_work \
        --bam "${bam}" \
        --sample "${sampleName}" \
        --out_dir wf_clone_out \
        --approx_size ${approxSize} \
        --assm_coverage ${assmCoverage} \
        --min_quality ${minQuality} \
        --trim_length ${trimLength} \
        --assembly_tool "${assemblyTool}" \
        --override_basecaller_cfg "${basecallerModel}" \
        --full_reference "${referencePath}" \
        ${largeConstruct}
    wf_clone_rc=\$?
    set -e
    if [[ \${wf_clone_rc} -ne 0 ]]; then
        printf 'wf-clone-validation failed (assembly_tool=%s, exit=%s)\n' "${assemblyTool}" "\${wf_clone_rc}" >&2
        exit "\${wf_clone_rc}"
    fi
    """
}

process CloneValidationAdapter {
    label 'local_cpu'
    publishDir "${params.out_dir}/assembly/adapter", mode: 'copy'
    tag "clone_validation_adapter"

    input:
    path result_root
    path runtime_provenance
    tuple path(aligned_bam), path(aligned_bai)
    path reference, stageAs: 'authoritative_reference.fasta'

    output:
    path "adapter_manifest.json", emit: manifest
    path "verification_input", emit: verification_input
    path "per_base_support.tsv", emit: per_base_support
    path "alignment_stats.tsv", emit: alignment_stats
    path "dimer_breakpoint_call.tsv", emit: breakpoint_call
    path "dimer_secondary_summary.tsv", emit: secondary_summary

    script:
    def sampleName = (params.wf_clone_sample ?: params.name ?: (params.job_id ? "nanopore_${params.job_id}" : "nanopore"))
        .toString()
        .replaceAll(/[^A-Za-z0-9._-]/, "_")
    def codeRoot = params.code_root ?: projectDir
    def adapter = shellQuote("${codeRoot}/scripts/adapt_wf_clone_validation.py")
    def inputBuilder = shellQuote("${codeRoot}/scripts/build_construct_verification_input.py")
    def supportBuilder = shellQuote("${codeRoot}/scripts/build_fastq_support_tables.py")
    def containerDir = params.container_dir ?: ''
    def doradoImage = shellQuote("${containerDir}/dorado.sif")
    """
    set -euo pipefail
    if command -v samtools >/dev/null 2>&1; then
        SAMTOOLS_CMD=(samtools)
    elif command -v apptainer >/dev/null 2>&1 && [[ -f ${doradoImage} ]]; then
        SAMTOOLS_CMD=(apptainer exec ${doradoImage} samtools)
    else
        echo "samtools not found on host and no fallback dorado container available" >&2
        exit 127
    fi

    "\${SAMTOOLS_CMD[@]}" fastq -F 2304 -n "${aligned_bam}" > source_reads.fastq
    test -s source_reads.fastq

    python3 ${adapter} \
        --result-root "\$(realpath ${result_root})" \
        --runtime-provenance "\$(realpath ${runtime_provenance})" \
        --sample "${sampleName}" \
        --execution-exit-code 0 \
        --full-reference-provided \
        --source-bam "\$(realpath ${aligned_bam})" \
        --source-bai "\$(realpath ${aligned_bai})" \
        --output adapter_manifest.json

    python3 ${inputBuilder} \
        --reference-fasta authoritative_reference.fasta \
        --expected-reference-sha256 "${params.reference_sequence_sha256 ?: ''}" \
        --source-reads source_reads.fastq \
        --consensus-fasta "${result_root}/${sampleName}.final.fasta" \
        --consensus-method wf_clone_validation_final_assembly \
        --out-dir verification_input

    python3 ${supportBuilder} \
        --bam "${aligned_bam}" \
        --reference-fasta authoritative_reference.fasta \
        --out-per-base-support per_base_support.tsv \
        --samtools-cmd "\${SAMTOOLS_CMD[@]}"

    total_reads=\$("\${SAMTOOLS_CMD[@]}" view -c -F 2304 "${aligned_bam}")
    mapped_reads=\$("\${SAMTOOLS_CMD[@]}" view -c -F 2308 "${aligned_bam}")
    unmapped_reads=\$("\${SAMTOOLS_CMD[@]}" view -c -f 4 -F 2304 "${aligned_bam}")
    {
        printf 'metric\tvalue\n'
        printf 'total_reads\t%s\n' "\${total_reads}"
        printf 'mapped_reads\t%s\n' "\${mapped_reads}"
        printf 'unmapped_reads\t%s\n' "\${unmapped_reads}"
    } > alignment_stats.tsv
    printf 'breakpoint_status\tconfidence\tprimary_breakpoint_in_boundary_window\n' > dimer_breakpoint_call.tsv
    printf 'aligned_dimer_reads\tnon_boundary_split_reads\n' > dimer_secondary_summary.tsv
    """
}
