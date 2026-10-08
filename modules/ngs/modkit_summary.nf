/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process ModkitSummary {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/methylation", mode: 'copy'
    tag "summary"

    input:
    tuple path(bam), path(bai)

    output:
    path "modkit_summary.tsv", emit: summary
    path "summary.log", emit: log

    script:
    """
    modkit summary \\
        ${bam} \\
        --tsv \\
        > modkit_summary.tsv \\
        2>summary.log
    """
}
