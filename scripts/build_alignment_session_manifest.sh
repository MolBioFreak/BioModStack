#!/usr/bin/env bash
# Build the default digest-bound dimer alignment-session manifest without Python.
set -euo pipefail

job_id="${1:?exact job_id is required}"
out="${2:-qc_manifest.json}"
if [[ ! "$job_id" =~ ^[A-Za-z0-9][A-Za-z0-9._\ -]{0,255}$ || "$job_id" == *..* ]]; then
    echo "exact safe job_id is required" >&2
    exit 1
fi
reference="dimer_reference.fasta"
[[ -f "$reference" && ! -L "$reference" ]] || { echo "alignment-session manifest requires a regular reference artifact" >&2; exit 1; }

normalized_sequence="$(awk '!/^>/ {gsub(/^[[:space:]]+|[[:space:]]+$/, ""); if (length) printf "%s", $0}' "$reference" | tr '[:lower:]' '[:upper:]')"
[[ -n "$normalized_sequence" ]] || { echo "reference FASTA has no sequence" >&2; exit 1; }
normalized_reference_sha256="$(printf '%s' "$normalized_sequence" | sha256sum | awk '{print $1}')"
[[ "$normalized_reference_sha256" =~ ^[0-9a-f]{64}$ ]] || { echo "failed to digest normalized reference" >&2; exit 1; }

artifacts='[]'
append_artifact() {
    local kind="$1" path="$2" required="$3" state sha size
    if [[ -f "$path" && ! -L "$path" ]]; then
        state='present'
        sha="$(sha256sum "$path" | awk '{print $1}')"
        size="$(stat -c '%s' "$path")"
        artifacts="$(jq -c --arg kind "$kind" --arg path "$path" --argjson required "$required" --arg state "$state" --arg sha "$sha" --argjson size "$size" '. + [{kind:$kind,path:$path,required:$required,state:$state,sha256:$sha,size_bytes:$size}]' <<<"$artifacts")"
    else
        state='missing_optional'
        [[ "$required" == true ]] && state='missing_required'
        artifacts="$(jq -c --arg kind "$kind" --argjson required "$required" --arg state "$state" '. + [{kind:$kind,path:null,required:$required,state:$state,sha256:null,size_bytes:null}]' <<<"$artifacts")"
    fi
}

append_artifact reference dimer_reference.fasta true
append_artifact reference_index dimer_reference.fasta.fai false
append_artifact dimer_alignment_bam dimer_candidates.aligned.bam false
append_artifact dimer_alignment_bai dimer_candidates.aligned.bam.bai false
append_artifact dimer_analysis_summary dimer_analysis_summary.tsv false

jq -n \
    --arg job_id "$job_id" \
    --arg reference_sha "$normalized_reference_sha256" \
    --argjson artifacts "$artifacts" \
    '{artifact_schema_version:2,schema:"sequence_qc.manifest.v1",job_id:$job_id,alignment_session:{mode:"dimer_candidates",reference_sequence_sha256:$reference_sha,binding:"server-generated manifest binds BAM, index, and normalized reference digests"},artifacts:$artifacts}' > "$out"
