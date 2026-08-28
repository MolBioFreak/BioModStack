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
    def declaredSourceSha256 = params.bam_source_sha256?.toString()?.trim()?.toLowerCase() ?: ''
    def declaredReferenceSha256 = params.reference_sequence_sha256?.toString()?.trim()?.toLowerCase() ?: ''
    if (declaredSourceSha256 && !(declaredSourceSha256 ==~ /[0-9a-f]{64}/)) {
        throw new IllegalArgumentException('bam_source_sha256 must be exactly 64 hexadecimal characters')
    }
    if (declaredReferenceSha256 && !(declaredReferenceSha256 ==~ /[0-9a-f]{64}/)) {
        throw new IllegalArgumentException('reference_sequence_sha256 must be exactly 64 hexadecimal characters')
    }
    """
    set -euo pipefail

    # Nextflow may stage the input as a symlink to a caller-writable result.
    # Authenticate one task-local copy and consume only that copy so mutation
    # and restoration of the source cannot race the aligner.
    cp --reflink=auto -- "${bam}" source.snapshot.bam
    chmod 0444 source.snapshot.bam
    source_sha256_before="\$(sha256sum source.snapshot.bam | awk '{print \$1}')"
    if [[ -n "${declaredSourceSha256}" && "\${source_sha256_before}" != "${declaredSourceSha256}" ]]; then
        echo "ERROR: task-local source BAM snapshot does not match authorized bam_source_sha256." >&2
        exit 98
    fi

    # Snapshot, authenticate, align against, and publish the same reference bytes.
    cp --reflink=auto -- "${reference}" reference.snapshot.fasta
    chmod 0444 reference.snapshot.fasta
    reference_raw_sha256_before="\$(sha256sum reference.snapshot.fasta | awk '{print \$1}')"
    normalized_reference="\$(awk '
      BEGIN { records=0; sequence="" }
      {
        line=\$0
        sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+\$/, "", line)
        if (line == "") next
        if (substr(line,1,1) == ">") { records++; if (records > 1) exit 91; next }
        if (records != 1) exit 92
        sequence=sequence toupper(line)
      }
      END { if (records != 1 || sequence == "") exit 93; print sequence }
    ' reference.snapshot.fasta)" || { echo 'ERROR: reference FASTA must contain exactly one non-empty record.' >&2; exit 96; }
    [[ "\${normalized_reference}" =~ ^[ACGTN]+\$ ]] || { echo 'ERROR: reference FASTA contains unsupported symbols.' >&2; exit 96; }
    reference_sequence_sha256="\$(printf '%s' "\${normalized_reference}" | sha256sum | awk '{print \$1}')"
    if [[ -n "${declaredReferenceSha256}" && "\${reference_sequence_sha256}" != "${declaredReferenceSha256}" ]]; then
        echo "ERROR: task-local reference snapshot does not match authorized reference_sequence_sha256." >&2
        exit 95
    fi

    # Sort and align; preserve MM/ML methylation tags.
    if [[ ${bamMinMapq} -gt 0 ]]; then
        dorado aligner \\
            reference.snapshot.fasta \\
            source.snapshot.bam \\
            --threads ${task.cpus} \\
            2>align.log \\
            | samtools view -h -q ${bamMinMapq} - \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
        echo "Applied MAPQ filter: >= ${bamMinMapq}" >> align.log
    else
        dorado aligner \\
            reference.snapshot.fasta \\
            source.snapshot.bam \\
            --threads ${task.cpus} \\
            2>align.log \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
    fi

    samtools index aligned.bam
    rm -f -- reference.fasta
    cp reference.snapshot.fasta reference.fasta
    samtools faidx reference.fasta

    input_records=\$(samtools view -c source.snapshot.bam)
    output_records=\$(samtools view -c aligned.bam)
    source_sha256_after="\$(sha256sum source.snapshot.bam | awk '{print \$1}')"
    if [[ "\${source_sha256_before}" != "\${source_sha256_after}" ]]; then
        echo "ERROR: task-local source BAM snapshot changed during alignment." >&2
        exit 97
    fi
    reference_raw_sha256_after="\$(sha256sum reference.snapshot.fasta | awk '{print \$1}')"
    if [[ "\${reference_raw_sha256_before}" != "\${reference_raw_sha256_after}" ]]; then
        echo "ERROR: task-local reference snapshot changed during alignment." >&2
        exit 94
    fi
    {
        echo "source_sha256_before=\${source_sha256_before}"
        echo "source_sha256_after=\${source_sha256_after}"
        echo "source_immutable=true"
        echo "reference_raw_sha256_before=\${reference_raw_sha256_before}"
        echo "reference_raw_sha256_after=\${reference_raw_sha256_after}"
        echo "reference_sequence_sha256=\${reference_sequence_sha256}"
        echo "reference_immutable=true"
        echo "bam_min_mapq=${bamMinMapq}"
        echo "input_records=\${input_records}"
        echo "output_records=\${output_records}"
    } >> align.log
    """
}
