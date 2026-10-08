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
    set -euo pipefail
    source "${params.code_root ?: projectDir}/scripts/ngs_producer_identity.sh"
    bms_producer_begin "${params.code_root ?: projectDir}" modules/ngs/modkit_summary.nf -- modkit || exit 1
    test ! -e modkit_summary.tsv
    bms_version="\$(modkit --version)"
    bms_input="\$(sha256sum "${bam}" | cut -d ' ' -f1)"
    bms_index="\$(sha256sum "${bai}" | cut -d ' ' -f1)"
    modkit summary \\
        ${bam} \\
        --tsv \\
        > modkit_summary.tsv \\
        2>summary.log
    test "\${bms_input}" = "\$(sha256sum "${bam}" | cut -d ' ' -f1)"
    test "\${bms_index}" = "\$(sha256sum "${bai}" | cut -d ' ' -f1)"
    {
        echo "bms_modkit_version=\${bms_version}"
        echo "bms_input_sha256=\${bms_input}"
        echo "bms_index_sha256=\${bms_index}"
        echo "bms_output_sha256=\$(sha256sum modkit_summary.tsv | cut -d ' ' -f1)"
    } >> summary.log
    bms_producer_finish >> summary.log || exit 1
    """
}
