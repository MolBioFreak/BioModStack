/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process ValidateModifiedBaseBam {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/methylation", mode: 'copy'
    tag "modified_base_tag_check"

    input:
    tuple path(bam, stageAs: 'modified-base-source.bam'), path(bai, stageAs: 'modified-base-source.bam.bai')

    output:
    tuple path("modified_base_input.bam"), path("modified_base_input.bam.bai"), emit: bam
    path "modified_base_tag_check.log", emit: log

    script:
    """
    set -euo pipefail
    source "${params.code_root ?: projectDir}/scripts/ngs_producer_identity.sh"
    bms_producer_begin "${params.code_root ?: projectDir}" modules/ngs/modkit_pileup.nf scripts/validate_modified_base_bam.py -- samtools python=/opt/igv-reports/bin/python || exit 1

    samtools quickcheck -v "${bam}"
    samtools idxstats "${bam}" >/dev/null
    /opt/igv-reports/bin/python "${params.code_root ?: projectDir}/scripts/validate_modified_base_bam.py" "${bam}" > modified_base_tag_check.log

    cp "${bam}" modified_base_input.bam
    cp "${bai}" modified_base_input.bam.bai
    bms_producer_finish >> modified_base_tag_check.log || exit 1
    """
}
process ModkitPileup {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/methylation", mode: 'copy'
    tag "pileup"

    input:
    tuple path(bam, stageAs: 'modkit-input.bam'), path(bai, stageAs: 'modkit-input.bam.bai')
    path reference, stageAs: 'modkit-reference.fasta'

    output:
    path "methylation.bed", emit: bed
    path "pileup.log", emit: log

    script:
    def filterThreshold = ''
    if (params.modkit_filter_threshold != null && params.modkit_filter_threshold.toString().trim() != '') {
        BigDecimal threshold
        try {
            threshold = new BigDecimal(params.modkit_filter_threshold.toString().trim())
        } catch (NumberFormatException exc) {
            throw new IllegalArgumentException('modkit_filter_threshold must be numeric')
        }
        if (threshold < 0 || threshold > 1) {
            throw new IllegalArgumentException('modkit_filter_threshold must be between 0 and 1')
        }
        filterThreshold = "--filter-threshold ${threshold.toPlainString()}"
    }
    """
    set -euo pipefail
    source "${params.code_root ?: projectDir}/scripts/ngs_producer_identity.sh"
    bms_producer_begin "${params.code_root ?: projectDir}" modules/ngs/modkit_pileup.nf -- modkit || exit 1
    test ! -e methylation.bed
    bms_version="\$(modkit --version)"
    bms_input="\$(sha256sum "${bam}" | cut -d ' ' -f1)"
    bms_index="\$(sha256sum "${bai}" | cut -d ' ' -f1)"
    bms_reference="\$(sha256sum "${reference}" | cut -d ' ' -f1)"
    modkit pileup \\
        "${bam}" \\
        methylation.bed \\
        --ref "${reference}" \\
        ${filterThreshold} \\
        --threads ${task.cpus} \\
        2>&1 | tee pileup.log
    test "\${bms_input}" = "\$(sha256sum "${bam}" | cut -d ' ' -f1)"
    test "\${bms_index}" = "\$(sha256sum "${bai}" | cut -d ' ' -f1)"
    test "\${bms_reference}" = "\$(sha256sum "${reference}" | cut -d ' ' -f1)"
    {
        echo "bms_modkit_version=\${bms_version}"
        echo "bms_input_sha256=\${bms_input}"
        echo "bms_index_sha256=\${bms_index}"
        echo "bms_reference_sha256=\${bms_reference}"
        echo "bms_filter_args=${filterThreshold}"
        echo "bms_output_sha256=\$(sha256sum methylation.bed | cut -d ' ' -f1)"
    } >> pileup.log
    bms_producer_finish >> pileup.log || exit 1
    """
}
