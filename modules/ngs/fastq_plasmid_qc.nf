/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

process FastqPlasmidQC {
    label 'fastq_qc_cpu'
    publishDir "${params.out_dir}/fastq_qc", mode: 'copy'
    tag "fastq_qc"

    input:
    tuple path(bam, stageAs: 'source-aligned.bam'), path(bai, stageAs: 'source-aligned.bam.bai')
    path reference, stageAs: 'expected-reference-source.fasta'
    path fastq

    output:
    path "aligned.bam", emit: alignment_bam
    path "aligned.bam.bai", emit: alignment_bai
    path "read_lengths.tsv", emit: lengths
    path "fastq_qc_summary.tsv", emit: summary
    path "fastq_alignment_stats.tsv", emit: alignment_stats
    path "fastq_coverage.tsv", emit: coverage
    path "per_base_support.tsv", emit: per_base_support
    path "qc_manifest.json", emit: qc_manifest
    path "construct_verification_input", emit: verification_input
    path "reference_qc.fasta", emit: reference
    path "reference_qc.fasta.fai", emit: reference_index
    path "igv_coverage_depth.bedgraph", emit: igv_coverage_depth
    path "igv_position_gradient.bedgraph", emit: igv_position_gradient
    path "igv_gc_content.bedgraph", emit: igv_gc_content
    path "igv_gc_zscore.bedgraph", emit: igv_gc_zscore
    path "igv_split_read_density.bedgraph", emit: igv_split_density
    path "igv_softclip_density.bedgraph", emit: igv_softclip_density
    path "igv_junction_hotspots.bed", emit: igv_hotspots
    path "igv_report_sites.bed", emit: igv_report_sites_bed
    path "igv_report_sites.tsv", emit: igv_report_sites_tsv
    path "igv_track_config.json", emit: igv_track_config
    path "igv_report.html", emit: igv_report
    path "igv_report.log", emit: igv_report_log
    path "fastq_consensus.fasta", emit: consensus
    path "fastq_consensus.fasta.fai", emit: consensus_index
    path "fastq_consensus.log", emit: consensus_log
    path "fastq_qc.log", emit: log

    script:
    def expectedSize = (params.expected_plasmid_size ?: 7000) as Integer
    def minReadLength = (params.min_fastq_read_length ?: 0) as Integer
    def minimapPreset = ((params.fastq_minimap2_preset ?: 'map-ont') as String).trim()
    def minimapAllowSecondary = (params.fastq_minimap2_allow_secondary == true) ? 'true' : 'false'
    def igvTrackWindowBp = (params.igv_track_window_bp ?: 100) as Integer
    def igvReportMaxSites = (params.igv_report_max_sites ?: 40) as Integer
    def igvReportFlankingBp = (params.igv_report_flanking_bp ?: 200) as Integer
    def codeRoot = params.code_root ?: projectDir
    def manifestJobId = ((params.job_id ?: '') as String).trim()
    if (!manifestJobId) {
        error('FASTQ plasmid QC requires an exact job_id')
    }
    def declaredReferenceSha256 = params.reference_sequence_sha256?.toString()?.trim()?.toLowerCase() ?: ''
    if (!(declaredReferenceSha256 ==~ /[0-9a-f]{64}/)) {
        throw new IllegalArgumentException('reference_sequence_sha256 must be exactly 64 hexadecimal characters')
    }
    def referenceSequenceSha256 = shellQuote(declaredReferenceSha256)
    def workflowId = ((params.workflow_id ?: params.ont_workflow_id ?: 'ont_fastq_qc') as String).trim()
    if (!(workflowId in ['ont_fastq_qc', 'ont_plasmid_qc', 'ont_construct_screening', 'wf_clone_validation'])) {
        error('FASTQ plasmid QC requires a canonical workflow_id')
    }
    def workflowIdArg = shellQuote(workflowId)
    def inputMode = ((params.input_mode ?: params.ont_input_mode ?: 'fastq') as String).trim()
    if (!(inputMode in ['fastq', 'bam', 'pod5'])) {
        error('FASTQ plasmid QC requires a canonical input_mode')
    }
    def inputModeArg = shellQuote(inputMode)
    """
    set -euo pipefail

    if ! command -v samtools >/dev/null 2>&1; then
        echo "CRITICAL_FAILURE: SAMTOOLS_RUNTIME_UNAVAILABLE" >&2
        exit 127
    fi
    SAMTOOLS_CMD=(samtools)

    if ! command -v python3 >/dev/null 2>&1; then
        echo "CRITICAL_FAILURE: PYTHON_RUNTIME_UNAVAILABLE" >&2
        exit 127
    fi
    PYTHON_CMD=(python3)

    printf "read_id\\tlength_bp\\n" > read_lengths.tsv

    if [[ "${fastq}" == *.gz ]]; then
        reader="zcat"
    else
        reader="cat"
    fi

    \${reader} "${fastq}" | awk -v minlen=${minReadLength} '
        NR % 4 == 1 {
            id = substr(\$0, 2)
            split(id, parts, /[ \\t]/)
            read_id = parts[1]
        }
        NR % 4 == 2 {
            len = length(\$0)
            if (len >= minlen) print read_id "\\t" len
        }
    ' >> read_lengths.tsv

    source_total_reads=\$(\${reader} "${fastq}" | awk '
        END {
            if (NR == 0 || NR % 4 != 0) exit 86
            print NR / 4
        }
    ') || { echo "CRITICAL_FAILURE: FASTQ_RECORD_ACCOUNTING_INVALID" >&2; exit 86; }
    reads_passing_length_filter=\$(awk 'NR > 1 {c++} END {print c + 0}' read_lengths.tsv)
    total_reads="\${source_total_reads}"
    total_bases=\$(awk 'NR > 1 {s += \$2} END {print s + 0}' read_lengths.tsv)
    mean_read_length=\$(awk 'NR > 1 {s += \$2; c++} END {if (c > 0) printf "%.2f", s / c; else printf "0"}' read_lengths.tsv)
    median_read_length=\$(awk 'NR > 1 {print \$2}' read_lengths.tsv | LC_ALL=C sort -n | awk '
        {v[NR] = \$1}
        END {
            if (NR == 0) {
                print 0
            } else if (NR % 2 == 1) {
                print v[(NR + 1) / 2]
            } else {
                printf "%.2f", (v[NR / 2] + v[(NR / 2) + 1]) / 2
            }
        }
    ')

    if [[ "\${total_bases}" -gt 0 ]]; then
        n50_read_length=\$(awk 'NR > 1 {print \$2}' read_lengths.tsv | LC_ALL=C sort -nr | awk -v half="\${total_bases}" '
            BEGIN { threshold = half / 2.0 }
            {
                cumulative += \$1
                if (!found && cumulative >= threshold) {
                    value = \$1
                    found = 1
                }
            }
            END { if (found) print value; else print 0 }
        ')
    else
        n50_read_length=0
    fi

    dimer_cutoff=\$(awk -v expected=${expectedSize} 'BEGIN { printf "%.0f", expected * 1.5 }')
    trimer_cutoff=\$(awk -v expected=${expectedSize} 'BEGIN { printf "%.0f", expected * 2.5 }')
    dimer_like_reads=\$(awk -v d="\${dimer_cutoff}" -v t="\${trimer_cutoff}" 'NR > 1 && (\$2 + 0) >= d && (\$2 + 0) < t {c++} END {print c + 0}' read_lengths.tsv)
    trimer_plus_reads=\$(awk -v t="\${trimer_cutoff}" 'NR > 1 && (\$2 + 0) >= t {c++} END {print c + 0}' read_lengths.tsv)
    estimated_copy_number_mean=\$(awk -v mean="\${mean_read_length}" -v expected=${expectedSize} 'BEGIN {
        if (expected > 0) printf "%.4f", mean / expected
        else printf "0"
    }')

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
    ' reference.snapshot.fasta)" || { echo 'CRITICAL_FAILURE: REFERENCE_FASTA_INVALID' >&2; exit 96; }
    [[ "\${normalized_reference}" =~ ^[ACGTN]+\$ ]] || { echo 'CRITICAL_FAILURE: REFERENCE_FASTA_SYMBOL_INVALID' >&2; exit 96; }
    reference_sequence_sha256="\$(printf '%s' "\${normalized_reference}" | sha256sum | awk '{print \$1}')"
    if [[ "\${reference_sequence_sha256}" != "${declaredReferenceSha256}" ]]; then
        echo "CRITICAL_FAILURE: REFERENCE_DIGEST_MISMATCH" >&2
        exit 95
    fi
    cp --reflink=auto -- reference.snapshot.fasta reference_qc.fasta
    "\${SAMTOOLS_CMD[@]}" faidx reference_qc.fasta
    reference_raw_sha256_after="\$(sha256sum reference.snapshot.fasta | awk '{print \$1}')"
    if [[ "\${reference_raw_sha256_before}" != "\${reference_raw_sha256_after}" ]]; then
        echo "CRITICAL_FAILURE: REFERENCE_SNAPSHOT_CHANGED" >&2
        exit 94
    fi
    reference_name=\$(head -n1 reference_qc.fasta.fai | cut -f1)
    reference_length=\$(head -n1 reference_qc.fasta.fai | cut -f2)

    mapped_alignment_records=\$("\${SAMTOOLS_CMD[@]}" view -c -F 4 "${bam}")
    unmapped_alignment_records=\$("\${SAMTOOLS_CMD[@]}" view -c -f 4 "${bam}")
    mapped_reads=\$("\${SAMTOOLS_CMD[@]}" view -c -F 2308 "${bam}")
    unmapped_reads=\$("\${SAMTOOLS_CMD[@]}" view -c -f 4 -F 2304 "${bam}")
    primary_mapped_reads="\${mapped_reads}"
    secondary_alignments=\$("\${SAMTOOLS_CMD[@]}" view -c -f 256 "${bam}")
    supplementary_alignments=\$("\${SAMTOOLS_CMD[@]}" view -c -f 2048 "${bam}")
    total_alignment_records=\$((mapped_alignment_records + unmapped_alignment_records))
    logical_read_records=\$((mapped_reads + unmapped_reads))
    if [[ "\${logical_read_records}" -ne "\${source_total_reads}" ]]; then
        echo "CRITICAL_FAILURE: FASTQ_BAM_READ_ACCOUNTING_MISMATCH source=\${source_total_reads} primary_records=\${logical_read_records}" >&2
        exit 86
    fi
    mapping_rate_pct=\$(awk -v mapped="\${mapped_reads}" -v total="\${source_total_reads}" 'BEGIN {
        if (total > 0) printf "%.4f", (100.0 * mapped) / total
        else printf "0"
    }')
    primary_mapping_rate_pct="\${mapping_rate_pct}"

    printf "reference\\tposition\\tdepth\\n" > fastq_coverage.tsv
    "\${SAMTOOLS_CMD[@]}" depth -aa "${bam}" | awk 'BEGIN { OFS="\\t" } { print \$1, \$2, \$3 }' >> fastq_coverage.tsv
    coverage_positions=\$(awk 'NR > 1 {c++} END {print c + 0}' fastq_coverage.tsv)
    covered_positions=\$(awk 'NR > 1 && (\$3 + 0) > 0 {c++} END {print c + 0}' fastq_coverage.tsv)
    mean_coverage=\$(awk 'NR > 1 {s += (\$3 + 0); c++} END {if (c > 0) printf "%.4f", s / c; else printf "0"}' fastq_coverage.tsv)
    median_coverage=\$(awk 'NR > 1 {print \$3 + 0}' fastq_coverage.tsv | LC_ALL=C sort -n | awk '
        {v[NR] = \$1}
        END {
            if (NR == 0) {
                print 0
            } else if (NR % 2 == 1) {
                print v[(NR + 1) / 2]
            } else {
                printf "%.4f", (v[NR / 2] + v[(NR / 2) + 1]) / 2
            }
        }
    ')
    covered_fraction_pct=\$(awk -v covered="\${covered_positions}" -v total="\${reference_length}" 'BEGIN {
        if (total > 0) printf "%.4f", (100.0 * covered) / total
        else printf "0"
    }')

    has_called_consensus_base() {
        awk '
            BEGIN { headers = 0; called = 0 }
            /^>/ { headers++; next }
            {
                line = toupper(\$0)
                if (line ~ /[ACGT]/) called = 1
            }
            END { exit(headers == 1 && called ? 0 : 1) }
        ' "\$1"
    }

    consensus_status="not_run"
    workflow_status="completed"
    verification_reason_code="phase1_manual_review_required"
    rm -f fastq_consensus.fasta fastq_consensus.fasta.fai
    if ! "\${SAMTOOLS_CMD[@]}" consensus --mode bayesian -f fasta "${bam}" > fastq_consensus.fasta 2> fastq_consensus.log; then
        echo "CRITICAL_FAILURE: SAMTOOLS_CONSENSUS_FAILED" | tee -a fastq_consensus.log >&2
        rm -f fastq_consensus.fasta fastq_consensus.fasta.fai
        exit 86
    fi
    if ! has_called_consensus_base fastq_consensus.fasta; then
        echo "CRITICAL_FAILURE: SAMTOOLS_CONSENSUS_EMPTY" | tee -a fastq_consensus.log >&2
        rm -f fastq_consensus.fasta fastq_consensus.fasta.fai
        exit 86
    fi
    consensus_status="ok"

    if ! "\${SAMTOOLS_CMD[@]}" faidx fastq_consensus.fasta >> fastq_consensus.log 2>&1; then
        echo "CRITICAL_FAILURE: SAMTOOLS_CONSENSUS_INDEX_FAILED" | tee -a fastq_consensus.log >&2
        rm -f fastq_consensus.fasta fastq_consensus.fasta.fai
        exit 86
    fi
    consensus_name=\$(awk 'NR == 1 {gsub(/^>/, "", \$0); print \$0; exit}' fastq_consensus.fasta)
    consensus_length=\$(awk 'NR > 1 {gsub(/\\r/, "", \$0); s += length(\$0)} END {print s + 0}' fastq_consensus.fasta)
    igv_report_status="not_generated"
    igv_report_cli_available=0

    if [[ ! -f "${codeRoot}/scripts/build_fastq_igv_tracks.py" ]]; then
        echo "Missing parser script: ${codeRoot}/scripts/build_fastq_igv_tracks.py" >&2
        exit 1
    fi
    if [[ ! -f "${codeRoot}/scripts/build_fastq_support_tables.py" ]]; then
        echo "Missing parser script: ${codeRoot}/scripts/build_fastq_support_tables.py" >&2
        exit 1
    fi
    if [[ ! -f "${codeRoot}/scripts/build_sequence_qc_manifest.py" ]]; then
        echo "Missing parser script: ${codeRoot}/scripts/build_sequence_qc_manifest.py" >&2
        exit 1
    fi

    "\${PYTHON_CMD[@]}" "${codeRoot}/scripts/build_fastq_igv_tracks.py" \\
        --bam "${bam}" \\
        --reference-fasta reference_qc.fasta \\
        --coverage-tsv fastq_coverage.tsv \\
        --samtools-cmd "\${SAMTOOLS_CMD[@]}" \\
        --window-bp ${igvTrackWindowBp} \\
        --hotspot-max ${igvReportMaxSites} \\
        --out-coverage-depth igv_coverage_depth.bedgraph \\
        --out-position-gradient igv_position_gradient.bedgraph \\
        --out-gc-content igv_gc_content.bedgraph \\
        --out-gc-zscore igv_gc_zscore.bedgraph \\
        --out-split-read-density igv_split_read_density.bedgraph \\
        --out-softclip-density igv_softclip_density.bedgraph \\
        --out-junction-hotspots-bed igv_junction_hotspots.bed \\
        --out-report-sites-bed igv_report_sites.bed \\
        --out-report-sites-tsv igv_report_sites.tsv

    "\${PYTHON_CMD[@]}" "${codeRoot}/scripts/build_fastq_support_tables.py" \\
        --bam "${bam}" \\
        --reference-fasta reference_qc.fasta \\
        --out-per-base-support per_base_support.tsv \\
        --samtools-cmd "\${SAMTOOLS_CMD[@]}"

    bam_local=\$(basename "${bam}")
    bai_local=\$(basename "${bai}")
    if [[ "\${bam_local}" != "aligned.bam" ]]; then
        cp -- "${bam}" aligned.bam
        bam_local="aligned.bam"
    fi
    if [[ "\${bai_local}" != "aligned.bam.bai" ]]; then
        cp -- "${bai}" aligned.bam.bai
        bai_local="aligned.bam.bai"
    fi

    if [[ ! -f "${codeRoot}/scripts/build_small_igv_report_inputs.py" ]]; then
        echo "Missing report input builder: ${codeRoot}/scripts/build_small_igv_report_inputs.py" >&2
        exit 1
    fi
    if [[ ! -f "${codeRoot}/scripts/finalize_small_igv_report.py" ]]; then
        echo "Missing report finalizer: ${codeRoot}/scripts/finalize_small_igv_report.py" >&2
        exit 1
    fi
    "\${PYTHON_CMD[@]}" "${codeRoot}/scripts/build_small_igv_report_inputs.py" \
        --job-id "${manifestJobId}" \
        --mode primary \
        --reference-fasta reference_qc.fasta \
        --reference-index reference_qc.fasta.fai \
        --alignment-bam "\${bam_local}" \
        --alignment-bai "\${bai_local}" \
        --coverage-depth igv_coverage_depth.bedgraph \
        --position-gradient igv_position_gradient.bedgraph \
        --gc-content igv_gc_content.bedgraph \
        --gc-zscore igv_gc_zscore.bedgraph \
        --split-read-density igv_split_read_density.bedgraph \
        --soft-clip-density igv_softclip_density.bedgraph \
        --junction-hotspots igv_junction_hotspots.bed \
        --out-track-config igv_track_config.json \
        --out-reference-config igv_reference_config.json

    : > igv_report.log

    if ! command -v create_report >/dev/null 2>&1; then
        echo "CRITICAL_FAILURE: IGV_REPORT_CREATE_REPORT_UNAVAILABLE" | tee -a igv_report.log >&2
        exit 86
    fi
    rm -f igv_report.html
    if ! create_report igv_report_sites.bed \
        --fasta reference_qc.fasta \
        --track-config igv_track_config.json \
        --flanking ${igvReportFlankingBp} \
        --title "FASTQ Plasmid QC IGV Report" \
        --no-embed \
        --output igv_report.html >> igv_report.log 2>&1; then
        echo "CRITICAL_FAILURE: IGV_REPORT_CREATE_REPORT_FAILED" | tee -a igv_report.log >&2
        rm -f igv_report.html
        exit 86
    fi
    if [[ ! -s igv_report.html ]]; then
        echo "CRITICAL_FAILURE: IGV_REPORT_ARTIFACT_EMPTY" | tee -a igv_report.log >&2
        exit 86
    fi
    if ! "\${PYTHON_CMD[@]}" "${codeRoot}/scripts/finalize_small_igv_report.py" \
        --report igv_report.html \
        --reference-config igv_reference_config.json \
        --track-config igv_track_config.json \
        --generated-reference-fasta reference_qc.fasta \
        --generated-reference-index reference_qc.fasta.fai \
        --max-bytes 1048576 >> igv_report.log 2>&1; then
        echo "CRITICAL_FAILURE: IGV_REPORT_FINALIZE_FAILED" | tee -a igv_report.log >&2
        rm -f igv_report.html
        exit 86
    fi
    igv_report_size=\$(wc -c < igv_report.html)
    if [[ "\${igv_report_size}" -gt 1048576 ]]; then
        echo "CRITICAL_FAILURE: IGV_REPORT_ARTIFACT_OVERSIZED bytes=\${igv_report_size} max_bytes=1048576" | tee -a igv_report.log >&2
        rm -f igv_report.html
        exit 86
    fi
    igv_report_cli_available=1
    igv_report_status="created"

    {
        echo "igv_report_cli_available=\${igv_report_cli_available}"
        echo "igv_report_status=\${igv_report_status}"
    } >> igv_report.log

    {
        echo -e "metric\\tvalue"
        echo -e "reference_name\\t\${reference_name}"
        echo -e "reference_length\\t\${reference_length}"
        echo -e "expected_plasmid_size\\t${expectedSize}"
        echo -e "min_fastq_read_length\\t${minReadLength}"
        echo -e "fastq_minimap2_preset\\t${minimapPreset}"
        echo -e "fastq_minimap2_allow_secondary\\t${minimapAllowSecondary}"
        echo -e "total_reads\\t\${total_reads}"
        echo -e "reads_passing_length_filter\\t\${reads_passing_length_filter}"
        echo -e "total_bases\\t\${total_bases}"
        echo -e "mean_read_length_bp\\t\${mean_read_length}"
        echo -e "median_read_length_bp\\t\${median_read_length}"
        echo -e "n50_read_length_bp\\t\${n50_read_length}"
        echo -e "estimated_copy_number_mean\\t\${estimated_copy_number_mean}"
        echo -e "dimer_like_reads\\t\${dimer_like_reads}"
        echo -e "trimer_plus_reads\\t\${trimer_plus_reads}"
        echo -e "mapped_reads\\t\${mapped_reads}"
        echo -e "unmapped_reads\\t\${unmapped_reads}"
        echo -e "logical_read_records\\t\${logical_read_records}"
        echo -e "mapped_alignment_records\\t\${mapped_alignment_records}"
        echo -e "unmapped_alignment_records\\t\${unmapped_alignment_records}"
        echo -e "total_alignment_records\\t\${total_alignment_records}"
        echo -e "mapping_rate_pct\\t\${mapping_rate_pct}"
        echo -e "primary_mapped_reads\\t\${primary_mapped_reads}"
        echo -e "primary_mapping_rate_pct\\t\${primary_mapping_rate_pct}"
        echo -e "secondary_alignments\\t\${secondary_alignments}"
        echo -e "supplementary_alignments\\t\${supplementary_alignments}"
        echo -e "coverage_positions\\t\${coverage_positions}"
        echo -e "covered_positions\\t\${covered_positions}"
        echo -e "covered_fraction_pct\\t\${covered_fraction_pct}"
        echo -e "mean_coverage_depth\\t\${mean_coverage}"
        echo -e "median_coverage_depth\\t\${median_coverage}"
        echo -e "consensus_status\\t\${consensus_status}"
        echo -e "consensus_name\\t\${consensus_name}"
        echo -e "consensus_length\\t\${consensus_length}"
        echo -e "igv_track_window_bp\\t${igvTrackWindowBp}"
        echo -e "igv_report_max_sites\\t${igvReportMaxSites}"
        echo -e "igv_report_flanking_bp\\t${igvReportFlankingBp}"
        echo -e "igv_report_cli_available\\t\${igv_report_cli_available}"
        echo -e "igv_report_status\\t\${igv_report_status}"
    } > fastq_alignment_stats.tsv

    {
        echo -e "metric\\tvalue"
        echo -e "reference_name\\t\${reference_name}"
        echo -e "reference_length\\t\${reference_length}"
        echo -e "reads_considered\\t\${total_reads}"
        echo -e "mapped_reads\\t\${mapped_reads}"
        echo -e "mapping_rate_pct\\t\${mapping_rate_pct}"
        echo -e "fastq_minimap2_preset\\t${minimapPreset}"
        echo -e "fastq_minimap2_allow_secondary\\t${minimapAllowSecondary}"
        echo -e "mean_read_length_bp\\t\${mean_read_length}"
        echo -e "n50_read_length_bp\\t\${n50_read_length}"
        echo -e "estimated_copy_number_mean\\t\${estimated_copy_number_mean}"
        echo -e "dimer_like_reads\\t\${dimer_like_reads}"
        echo -e "trimer_plus_reads\\t\${trimer_plus_reads}"
        echo -e "mean_coverage_depth\\t\${mean_coverage}"
        echo -e "covered_fraction_pct\\t\${covered_fraction_pct}"
        echo -e "consensus_status\\t\${consensus_status}"
        echo -e "consensus_length\\t\${consensus_length}"
        echo -e "igv_report_status\\t\${igv_report_status}"
    } > fastq_qc_summary.tsv

    {
        echo "FASTQ plasmid QC complete"
        echo "Reference: \${reference_name} (\${reference_length} bp)"
        echo "Reads considered: \${total_reads}; mapped: \${mapped_reads} (rate \${mapping_rate_pct}%)"
        echo "Read length mean/N50: \${mean_read_length}/\${n50_read_length} bp"
        echo "Coverage mean/median: \${mean_coverage}/\${median_coverage}"
        echo "Consensus: \${consensus_status} (\${consensus_length} bp)"
    } > fastq_qc.log

    "\${PYTHON_CMD[@]}" "${codeRoot}/scripts/build_construct_verification_input.py" \\
        --reference-fasta reference_qc.fasta \\
        --expected-reference-sha256 ${referenceSequenceSha256} \\
        --source-reads "${fastq}" \\
        --consensus-fasta fastq_consensus.fasta \\
        --consensus-method samtools_consensus \\
        --out-dir construct_verification_input

    "\${PYTHON_CMD[@]}" "${codeRoot}/scripts/build_sequence_qc_manifest.py" \\
        --out qc_manifest.json \\
        --job-id "${manifestJobId}" \\
        --workflow-id ${workflowIdArg} \\
        --input-mode ${inputModeArg} \\
        --sample-name "fastq_plasmid_qc" \\
        --reference-fasta reference_qc.fasta \\
        --expected-sha256 ${referenceSequenceSha256} \\
        --reference-index reference_qc.fasta.fai \\
        --summary fastq_qc_summary.tsv \\
        --read-lengths read_lengths.tsv \\
        --alignment-stats fastq_alignment_stats.tsv \\
        --coverage fastq_coverage.tsv \\
        --per-base-support per_base_support.tsv \\
        --consensus fastq_consensus.fasta \\
        --consensus-index fastq_consensus.fasta.fai \\
        --consensus-log fastq_consensus.log \\
        --consensus-status "\${consensus_status}" \\
        --workflow-status "\${workflow_status}" \\
        --verification-status review_required \\
        --verification-reason-code "\${verification_reason_code}" \\
        --alignment-bam "\${bam_local}" \\
        --alignment-bai "\${bai_local}" \\
        --igv-coverage-depth igv_coverage_depth.bedgraph \\
        --igv-position-gradient igv_position_gradient.bedgraph \\
        --igv-gc-content igv_gc_content.bedgraph \\
        --igv-gc-zscore igv_gc_zscore.bedgraph \\
        --igv-split-read-density igv_split_read_density.bedgraph \\
        --igv-softclip-density igv_softclip_density.bedgraph \\
        --igv-junction-hotspots igv_junction_hotspots.bed \\
        --igv-report-sites-bed igv_report_sites.bed \\
        --igv-report-sites-tsv igv_report_sites.tsv \\
        --igv-track-config igv_track_config.json \\
        --igv-report igv_report.html \\
        --igv-report-log igv_report.log \\
        --log fastq_qc.log
    """
}
