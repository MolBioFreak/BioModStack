/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process ValidateModifiedBaseBam {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/methylation", mode: 'copy'
    tag "modified_base_tag_check"

    input:
    tuple path(bam), path(bai)

    output:
    tuple path("modified_base_input.bam"), path("modified_base_input.bam.bai"), emit: bam
    path "modified_base_tag_check.log", emit: log

    script:
    """
    set -euo pipefail

    total_records=\$(samtools view -c "${bam}")
    tagged_records=\$(samtools view "${bam}" | awk '
        /\t[Mm][Mm]:Z:/ && /\t[Mm][Ll]:B:C/ { c++ }
        END { print c + 0 }
    ')
    {
        echo "total_records=\${total_records}"
        echo "modified_base_tagged_records=\${tagged_records}"
    } > modified_base_tag_check.log

    if [[ "\${tagged_records}" -eq 0 ]]; then
        echo "ERROR: BAM contains no MM/ML modified-base tags for modkit." >&2
        echo "Basecall POD5 with --modified-bases or provide a BAM containing MM/ML tags." >&2
        exit 1
    fi

    cp "${bam}" modified_base_input.bam
    cp "${bai}" modified_base_input.bam.bai
    """
}
process ModkitPileup {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/methylation", mode: 'copy'
    tag "pileup"

    input:
    tuple path(bam), path(bai)
    path reference

    output:
    path "methylation.bed", emit: bed
    path "pileup.log", emit: log

    script:
    def filterThreshold = (params.modkit_filter_threshold != null && params.modkit_filter_threshold.toString().trim() != '')
        ? "--filter-threshold ${params.modkit_filter_threshold}"
        : ''
    """
    modkit pileup \\
        ${bam} \\
        methylation.bed \\
        --ref ${reference} \\
        ${filterThreshold} \\
        --threads ${task.cpus} \\
        2>&1 | tee pileup.log
    """
}
