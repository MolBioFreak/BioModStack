#!/usr/bin/env bash
# Build the default digest-bound dimer alignment-session manifest without Python.
set -euo pipefail

job_id="${1:?exact job_id is required}"
expected_source_reference_sha256="${2:?authorized source reference SHA-256 is required}"
workflow_id="${3:?canonical workflow_id is required}"
input_mode="${4:?canonical input_mode is required}"
out="${5:-qc_manifest.json}"
if [[ ! "$job_id" =~ ^[A-Za-z0-9][A-Za-z0-9._\ -]{0,255}$ || "$job_id" == *..* ]]; then
    echo "exact safe job_id is required" >&2
    exit 1
fi
[[ "$expected_source_reference_sha256" =~ ^[0-9a-f]{64}$ ]] || { echo "authorized source reference SHA-256 is invalid" >&2; exit 1; }
case "$workflow_id" in
    ont_fastq_qc|ont_plasmid_qc|ont_construct_screening|wf_clone_validation) ;;
    *) echo "canonical workflow_id is invalid" >&2; exit 1 ;;
esac
case "$input_mode" in
    fastq|bam|pod5) ;;
    *) echo "canonical input_mode is invalid" >&2; exit 1 ;;
esac
reference="dimer_reference.fasta"
[[ -f "$reference" && ! -L "$reference" ]] || { echo "alignment-session manifest requires a regular reference artifact" >&2; exit 1; }

normalized_sequence="$(awk '!/^>/ {gsub(/^[[:space:]]+|[[:space:]]+$/, ""); if (length) printf "%s", $0}' "$reference" | tr '[:lower:]' '[:upper:]')"
[[ -n "$normalized_sequence" ]] || { echo "reference FASTA has no sequence" >&2; exit 1; }
(( ${#normalized_sequence} % 2 == 0 )) || { echo "dimer reference length is invalid" >&2; exit 1; }
source_length=$(( ${#normalized_sequence} / 2 ))
source_sequence="${normalized_sequence:0:source_length}"
[[ "$normalized_sequence" == "$source_sequence$source_sequence" ]] || { echo "dimer reference is not an exact source-reference tandem" >&2; exit 1; }
observed_source_reference_sha256="$(printf '%s' "$source_sequence" | sha256sum | awk '{print $1}')"
[[ "$observed_source_reference_sha256" == "$expected_source_reference_sha256" ]] || { echo "dimer source reference does not match authorized identity" >&2; exit 1; }
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
    --arg workflow_id "$workflow_id" \
    --arg input_mode "$input_mode" \
    --arg reference_sha "$normalized_reference_sha256" \
    --arg source_reference_sha "$expected_source_reference_sha256" \
    --argjson artifacts "$artifacts" \
    '{artifact_schema_version:2,schema:"sequence_qc.manifest.v1",workflow_id:$workflow_id,job_id:$job_id,input_mode:$input_mode,analysis_status:"completed",alignment_session:{mode:"dimer_candidates",reference_sequence_sha256:$reference_sha,source_reference_sequence_sha256:$source_reference_sha,binding:"authorized source reference binds an exact tandem dimer reference plus BAM and index digests"},artifacts:$artifacts}' > "$out"
