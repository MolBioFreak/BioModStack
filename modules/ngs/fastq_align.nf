/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process FastqAlign {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/align", mode: 'copy'
    tag "fastq_align"

    input:
    path fastq
    path reference

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "reference.fasta", emit: reference_copy
    path "reference.fasta.fai", emit: reference_index
    path "fastq_align.log", emit: log

    script:
    def minimapPreset = ((params.fastq_minimap2_preset ?: 'map-ont') as String).trim()
    def allowSecondary = (params.fastq_minimap2_allow_secondary == true) ? 'true' : 'false'
    def declaredReferenceSha256 = params.reference_sequence_sha256?.toString()?.trim()?.toLowerCase() ?: ''
    if (!(declaredReferenceSha256 ==~ /[0-9a-f]{64}/)) {
        throw new IllegalArgumentException('reference_sequence_sha256 must be exactly 64 hexadecimal characters')
    }
    """
    set -euo pipefail

    # Snapshot and authenticate the reference before alignment.
    cp --reflink=auto -- "${reference}" reference.snapshot.fasta
    chmod 0444 reference.snapshot.fasta
    reference_raw_sha256_before="\$(sha256sum reference.snapshot.fasta | awk '{print \$1}')"
    normalized_reference="\$(awk '
      BEGIN { records=0; sequence="" }
      /^>/ {
        records++
        next
      }
      {
        line=\$0
        sub(/^[[:space:]]+/, "", line)
        sub(/[[:space:]]+\$/, "", line)
        if (line != "") sequence=sequence toupper(line)
      }
      END { if (records != 1 || sequence == "") exit 93; print sequence }
    ' reference.snapshot.fasta)" || { echo 'ERROR: reference FASTA must contain exactly one non-empty record.' >&2; exit 96; }
    [[ "\${normalized_reference}" =~ ^[ACGTN]+\$ ]] || { echo 'ERROR: reference FASTA contains unsupported symbols.' >&2; exit 96; }
    reference_sequence_sha256="\$(printf '%s' "\${normalized_reference}" | sha256sum | awk '{print \$1}')"
    if [[ "\${reference_sequence_sha256}" != "${declaredReferenceSha256}" ]]; then
        echo "ERROR: task-local reference snapshot does not match authorized reference_sequence_sha256." >&2
        exit 95
    fi

    # Align FASTQ reads to the authenticated reference with minimap2.
    MM2_ARGS=(-a -x "${minimapPreset}" -t ${task.cpus})
    if [[ "${allowSecondary}" != "true" ]]; then
        MM2_ARGS+=(--secondary=no)
    fi

    minimap2 "\${MM2_ARGS[@]}" \\
        reference.snapshot.fasta "${fastq}" 2>fastq_align.log \\
        | samtools sort -@ ${task.cpus} -o aligned.bam

    samtools quickcheck -v aligned.bam 2>>fastq_align.log
    samtools index aligned.bam
    samtools idxstats aligned.bam > /dev/null 2>>fastq_align.log
    reference_raw_sha256_after="\$(sha256sum reference.snapshot.fasta | awk '{print \$1}')"
    if [[ "\${reference_raw_sha256_before}" != "\${reference_raw_sha256_after}" ]]; then
        echo "ERROR: task-local reference snapshot changed during alignment." >&2
        exit 94
    fi
    rm -f -- "${reference}"
    cp --reflink=auto -- reference.snapshot.fasta reference.fasta
    samtools faidx reference.fasta
    """
}
