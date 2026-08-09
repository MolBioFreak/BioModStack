#!/usr/bin/env bash
set -euo pipefail

events=""
bam=""
dimer_count="0"
screened_pos="NA"
screened_support="0"
dominant_split_pos="NA"
dominant_split_support="0"
single_ref_pos="NA"
single_ref_support="0"
dominant_junction_pos="NA"
dominant_junction_support="0"
threads="1"
out_consensus="dominant_dimer_consensus.fasta"
out_log="dominant_dimer_consensus.log"
out_metadata="dominant_dimer_consensus_metadata.tsv"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --events) events="$2"; shift 2 ;;
        --bam) bam="$2"; shift 2 ;;
        --dimer-count) dimer_count="$2"; shift 2 ;;
        --screened-pos) screened_pos="$2"; shift 2 ;;
        --screened-support) screened_support="$2"; shift 2 ;;
        --dominant-split-pos) dominant_split_pos="$2"; shift 2 ;;
        --dominant-split-support) dominant_split_support="$2"; shift 2 ;;
        --single-ref-pos) single_ref_pos="$2"; shift 2 ;;
        --single-ref-support) single_ref_support="$2"; shift 2 ;;
        --dominant-junction-pos) dominant_junction_pos="$2"; shift 2 ;;
        --dominant-junction-support) dominant_junction_support="$2"; shift 2 ;;
        --threads) threads="$2"; shift 2 ;;
        --out-consensus) out_consensus="$2"; shift 2 ;;
        --out-log) out_log="$2"; shift 2 ;;
        --out-metadata) out_metadata="$2"; shift 2 ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

: > "$out_log"
rm -f "$out_consensus"

tmpdir="$(mktemp -d)"
cleanup() {
    rm -rf "$tmpdir"
}
trap cleanup EXIT

normalize_num() {
    local raw="${1:-0}"
    if [[ "$raw" =~ ^-?[0-9]+$ ]]; then
        echo "$raw"
    else
        echo "0"
    fi
}

is_valid_pos() {
    local raw="${1:-}"
    local norm
    norm="$(echo "$raw" | tr '[:upper:]' '[:lower:]' | xargs)"
    [[ -n "$norm" && "$norm" != "na" && "$norm" != "null" && "$norm" != "none" && "$norm" != "undefined" && "$norm" != "n/a" ]]
}

has_called_consensus_base() {
    awk '
        BEGIN { headers = 0; called = 0 }
        /^>/ { headers++; next }
        {
            line = toupper($0)
            if (line ~ /[ACGT]/) called = 1
        }
        END { exit(headers == 1 && called ? 0 : 1) }
    ' "$1"
}

critical_failure() {
    local reason="$1"
    echo "CRITICAL_FAILURE: $reason" | tee -a "$out_log" >&2
    rm -f "$out_consensus"
    exit 86
}

status="not_applicable"
breakpoint_pos="NA"
breakpoint_source="none"
support_reads="0"
support_pct="0.0000"
read_id="NA"
read_copies="0"
read_support_pct="0.0000"

dimer_count_num="$(normalize_num "$dimer_count")"
screened_support_num="$(normalize_num "$screened_support")"
dominant_split_support_num="$(normalize_num "$dominant_split_support")"
single_ref_support_num="$(normalize_num "$single_ref_support")"
dominant_junction_support_num="$(normalize_num "$dominant_junction_support")"

candidate_pos=""
candidate_source=""
if is_valid_pos "$screened_pos" && [[ "$screened_support_num" -gt 0 ]]; then
    candidate_pos="$screened_pos"
    candidate_source="screened_primary_breakpoint"
elif is_valid_pos "$dominant_split_pos" && [[ "$dominant_split_support_num" -gt 0 ]]; then
    candidate_pos="$dominant_split_pos"
    candidate_source="dominant_split_hotspot"
elif is_valid_pos "$single_ref_pos" && [[ "$single_ref_support_num" -gt 0 ]]; then
    candidate_pos="$single_ref_pos"
    candidate_source="single_ref_split_hotspot"
