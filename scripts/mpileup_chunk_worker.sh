#!/usr/bin/env bash
# Emit one mpileup region chunk for FASTQ QC consensus fallback.
#
# Arguments: <chunk_start> <chunk_end>
# Environment: SAMTOOLS_CMD (array, space-joined), REFERENCE_QC_FASTA,
#   REFERENCE_QC_NAME, MPILEUP_QC_BAM
#
# Output: mpileup.chunk.<start-1> in the current working directory, appended
# to fastq_consensus.log. This script is a separate file so the Nextflow
# template never embeds positional/arithmetic shell syntax directly.
set -euo pipefail

chunk_start="${1:?chunk start required}"
chunk_end="${2:?chunk end required}"

read -r -a samtools_cmd <<< "${SAMTOOLS_CMD_STR:?SAMTOOLS_CMD_STR required}"
chunk_file=$(printf 'mpileup.chunk.%04d' "$((chunk_start - 1))")

"${samtools_cmd[@]}" mpileup -aa -A -d 1000000 \
    -f "${REFERENCE_QC_FASTA:?REFERENCE_QC_FASTA required}" \
    -r "${REFERENCE_QC_NAME:?REFERENCE_QC_NAME required}:${chunk_start}-${chunk_end}" \
    "${MPILEUP_QC_BAM:?MPILEUP_QC_BAM required}" \
    2>> fastq_consensus.log > "${chunk_file}"
