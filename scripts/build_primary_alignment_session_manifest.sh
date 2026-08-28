#!/usr/bin/env bash
# Build a digest-bound primary alignment-session manifest.
set -euo pipefail

job_id="${1:?exact job_id is required}"
expected_reference_sha256="${2:?authorized reference SHA-256 is required}"
workflow_id="${3:?canonical workflow_id is required}"
input_mode="${4:?canonical input_mode is required}"
out="${5:-qc_manifest.json}"
reference_topology="${6:?reference topology is required}"

if [[ ! "$job_id" =~ ^[A-Za-z0-9][A-Za-z0-9._\ -]{0,255}$ || "$job_id" == *..* ]]; then
    echo "exact safe job_id is required" >&2
    exit 1
fi
[[ "$expected_reference_sha256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "authorized reference SHA-256 is invalid" >&2
    exit 1
}
case "$workflow_id" in
    ont_fastq_qc|ont_plasmid_qc|ont_construct_screening|wf_clone_validation) ;;
    *) echo "canonical workflow_id is invalid" >&2; exit 1 ;;
esac
[[ "$input_mode" == "bam" ]] || { echo "primary alignment manifest requires BAM input mode" >&2; exit 1; }
case "$reference_topology" in
    linear|circular) ;;
    *) echo "reference topology must be linear or circular" >&2; exit 1 ;;
esac

for artifact in aligned.bam aligned.bam.bai reference.fasta reference.fasta.fai; do
    [[ -f "$artifact" && ! -L "$artifact" ]] || {
        echo "required primary alignment artifact is unavailable: $artifact" >&2
        exit 1
    }
done

normalized_reference="$(awk '
  BEGIN { records=0; sequence="" }
  {
    line=$0
    sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line)
    if (line == "") next
    if (substr(line,1,1) == ">") { records++; if (records > 1) exit 91; next }
    if (records != 1) exit 92
    sequence=sequence toupper(line)
  }
  END { if (records != 1 || sequence == "") exit 93; print sequence }
' reference.fasta)" || {
    echo "reference FASTA must contain exactly one non-empty record" >&2
    exit 1
}
observed_reference_sha256="$(printf '%s' "$normalized_reference" | sha256sum | cut -d' ' -f1)"
[[ "$observed_reference_sha256" == "$expected_reference_sha256" ]] || {
    echo "primary reference does not match authorized identity" >&2
    exit 1
}

artifacts='[]'
append_artifact() {
    local kind="$1" local_path="$2" published_path="$3" sha size
    sha="$(sha256sum "$local_path" | cut -d' ' -f1)"
    size="$(stat -c '%s' "$local_path")"
    artifacts="$(jq -c \
        --arg kind "$kind" \
        --arg path "$published_path" \
        --arg sha "$sha" \
        --argjson size "$size" \
        '. + [{kind:$kind,path:$path,required:true,state:"present",sha256:$sha,size_bytes:$size}]' \
        <<<"$artifacts")"
}

append_artifact alignment_bam aligned.bam align/aligned.bam
append_artifact alignment_bai aligned.bam.bai align/aligned.bam.bai
append_artifact reference reference.fasta align/reference.fasta
append_artifact reference_index reference.fasta.fai align/reference.fasta.fai

jq -n \
    --arg job_id "$job_id" \
    --arg workflow_id "$workflow_id" \
    --arg input_mode "$input_mode" \
    --arg reference_sha "$expected_reference_sha256" \
    --arg reference_topology "$reference_topology" \
    --argjson artifacts "$artifacts" \
    '{
        artifact_schema_version:2,
        schema:"sequence_qc.manifest.v1",
        workflow_id:$workflow_id,
        job_id:$job_id,
        input_mode:$input_mode,
        analysis_status:"completed",
        summary:{reference_topology:$reference_topology},
        alignment_session:{
            mode:"primary",
            reference_sequence_sha256:$reference_sha,
            source_reference_sequence_sha256:$reference_sha,
            binding:"authorized source reference binds exact primary BAM, index, FASTA, and index digests"
        },
        artifacts:$artifacts
    }' > "$out"
