/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process PrepareBamForAnalysis {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/align", mode: 'copy'
    tag "bam_prepare"

    input:
    path bam

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "bam_prepare.log", emit: log

    script:
    def bamMinMapq = Math.max((params.bam_min_mapq ?: 0) as Integer, 0)
    """
    set -euo pipefail

    # Preserve MM/ML tags while enforcing coordinate-sorted BAM + index for modkit.
    if [[ ${bamMinMapq} -gt 0 ]]; then
        samtools view -h -q ${bamMinMapq} ${bam} 2> bam_prepare.log \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
        echo "Applied MAPQ filter: >= ${bamMinMapq}" >> bam_prepare.log
    else
        samtools sort -@ ${task.cpus} -o aligned.bam ${bam} 2> bam_prepare.log
    fi
    samtools index aligned.bam 2>> bam_prepare.log
    input_records=\$(samtools view -c ${bam})
    output_records=\$(samtools view -c aligned.bam)
    {
        echo "bam_min_mapq=${bamMinMapq}"
        echo "input_records=\${input_records}"
        echo "output_records=\${output_records}"
    } >> bam_prepare.log
    """
}
process ValidateMappedBam {
    label 'dorado_cpu'
    tag "bam_mapped_check"

    input:
    tuple path(bam), path(bai)

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "bam_mapped_check.log", emit: log

    script:
    """
    set -euo pipefail

    total_reads=\$(samtools view -c "${bam}")
    mapped_reads=\$(samtools view -c -F 4 "${bam}")
    {
        echo "total_reads=\${total_reads}"
        echo "mapped_reads=\${mapped_reads}"
    } > bam_mapped_check.log

    if [[ "\${mapped_reads}" -eq 0 ]]; then
        echo "ERROR: BAM has zero mapped reads." >&2
        echo "Provide --reference_fasta so the workflow can align BAM input before modkit/wf-clone-validation." >&2
        exit 1
    fi

    cp "${bam}" aligned.bam
    cp "${bai}" aligned.bam.bai
    """
}
process BamToFastqForQC {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/fastq_qc", mode: 'copy'
    tag "bam_to_fastq_for_qc"

    input:
    tuple path(bam), path(bai)

    output:
    path "reads_for_qc.fastq", emit: fastq
    path "bam_to_fastq_for_qc.log", emit: log

    script:
    """
    set -euo pipefail

    samtools fastq -@ ${task.cpus} "${bam}" > reads_for_qc.fastq 2> bam_to_fastq_for_qc.log
    read_count=\$(awk 'NR % 4 == 1 {c++} END {print c + 0}' reads_for_qc.fastq)
    echo "reads_written=\${read_count}" >> bam_to_fastq_for_qc.log
    if [[ "\${read_count}" -eq 0 ]]; then
        echo "ERROR: BAM-to-FASTQ conversion produced zero reads." >&2
        exit 1
    fi
    """
}
process PrepareReferenceForIGV {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/align", mode: 'copy'
    tag "reference_prepare"

    input:
    path reference

    output:
    path "reference.fasta", emit: reference_copy
    path "reference.fasta.fai", emit: reference_index
    path "reference_prepare.log", emit: log

    script:
    """
    set -euo pipefail
    cp ${reference} reference.fasta
    samtools faidx reference.fasta > /dev/null 2>&1
    echo "Prepared reference.fasta and reference.fasta.fai for IGV" > reference_prepare.log
    """
}
