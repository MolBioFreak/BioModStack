/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process PrepareBamForAnalysis {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/align", mode: 'copy'
    tag "bam_prepare"

    input:
    path bam, stageAs: 'source.bam'

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "bam_prepare.log", emit: log

    script:
    def bamMinMapq = Math.max((params.bam_min_mapq ?: 0) as Integer, 0)
    def declaredSourceSha256 = params.bam_source_sha256?.toString()?.trim()?.toLowerCase() ?: ''
    if (declaredSourceSha256 && !(declaredSourceSha256 ==~ /[0-9a-f]{64}/)) {
        throw new IllegalArgumentException('bam_source_sha256 must be exactly 64 hexadecimal characters')
    }
    """
    set -euo pipefail

    # Authenticate one task-local regular-file copy and consume only that copy.
    # The staged input may be a symlink to caller-writable storage.
    cp --reflink=auto -- "${bam}" source.snapshot.bam
    chmod 0444 source.snapshot.bam
    source_sha256_before="\$(sha256sum source.snapshot.bam | awk '{print \$1}')"
    if [[ -n "${declaredSourceSha256}" && "\${source_sha256_before}" != "${declaredSourceSha256}" ]]; then
        echo "ERROR: task-local source BAM snapshot does not match authorized bam_source_sha256." >&2
        exit 98
    fi
    samtools quickcheck -v source.snapshot.bam 2> bam_prepare.log
    input_sort_order=\$(samtools view -H source.snapshot.bam | awk -F '\t' '
        /^@HD/ { for (i=1; i<=NF; i++) if (\$i ~ /^SO:/) { sub(/^SO:/, "", \$i); print \$i; exit } }
    ')
    echo "input_sort_order=\${input_sort_order:-unknown}" >> bam_prepare.log

    # Preserve MM/ML tags while enforcing coordinate-sorted BAM + index for modkit.
    if [[ ${bamMinMapq} -gt 0 ]]; then
        samtools view -h -q ${bamMinMapq} source.snapshot.bam 2>> bam_prepare.log \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
        echo "Applied MAPQ filter: >= ${bamMinMapq}" >> bam_prepare.log
    else
        samtools sort -@ ${task.cpus} -o aligned.bam source.snapshot.bam 2>> bam_prepare.log
    fi
    samtools index aligned.bam 2>> bam_prepare.log
    samtools quickcheck -v aligned.bam 2>> bam_prepare.log
    samtools idxstats aligned.bam > /dev/null 2>> bam_prepare.log
    input_records=\$(samtools view -c source.snapshot.bam)
    output_records=\$(samtools view -c aligned.bam)
    mapped_records=\$(samtools view -c -F 4 aligned.bam)
    if [[ "\${mapped_records}" -eq 0 ]]; then
        echo "ERROR: prepared BAM contains no mapped reads." >&2
        exit 1
    fi
    source_sha256_after="\$(sha256sum source.snapshot.bam | awk '{print \$1}')"
    if [[ "\${source_sha256_before}" != "\${source_sha256_after}" ]]; then
        echo "ERROR: task-local source BAM snapshot changed during preparation." >&2
        exit 97
    fi
    {
        echo "source_sha256_before=\${source_sha256_before}"
        echo "source_sha256_after=\${source_sha256_after}"
        echo "source_immutable=true"
        echo "bam_min_mapq=${bamMinMapq}"
        echo "input_records=\${input_records}"
        echo "output_records=\${output_records}"
        echo "mapped_records=\${mapped_records}"
    } >> bam_prepare.log
    """
}
process ValidateMappedBam {
    label 'dorado_cpu'
    tag "bam_mapped_check"

    input:
    tuple path(bam, stageAs: 'validated-source.bam'), path(bai, stageAs: 'validated-source.bam.bai')
    path reference, stageAs: 'expected-reference.fasta'

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "bam_mapped_check.log", emit: log

    script:
    def declaredReferenceSha256 = params.bam_reference_sha256?.toString()?.trim()?.toLowerCase() ?: ''
    def declaredSourceSha256 = params.bam_source_sha256?.toString()?.trim()?.toLowerCase() ?: ''
    if (declaredReferenceSha256 && !(declaredReferenceSha256 ==~ /[0-9a-f]{64}/)) {
        error("bam_reference_sha256 must be exactly 64 hexadecimal characters")
    }
    if (declaredSourceSha256 && !(declaredSourceSha256 ==~ /[0-9a-f]{64}/)) {
        error("bam_source_sha256 must be exactly 64 hexadecimal characters")
    }
    """
    set -euo pipefail

    total_reads=\$(samtools view -c "${bam}")
    mapped_reads=\$(samtools view -c -F 4 "${bam}")
    samtools quickcheck -v "${bam}" 2> bam_mapped_check.log
    samtools idxstats "${bam}" > /dev/null 2>> bam_mapped_check.log
    samtools faidx "${reference}"

    if [[ "\${mapped_reads}" -eq 0 ]]; then
        echo "ERROR: BAM has zero mapped reads." >&2
        echo "Provide --reference_fasta so the workflow can align BAM input before modkit/wf-clone-validation." >&2
        exit 1
    fi

    samtools idxstats "${bam}" | awk -F '\t' '\$1 != "*" && \$3 > 0 { print \$1 "\t" \$2 }' | sort -u > bam.mapped_contigs
    cut -f1,2 "${reference}.fai" | sort -u > reference.contigs
    incompatible_contigs=\$(comm -23 bam.mapped_contigs reference.contigs || true)
    if [[ -n "\${incompatible_contigs}" ]]; then
        echo "ERROR: BAM and reference contigs do not overlap; mapped contig names/lengths are incompatible." >&2
        echo "\${incompatible_contigs}" >&2
        exit 1
    fi

    samtools view -H "${bam}" | awk -F '\t' '
        /^@SQ/ {
            sn = ""; ln = ""; m5 = ""
            for (i = 2; i <= NF; i++) {
                if (\$i ~ /^SN:/) { sn = substr(\$i, 4) }
                else if (\$i ~ /^LN:/) { ln = substr(\$i, 4) }
                else if (\$i ~ /^M5:/) { m5 = tolower(substr(\$i, 4)) }
            }
            if (sn != "") { print sn "\t" ln "\t" m5 }
        }
    ' > bam.header_contigs

    missing_m5=0
    while IFS=\$'\t' read -r contig contig_length; do
        bam_m5=\$(awk -F '\t' -v target="\${contig}" '\$1 == target { print \$3; exit }' bam.header_contigs)
        if [[ -z "\${bam_m5}" ]]; then
            missing_m5=1
            continue
        fi
        reference_m5=\$(samtools faidx "${reference}" "\${contig}" \\
            | awk 'NR > 1 { gsub(/[[:space:]]/, ""); printf "%s", toupper(\$0) }' \\
            | md5sum | cut -d ' ' -f1)
        if [[ "\${bam_m5,,}" != "\${reference_m5}" ]]; then
            echo "ERROR: BAM @SQ M5 does not match expected reference for mapped contig \${contig}." >&2
            echo "bam_m5=\${bam_m5,,} expected_reference_m5=\${reference_m5}" >&2
            exit 1
        fi
    done < bam.mapped_contigs

    if [[ "\${missing_m5}" -eq 1 ]]; then
        if [[ -z "${declaredReferenceSha256}" || -z "${declaredSourceSha256}" ]]; then
            echo "ERROR: mapped BAM contigs lack @SQ M5; trusted bam_reference_sha256 and bam_source_sha256 provenance are both required." >&2
            exit 1
        fi
        actual_source_sha256=\$(sha256sum "${bam}" | cut -d ' ' -f1)
        if [[ "${declaredSourceSha256}" != "\${actual_source_sha256}" ]]; then
            echo "ERROR: bam_source_sha256 does not match the exact BAM object being validated." >&2
            exit 1
        fi
        expected_reference_sha256=\$(awk '!/^>/ { gsub(/[[:space:]]/, ""); printf "%s", toupper(\$0) }' "${reference}" \\
            | sha256sum | cut -d ' ' -f1)
        if [[ "${declaredReferenceSha256}" != "\${expected_reference_sha256}" ]]; then
            echo "ERROR: bam_reference_sha256 does not match normalized expected reference sequence." >&2
            exit 1
        fi
        {
            echo "reference_identity=trusted_source_bam_and_reference_sha256"
            echo "validated_bam_sha256=\${actual_source_sha256}"
            echo "validated_reference_sha256=\${expected_reference_sha256}"
        } >> bam_mapped_check.log
    else
        echo "reference_identity=bam_sq_m5" >> bam_mapped_check.log
    fi
    {
        echo "total_reads=\${total_reads}"
        echo "mapped_reads=\${mapped_reads}"
    } >> bam_mapped_check.log

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
    path reference, stageAs: 'expected-reference-source.fasta'

    output:
    path "reference.fasta", emit: reference_copy
    path "reference.fasta.fai", emit: reference_index
    path "reference_prepare.log", emit: log

    script:
    """
    set -euo pipefail
    cp "${reference}" reference.fasta
    samtools faidx reference.fasta > /dev/null 2>&1
    echo "Prepared reference.fasta and reference.fasta.fai for IGV" > reference_prepare.log
    """
}
