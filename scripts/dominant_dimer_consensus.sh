#!/usr/bin/env bash
set -euo pipefail

events=""
bam=""
fastq=""
dimer_consensus=""
dimer_reference=""
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
        --fastq) fastq="$2"; shift 2 ;;
        --dimer-consensus) dimer_consensus="$2"; shift 2 ;;
        --dimer-reference) dimer_reference="$2"; shift 2 ;;
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

: > "${out_log}"

tmpdir="$(mktemp -d)"
cleanup() {
    rm -rf "${tmpdir}"
}
trap cleanup EXIT

normalize_num() {
    local raw="${1:-0}"
    if [[ "${raw}" =~ ^-?[0-9]+$ ]]; then
        echo "${raw}"
    else
        echo "0"
    fi
}

is_valid_pos() {
    local raw="${1:-}"
    local norm
    norm="$(echo "${raw}" | tr '[:upper:]' '[:lower:]' | xargs)"
    [[ -n "${norm}" && "${norm}" != "na" && "${norm}" != "null" && "${norm}" != "none" && "${norm}" != "undefined" && "${norm}" != "n/a" ]]
}

status="not_run"
breakpoint_pos="NA"
breakpoint_source="none"
support_reads="0"
support_pct="0.0000"
read_id="NA"
read_copies="0"
read_support_pct="0.0000"

dimer_count_num="$(normalize_num "${dimer_count}")"
screened_support_num="$(normalize_num "${screened_support}")"
dominant_split_support_num="$(normalize_num "${dominant_split_support}")"
single_ref_support_num="$(normalize_num "${single_ref_support}")"
dominant_junction_support_num="$(normalize_num "${dominant_junction_support}")"

candidate_pos=""
candidate_source=""
if is_valid_pos "${screened_pos}" && [[ "${screened_support_num}" -gt 0 ]]; then
    candidate_pos="${screened_pos}"
    candidate_source="screened_primary_breakpoint"
elif is_valid_pos "${dominant_split_pos}" && [[ "${dominant_split_support_num}" -gt 0 ]]; then
    candidate_pos="${dominant_split_pos}"
    candidate_source="dominant_split_hotspot"
elif is_valid_pos "${single_ref_pos}" && [[ "${single_ref_support_num}" -gt 0 ]]; then
    candidate_pos="${single_ref_pos}"
    candidate_source="single_ref_split_hotspot"
elif is_valid_pos "${dominant_junction_pos}" && [[ "${dominant_junction_support_num}" -gt 0 ]]; then
    candidate_pos="${dominant_junction_pos}"
    candidate_source="dominant_junction_hotspot"
fi

if [[ -n "${candidate_pos}" && -f "${events}" ]]; then
    awk -F"\t" -v pos="${candidate_pos}" '
        NR == 1 { next }
        ($4 + 0) == (pos + 0) && ($5 + 0) > 0 { print $1 }
    ' "${events}" | LC_ALL=C sort -u > "${tmpdir}/read_ids.txt" || true

    if [[ -s "${tmpdir}/read_ids.txt" && -f "${bam}" ]]; then
        support_reads="$(wc -l < "${tmpdir}/read_ids.txt" | tr -d '[:space:]')"
        support_reads="$(normalize_num "${support_reads}")"
        support_pct="$(awk -v support="${support_reads}" -v total="${dimer_count_num}" 'BEGIN {
            if (total > 0) printf "%.4f", (100.0 * support) / total;
            else printf "0.0000";
        }')"

        breakpoint_pos="${candidate_pos}"
        breakpoint_source="${candidate_source}"

        samtools view -h "${bam}" 2>> "${out_log}" \
            | awk 'NR == FNR { keep[$1]=1; next } /^@/ { print; next } ($1 in keep) { print }' "${tmpdir}/read_ids.txt" - \
            > "${tmpdir}/subset.sam" 2>> "${out_log}" || true

        if [[ -s "${tmpdir}/subset.sam" ]]; then
            samtools sort -@ "${threads}" -o "${tmpdir}/subset.bam" "${tmpdir}/subset.sam" >> "${out_log}" 2>&1 || true
            if [[ -s "${tmpdir}/subset.bam" ]]; then
                samtools index "${tmpdir}/subset.bam" >> "${out_log}" 2>&1 || true
                if samtools consensus -f fasta "${tmpdir}/subset.bam" > "${out_consensus}" 2>> "${out_log}"; then
                    status="subset_consensus"
                fi
            fi
        fi
    fi
