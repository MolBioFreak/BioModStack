/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

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