elif is_valid_pos "$dominant_junction_pos" && [[ "$dominant_junction_support_num" -gt 0 ]]; then
    candidate_pos="$dominant_junction_pos"
    candidate_source="dominant_junction_hotspot"
fi

if [[ -n "$candidate_pos" ]]; then
    if [[ ! -f "$events" ]]; then
        critical_failure "DOMINANT_CONSENSUS_EVENTS_UNAVAILABLE"
    fi
    if ! awk -F"\t" -v pos="$candidate_pos" '
        NR == 1 { next }
        ($4 + 0) == (pos + 0) && ($5 + 0) > 0 { print $1 }
    ' "$events" | LC_ALL=C sort -u > "$tmpdir/read_ids.txt"; then
        critical_failure "DOMINANT_CONSENSUS_READ_SELECTION_FAILED"
    fi
    if [[ ! -s "$tmpdir/read_ids.txt" ]]; then
        critical_failure "DOMINANT_CONSENSUS_READS_UNAVAILABLE"
    fi
    if [[ ! -f "$bam" ]]; then
        critical_failure "DOMINANT_CONSENSUS_BAM_UNAVAILABLE"
    fi

    support_reads="$(wc -l < "$tmpdir/read_ids.txt" | tr -d '[:space:]')"
    support_reads="$(normalize_num "$support_reads")"
    support_pct="$(awk -v support="$support_reads" -v total="$dimer_count_num" 'BEGIN {
        if (total > 0) printf "%.4f", (100.0 * support) / total;
        else printf "0.0000";
    }')"
    breakpoint_pos="$candidate_pos"
    breakpoint_source="$candidate_source"

    if ! samtools view -h "$bam" 2>> "$out_log" \
        | awk 'NR == FNR { keep[$1]=1; next } /^@/ { print; next } ($1 in keep) { print }' "$tmpdir/read_ids.txt" - \
        > "$tmpdir/subset.sam" 2>> "$out_log"; then
        critical_failure "DOMINANT_CONSENSUS_SUBSET_FAILED"
    fi
    if [[ ! -s "$tmpdir/subset.sam" ]]; then
        critical_failure "DOMINANT_CONSENSUS_SUBSET_EMPTY"
    fi
    if ! samtools sort -@ "$threads" -o "$tmpdir/subset.bam" "$tmpdir/subset.sam" >> "$out_log" 2>&1; then
        critical_failure "DOMINANT_CONSENSUS_SORT_FAILED"
    fi
    if [[ ! -s "$tmpdir/subset.bam" ]]; then
        critical_failure "DOMINANT_CONSENSUS_BAM_EMPTY"
    fi
    if ! samtools index "$tmpdir/subset.bam" >> "$out_log" 2>&1; then
        critical_failure "DOMINANT_CONSENSUS_INDEX_FAILED"
    fi
    if ! samtools consensus --mode bayesian -f fasta "$tmpdir/subset.bam" > "$out_consensus" 2>> "$out_log"; then
        critical_failure "SAMTOOLS_CONSENSUS_FAILED"
    fi
    if ! has_called_consensus_base "$out_consensus"; then
        critical_failure "SAMTOOLS_CONSENSUS_EMPTY"
    fi
    status="subset_samtools_consensus"
fi

{
    echo -e "metric\tvalue"
    echo -e "dominant_consensus_status\t$status"
    echo -e "dominant_consensus_breakpoint_position_mod_ref\t$breakpoint_pos"
    echo -e "dominant_consensus_breakpoint_source\t$breakpoint_source"
    echo -e "dominant_consensus_support_reads\t$support_reads"
    echo -e "dominant_consensus_support_pct\t$support_pct"
    echo -e "dominant_consensus_read_id\t$read_id"
    echo -e "dominant_consensus_read_copies\t$read_copies"
    echo -e "dominant_consensus_read_support_pct\t$read_support_pct"
} > "$out_metadata"

echo "dominant consensus status=$status breakpoint=$breakpoint_pos source=$breakpoint_source" >> "$out_log"
