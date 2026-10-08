/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process DoradoAlign {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/align", mode: 'copy'
    tag "align"

    input:
    path bam
    path reference

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "reference.fasta", emit: reference_copy
    path "reference.fasta.fai", emit: reference_index
    path "align.log", emit: log

    script:
    def rawBamMinMapq = (params.bam_min_mapq ?: '0').toString().trim()
    if (!(rawBamMinMapq ==~ /[0-9]+/)) {
        throw new IllegalArgumentException('bam_min_mapq must be an integer between 0 and 255')
    }
    def parsedBamMinMapq = rawBamMinMapq.toBigInteger()
    if (parsedBamMinMapq < 0 || parsedBamMinMapq > 255) {
        throw new IllegalArgumentException('bam_min_mapq must be an integer between 0 and 255')
    }
    def bamMinMapq = parsedBamMinMapq.toString()
    """
    set -euo pipefail

    # Sort and align; preserve MM/ML methylation tags
    if [[ ${bamMinMapq} -gt 0 ]]; then
        dorado aligner \\
            "${reference}" \\
            "${bam}" \\
            --threads ${task.cpus} \\
            2>align.log \\
            | samtools view -h -q ${bamMinMapq} - \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
        echo "Applied MAPQ filter: >= ${bamMinMapq}" >> align.log
    else
        dorado aligner \\
            "${reference}" \\
            "${bam}" \\
            --threads ${task.cpus} \\
            2>align.log \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
    fi

    samtools index aligned.bam
    cp "${reference}" reference.fasta
    samtools faidx reference.fasta

    input_records=\$(samtools view -c "${bam}")
    output_records=\$(samtools view -c aligned.bam)
    {
        echo "bam_min_mapq=${bamMinMapq}"
        echo "input_records=\${input_records}"
        echo "output_records=\${output_records}"
    } >> align.log
    """
}
