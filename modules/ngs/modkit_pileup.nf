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

    samtools quickcheck -v "${bam}"
    samtools idxstats "${bam}" >/dev/null
    total_records=\$(samtools view -c "${bam}")
    mapped_records=\$(samtools view -c -F 4 "${bam}")
    tagged_records=\$(samtools view "${bam}" | awk -F '\t' '
        {
            has_mm = 0
            has_ml = 0
            for (i = 12; i <= NF; i++) {
                if (\$i ~ /^MM:Z:[ACGTUN][+-][^,;]+,[0-9]+/) has_mm = 1
                if (\$i ~ /^ML:B:[cCsSiI],[0-9]+(,[0-9]+)*\$/) has_ml = 1
            }
            if (has_mm && has_ml) c++
        }
        END { print c + 0 }
    ')
    {
        echo "total_records=\${total_records}"
        echo "mapped_records=\${mapped_records}"
        echo "modified_base_tagged_records=\${tagged_records}"
    } > modified_base_tag_check.log

    if [[ "\${mapped_records}" -eq 0 ]]; then
        echo "ERROR: BAM contains no mapped reads for modkit; reference alignment is required." >&2
        exit 1
    fi

    if [[ "\${tagged_records}" -eq 0 ]]; then
        echo "ERROR: BAM contains no meaningful paired MM/ML modified-base tags for modkit; operator review required." >&2
        echo "Basecall POD5 with --modified-bases or provide a BAM containing non-empty MM/ML tags." >&2
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
    modkit pileup \\
        "${bam}" \\
        methylation.bed \\
        --ref "${reference}" \\
        ${filterThreshold} \\
        --threads ${task.cpus} \\
        2>&1 | tee pileup.log
    """
}