fi

if [[ ! -s "${out_consensus}" && -f "${fastq}" ]]; then
    awk '
        NR % 4 == 1 {
            rid = substr($0, 2)
            sub(/[ \t].*/, "", rid)
            next
        }
        NR % 4 == 2 {
            seq = toupper($0)
            if (!(seq in first_id)) first_id[seq] = rid
            counts[seq]++
            total++
        }
        END {
            best_seq = ""
            best_count = -1
            best_id = "NA"
            for (seq in counts) {
                c = counts[seq]
                if (c > best_count || (c == best_count && length(seq) > length(best_seq)) || (c == best_count && length(seq) == length(best_seq) && seq < best_seq)) {
                    best_count = c
                    best_seq = seq
                    best_id = first_id[seq]
                }
            }
            if (best_count > 0 && length(best_seq) > 0) {
                printf("read_id\tcopies\ttotal_reads\n%s\t%d\t%d\n", best_id, best_count, total) > "'"${tmpdir}/most_abundant.stats.tsv"'"
                print ">dominant_read|id=" best_id "|copies=" best_count "|total_reads=" total > "'"${tmpdir}/most_abundant.fasta"'"
                width = 80
                while (length(best_seq) > width) {
                    print substr(best_seq, 1, width) >> "'"${tmpdir}/most_abundant.fasta"'"
                    best_seq = substr(best_seq, width + 1)
                }
                if (length(best_seq) > 0) print best_seq >> "'"${tmpdir}/most_abundant.fasta"'"
            }
        }
    ' "${fastq}" >> "${out_log}" 2>&1 || true

    if [[ -s "${tmpdir}/most_abundant.fasta" ]]; then
        cp "${tmpdir}/most_abundant.fasta" "${out_consensus}"
        status="most_abundant_read_fallback"
    fi
fi

if [[ ! -s "${out_consensus}" ]]; then
    if [[ -n "${dimer_consensus}" && -s "${dimer_consensus}" ]]; then
        cp "${dimer_consensus}" "${out_consensus}"
        status="global_consensus_copy"
    elif [[ -n "${dimer_reference}" && -s "${dimer_reference}" ]]; then
        cp "${dimer_reference}" "${out_consensus}"
        status="reference_copy"
    else
        : > "${out_consensus}"
        status="empty_fallback"
    fi
fi

if [[ -s "${tmpdir}/most_abundant.stats.tsv" ]]; then
    read_id="$(awk -F"\t" 'NR == 2 {print $1; exit}' "${tmpdir}/most_abundant.stats.tsv")"
    read_copies="$(awk -F"\t" 'NR == 2 {print $2 + 0; exit}' "${tmpdir}/most_abundant.stats.tsv")"
    read_support_pct="$(awk -F"\t" 'NR == 2 {
        total = $3 + 0
        if (total > 0) printf "%.4f", (100.0 * ($2 + 0)) / total
        else printf "0.0000"
        exit
    }' "${tmpdir}/most_abundant.stats.tsv")"
fi

{
    echo -e "metric\tvalue"
    echo -e "dominant_consensus_status\t${status}"
    echo -e "dominant_consensus_breakpoint_position_mod_ref\t${breakpoint_pos}"
    echo -e "dominant_consensus_breakpoint_source\t${breakpoint_source}"
    echo -e "dominant_consensus_support_reads\t${support_reads}"
    echo -e "dominant_consensus_support_pct\t${support_pct}"
    echo -e "dominant_consensus_read_id\t${read_id}"
    echo -e "dominant_consensus_read_copies\t${read_copies}"
    echo -e "dominant_consensus_read_support_pct\t${read_support_pct}"
} > "${out_metadata}"

echo "dominant consensus status=${status} breakpoint=${breakpoint_pos} source=${breakpoint_source}" >> "${out_log}"
