/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process FastqAlign {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/align", mode: 'copy'
    tag "fastq_align"

    input:
    path fastq
    path reference

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "reference.fasta", emit: reference_copy
    path "reference.fasta.fai", emit: reference_index
    path "fastq_align.log", emit: log

    script:
    def minimapPreset = ((params.fastq_minimap2_preset ?: 'map-ont') as String).trim()
    def allowSecondary = (params.fastq_minimap2_allow_secondary == true) ? 'true' : 'false'
    """
    set -euo pipefail

    # Align FASTQ reads to reference with minimap2 (preset configurable via params).
    MM2_ARGS=(-a -x "${minimapPreset}" -t ${task.cpus})
    if [[ "${allowSecondary}" != "true" ]]; then
        MM2_ARGS+=(--secondary=no)
    fi

    minimap2 "\${MM2_ARGS[@]}" \\
        "${reference}" "${fastq}" 2>fastq_align.log \\
        | samtools sort -@ ${task.cpus} -o aligned.bam

    samtools quickcheck -v aligned.bam 2>>fastq_align.log
    samtools index aligned.bam
    samtools idxstats aligned.bam > /dev/null 2>>fastq_align.log
    if [[ "\$(realpath "${reference}")" != "\$(realpath -m reference.fasta)" ]]; then
        cp "${reference}" reference.fasta
    fi
    samtools faidx reference.fasta
    """
}
