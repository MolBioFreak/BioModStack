/**
 * Nanopore Dorado basecalling and methylation analysis module.
 *
 * Self-contained: no `include` from other .nf files (BMS-lite extractable).
 * Processes: DoradoBasecall (GPU) → DoradoAlign → ModkitPileup / ModkitSummary
 *            + BAM ingest/sort/index + FASTQ align/plasmid-QC helpers
 */


// ═══════════════════════════════════════════════════════════════════════════════
// Process 1: GPU basecalling with optional modified-base detection
// ═══════════════════════════════════════════════════════════════════════════════
process DoradoBasecall {
    label 'dorado_gpu'
    label 'gpu'
    publishDir "${params.out_dir}/basecall", mode: 'copy'
    tag "basecall"

    input:
    path pod5_dir

    output:
    path "calls.bam", emit: bam
    path "basecall.log", emit: log
    path "sequencing_summary.tsv", emit: summary, optional: true

    script:
    def model = params.dorado_model ?: 'sup'
    def normalizedModifiedBases = params.modified_bases?.toString()?.trim()
    if (normalizedModifiedBases == '6mA 5mC') {
        normalizedModifiedBases = '6mA 4mC_5mC'
    }
    def modBases = normalizedModifiedBases && normalizedModifiedBases != 'none' ? "--modified-bases ${normalizedModifiedBases}" : ''
    def minQscore = params.min_qscore ? "--min-qscore ${params.min_qscore}" : ''
    def trimAdapt = params.trim_adapters != false ? '' : '--no-trim'
    def emitSummary = params.emit_summary != false ? '--emit-summary' : ''
    def batchSize = params.dorado_batch_size ? "--batchsize ${params.dorado_batch_size}" : ''
    def doradoDevice = (params.dorado_device ?: 'cuda:0').toString().trim()
    if (!doradoDevice) {
        doradoDevice = 'cuda:0'
    }
    """
    mkdir -p /weights/dorado

    dorado basecaller \\
        ${model} \\
        ${pod5_dir} \\
        --models-directory /weights/dorado \\
        ${modBases} \\
        ${minQscore} \\
        ${trimAdapt} \\
        ${emitSummary} \\
        ${batchSize} \\
        --device ${doradoDevice} \\
        > calls.bam \\
        2> basecall.log

    # Dorado emits sequencing_summary.txt by default; normalize to .tsv for pipeline consumers.
    if [[ -f sequencing_summary.txt && ! -f sequencing_summary.tsv ]]; then
        mv sequencing_summary.txt sequencing_summary.tsv
    fi
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 2: Align basecalled reads to reference with minimap2 via dorado
// ═══════════════════════════════════════════════════════════════════════════════
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
    def bamMinMapq = Math.max((params.bam_min_mapq ?: 0) as Integer, 0)
    """
    set -euo pipefail

    # Sort and align; preserve MM/ML methylation tags
    if [[ ${bamMinMapq} -gt 0 ]]; then
        dorado aligner \\
            ${reference} \\
            ${bam} \\
            --threads ${task.cpus} \\
            2>align.log \\
            | samtools view -h -q ${bamMinMapq} - \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
        echo "Applied MAPQ filter: >= ${bamMinMapq}" >> align.log
    else
        dorado aligner \\
            ${reference} \\
            ${bam} \\
            --threads ${task.cpus} \\
            2>align.log \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
    fi

    samtools index aligned.bam
    cp ${reference} reference.fasta
    samtools faidx reference.fasta

    input_records=\$(samtools view -c ${bam})
    output_records=\$(samtools view -c aligned.bam)
    {
        echo "bam_min_mapq=${bamMinMapq}"
        echo "input_records=\${input_records}"
        echo "output_records=\${output_records}"
    } >> align.log
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 3: Prepare existing BAM for downstream analysis (sort + index)
// ═══════════════════════════════════════════════════════════════════════════════
process PrepareBamForAnalysis {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/align", mode: 'copy'
    tag "bam_prepare"

    input:
    path bam

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "bam_prepare.log", emit: log

    script:
    def bamMinMapq = Math.max((params.bam_min_mapq ?: 0) as Integer, 0)
    """
    set -euo pipefail

    # Preserve MM/ML tags while enforcing coordinate-sorted BAM + index for modkit.
    if [[ ${bamMinMapq} -gt 0 ]]; then
        samtools view -h -q ${bamMinMapq} ${bam} 2> bam_prepare.log \\
            | samtools sort -@ ${task.cpus} -o aligned.bam
        echo "Applied MAPQ filter: >= ${bamMinMapq}" >> bam_prepare.log
    else
        samtools sort -@ ${task.cpus} -o aligned.bam ${bam} 2> bam_prepare.log
    fi
    samtools index aligned.bam 2>> bam_prepare.log
    input_records=\$(samtools view -c ${bam})
    output_records=\$(samtools view -c aligned.bam)
    {
        echo "bam_min_mapq=${bamMinMapq}"
        echo "input_records=\${input_records}"
        echo "output_records=\${output_records}"
    } >> bam_prepare.log
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 4: Validate BAM has mapped reads before modkit/assembly without reference
// ═══════════════════════════════════════════════════════════════════════════════
process ValidateMappedBam {
    label 'dorado_cpu'
    tag "bam_mapped_check"

    input:
    tuple path(bam), path(bai)

    output:
    tuple path("aligned.bam"), path("aligned.bam.bai"), emit: aligned
    path "bam_mapped_check.log", emit: log

    script:
    """
    set -euo pipefail

    total_reads=\$(samtools view -c "${bam}")
    mapped_reads=\$(samtools view -c -F 4 "${bam}")
    {
        echo "total_reads=\${total_reads}"
        echo "mapped_reads=\${mapped_reads}"
    } > bam_mapped_check.log

    if [[ "\${mapped_reads}" -eq 0 ]]; then
        echo "ERROR: BAM has zero mapped reads." >&2
        echo "Provide --reference_fasta so the workflow can align BAM input before modkit/wf-clone-validation." >&2
        exit 1
    fi
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 5: Stage reference FASTA (+ .fai) for IGV consumption
// ═══════════════════════════════════════════════════════════════════════════════
process PrepareReferenceForIGV {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/align", mode: 'copy'
    tag "reference_prepare"

    input:
    path reference

    output:
    path "reference.fasta", emit: reference_copy
    path "reference.fasta.fai", emit: reference_index
    path "reference_prepare.log", emit: log

    script:
    """
    set -euo pipefail
    cp ${reference} reference.fasta
    samtools faidx reference.fasta > /dev/null 2>&1
    echo "Prepared reference.fasta and reference.fasta.fai for IGV" > reference_prepare.log
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 6: Generate per-site methylation BED (modkit pileup)
// ═══════════════════════════════════════════════════════════════════════════════
process ModkitPileup {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/methylation", mode: 'copy'
    tag "pileup"

    input:
    tuple path(bam), path(bai)
    path reference

    output:
    path "methylation.bed", emit: bed
    path "pileup.log", emit: log

    script:
    def filterThreshold = (params.modkit_filter_threshold != null && params.modkit_filter_threshold.toString().trim() != '')
        ? "--filter-threshold ${params.modkit_filter_threshold}"
        : ''
    """
    modkit pileup \\
        ${bam} \\
        methylation.bed \\
        --ref ${reference} \\
        ${filterThreshold} \\
        --threads ${task.cpus} \\
        2>&1 | tee pileup.log
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 7: Per-read methylation summary statistics
// ═══════════════════════════════════════════════════════════════════════════════
process ModkitSummary {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/methylation", mode: 'copy'
    tag "summary"

    input:
    tuple path(bam), path(bai)

    output:
    path "modkit_summary.tsv", emit: summary
    path "summary.log", emit: log

    script:
    """
    modkit summary \\
        ${bam} \\
        --tsv \\
        > modkit_summary.tsv \\
        2>summary.log
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 8: Align pre-basecalled FASTQ reads to a reference (minimap2)
// ═══════════════════════════════════════════════════════════════════════════════
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
    """
    set -euo pipefail

    # Align FASTQ reads to reference with minimap2 (preset configurable via params).
    MM2_ARGS=(-a -x "${minimapPreset}" -t ${task.cpus})
    if [[ "${allowSecondary}" != "true" ]]; then
        MM2_ARGS+=(--secondary=no)
    fi

    minimap2 "\${MM2_ARGS[@]}" \\
        "${reference}" "${fastq}" 2>fastq_align.log \\
        | samtools sort -@ ${task.cpus} -o aligned.bam

    samtools index aligned.bam
    cp "${reference}" reference.fasta
    samtools faidx reference.fasta
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 9: FASTQ plasmid QC (alignment-derived summary + consensus)
// ═══════════════════════════════════════════════════════════════════════════════
process FastqPlasmidQC {
    label 'local_cpu'
    publishDir "${params.out_dir}/fastq_qc", mode: 'copy'
    tag "fastq_qc"

    input:
    tuple path(bam), path(bai)
    path reference
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
    def manifestJobId = params.job_id ?: 'nanopore-fastq-qc'
    """
    set -euo pipefail

    if command -v samtools >/dev/null 2>&1; then
        SAMTOOLS_CMD=(samtools)
    elif command -v apptainer >/dev/null 2>&1 && [[ -f "${params.container_dir}/dorado.sif" ]]; then
        SAMTOOLS_CMD=(apptainer exec "${params.container_dir}/dorado.sif" samtools)
    else
        echo "samtools not found on host and no fallback dorado container available" >&2
        exit 127
    fi

    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD=(python3)
    elif command -v python >/dev/null 2>&1; then
        PYTHON_CMD=(python)
    else
        echo "python interpreter not found (tried python3 and python)" >&2
        exit 127
    fi

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

    total_reads=\$(awk 'NR > 1 {c++} END {print c + 0}' read_lengths.tsv)
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
            { cumulative += \$1; if (cumulative >= threshold) { print \$1; found = 1; exit } }
            END { if (!found) print 0 }
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

    cp "${reference}" reference_qc.fasta
    "\${SAMTOOLS_CMD[@]}" faidx reference_qc.fasta
    reference_name=\$(head -n1 reference_qc.fasta.fai | cut -f1)
    reference_length=\$(head -n1 reference_qc.fasta.fai | cut -f2)

    mapped_reads=\$("\${SAMTOOLS_CMD[@]}" view -c -F 4 "${bam}")
    unmapped_reads=\$("\${SAMTOOLS_CMD[@]}" view -c -f 4 "${bam}")
    primary_mapped_reads=\$("\${SAMTOOLS_CMD[@]}" view -c -F 2308 "${bam}")
    secondary_alignments=\$("\${SAMTOOLS_CMD[@]}" view -c -f 256 "${bam}")
    supplementary_alignments=\$("\${SAMTOOLS_CMD[@]}" view -c -f 2048 "${bam}")
    total_alignment_records=\$((mapped_reads + unmapped_reads))
    mapping_rate_pct=\$(awk -v mapped="\${mapped_reads}" -v total="\${total_alignment_records}" 'BEGIN {
        if (total > 0) printf "%.4f", (100.0 * mapped) / total
        else printf "0"
    }')
    primary_mapping_rate_pct=\$(awk -v mapped="\${primary_mapped_reads}" -v total="\${total_alignment_records}" 'BEGIN {
        if (total > 0) printf "%.4f", (100.0 * mapped) / total
        else printf "0"
    }')

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

    consensus_status="not_run"
    if "\${SAMTOOLS_CMD[@]}" consensus -f fasta "${bam}" > fastq_consensus.fasta 2> fastq_consensus.log; then
        consensus_status="ok"
    else
        echo "samtools consensus unavailable or failed; attempting mpileup-majority fallback" >> fastq_consensus.log
        if [[ -f "${codeRoot}/scripts/mpileup_majority_consensus.awk" ]] && \
           "\${SAMTOOLS_CMD[@]}" mpileup -aa -A -d 1000000 -f reference_qc.fasta "${bam}" 2>> fastq_consensus.log | \
           awk -f "${codeRoot}/scripts/mpileup_majority_consensus.awk" > fastq_consensus.fasta.tmp && \
           [[ -s fastq_consensus.fasta.tmp ]]; then
            mv fastq_consensus.fasta.tmp fastq_consensus.fasta
            consensus_status="pileup_majority_fallback"
        else
            rm -f fastq_consensus.fasta.tmp
            cp reference_qc.fasta fastq_consensus.fasta
            consensus_status="reference_copy_fallback"
        fi
    fi
    "\${SAMTOOLS_CMD[@]}" faidx fastq_consensus.fasta

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
        cp "${bam}" aligned.bam
        bam_local="aligned.bam"
    fi
    if [[ "\${bai_local}" != "aligned.bam.bai" ]]; then
        cp "${bai}" aligned.bam.bai
        bai_local="aligned.bam.bai"
    fi

    cat > igv_track_config.json <<JSON
[
  {
    "name": "Aligned Reads",
    "type": "alignment",
    "format": "bam",
    "url": "\${bam_local}",
    "indexURL": "\${bai_local}",
    "showCoverage": true,
    "showSoftClips": true,
    "showMismatches": true,
    "showAllBases": true,
    "showInsertionText": true,
    "displayMode": "EXPANDED",
    "visibilityWindow": -1
  },
  {
    "name": "Coverage Depth",
    "type": "wig",
    "format": "bedgraph",
    "url": "igv_coverage_depth.bedgraph",
    "graphType": "bar",
    "autoscale": true,
    "color": "#4ea6ff"
  },
  {
    "name": "Position Gradient",
    "type": "wig",
    "format": "bedgraph",
    "url": "igv_position_gradient.bedgraph",
    "graphType": "heatmap",
    "min": 0,
    "max": 1,
    "autoscale": false
  },
  {
    "name": "GC Content (%)",
    "type": "wig",
    "format": "bedgraph",
    "url": "igv_gc_content.bedgraph",
    "graphType": "line",
    "autoscale": true,
    "color": "#2ec27e"
  },
  {
    "name": "GC Z-score",
    "type": "wig",
    "format": "bedgraph",
    "url": "igv_gc_zscore.bedgraph",
    "graphType": "line",
    "autoscale": true,
    "color": "#f6d32d"
  },
  {
    "name": "Split-read Density",
    "type": "wig",
    "format": "bedgraph",
    "url": "igv_split_read_density.bedgraph",
    "graphType": "bar",
    "autoscale": true,
    "color": "#ff7800"
  },
  {
    "name": "Soft-clip Density",
    "type": "wig",
    "format": "bedgraph",
    "url": "igv_softclip_density.bedgraph",
    "graphType": "bar",
    "autoscale": true,
    "color": "#e01b24"
  },
  {
    "name": "Junction Hotspots",
    "type": "annotation",
    "format": "bed",
    "url": "igv_junction_hotspots.bed",
    "displayMode": "EXPANDED",
    "color": "#ffbe6f"
  }
]
JSON

    : > igv_report.log

    if command -v create_report >/dev/null 2>&1; then
        igv_report_cli_available=1
        if create_report igv_report_sites.bed \\
            --fasta reference_qc.fasta \\
            --track-config igv_track_config.json \\
            --flanking ${igvReportFlankingBp} \\
            --title "FASTQ Plasmid QC IGV Report" \\
            --output igv_report.html >> igv_report.log 2>&1; then
            igv_report_status="created"
        else
            igv_report_status="fallback_cli_error"
            {
                echo "<!doctype html>"
                echo "<html><head><meta charset=\\"utf-8\\"><title>IGV Report Fallback</title></head><body>"
                echo "<h1>IGV report fallback</h1>"
                echo "<p><strong>create_report</strong> was found but failed for this run. See <code>igv_report.log</code>.</p>"
                echo "<p>Generated tracks remain available in this directory.</p>"
                echo "</body></html>"
            } > igv_report.html
        fi
    else
        igv_report_status="fallback_missing_cli"
        {
            echo "<!doctype html>"
            echo "<html><head><meta charset=\\"utf-8\\"><title>IGV Report Fallback</title></head><body>"
            echo "<h1>IGV report fallback</h1>"
            echo "<p><strong>create_report</strong> (igv-reports) is not installed in this runtime.</p>"
            echo "<p>Install igv-reports to generate fully interactive static report HTML from these artifacts.</p>"
            echo "</body></html>"
        } > igv_report.html
    fi

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
        echo -e "total_bases\\t\${total_bases}"
        echo -e "mean_read_length_bp\\t\${mean_read_length}"
        echo -e "median_read_length_bp\\t\${median_read_length}"
        echo -e "n50_read_length_bp\\t\${n50_read_length}"
        echo -e "estimated_copy_number_mean\\t\${estimated_copy_number_mean}"
        echo -e "dimer_like_reads\\t\${dimer_like_reads}"
        echo -e "trimer_plus_reads\\t\${trimer_plus_reads}"
        echo -e "mapped_reads\\t\${mapped_reads}"
        echo -e "unmapped_reads\\t\${unmapped_reads}"
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

    "\${PYTHON_CMD[@]}" "${codeRoot}/scripts/build_sequence_qc_manifest.py" \\
        --out qc_manifest.json \\
        --job-id "${manifestJobId}" \\
        --sample-name "fastq_plasmid_qc" \\
        --reference-fasta reference_qc.fasta \\
        --reference-index reference_qc.fasta.fai \\
        --summary fastq_qc_summary.tsv \\
        --alignment-stats fastq_alignment_stats.tsv \\
        --coverage fastq_coverage.tsv \\
        --per-base-support per_base_support.tsv \\
        --consensus fastq_consensus.fasta \\
        --consensus-index fastq_consensus.fasta.fai \\
        --consensus-status "\${consensus_status}" \\
        --alignment-bam "\${bam_local}" \\
        --alignment-bai "\${bai_local}" \\
        --igv-track-config igv_track_config.json \\
        --igv-report igv_report.html \\
        --igv-report-log igv_report.log \\
        --log fastq_qc.log
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 10: FASTQ read-length based multimerization QC (legacy)
// ═══════════════════════════════════════════════════════════════════════════════
process FastqMultimerQC {
    label 'local_cpu'
    publishDir "${params.out_dir}/multimer_qc", mode: 'copy'
    tag "multimer_qc"

    input:
    path fastq

    output:
    path "read_lengths.tsv", emit: lengths
    path "multimer_summary.tsv", emit: summary
    path "multimer_candidates.tsv", emit: candidates
    path "multimer_qc.log", emit: log

    script:
    def expectedSize = (params.expected_plasmid_size ?: 7000) as Integer
    def minReadLength = (params.min_fastq_read_length ?: 0) as Integer
    """
    set -euo pipefail

    touch multimer_candidates.tsv

    if [[ "${fastq}" == *.gz ]]; then
        zcat "${fastq}" | awk 'NR % 4 == 2 { print length(\$0) }' > read_lengths.tsv
    else
        cat "${fastq}" | awk 'NR % 4 == 2 { print length(\$0) }' > read_lengths.tsv
    fi

    awk -v expected=${expectedSize} -v minlen=${minReadLength} '
        BEGIN {
            total = 0; mono = 0; dimer = 0; trimer = 0; tetramer_plus = 0; sum = 0;
            # Midpoints between integer multiples of plasmid size
            dimer_cutoff   = expected * 1.5;   # boundary between 1x and 2x
            trimer_cutoff  = expected * 2.5;   # boundary between 2x and 3x
            tetramer_cutoff = expected * 3.5;  # boundary between 3x and 4x+
        }
        {
            len = \$1;
            if (len < minlen) next;
            total++;
            sum += len;
            if (len >= tetramer_cutoff) {
                tetramer_plus++;
                print NR "\\t" len "\\ttetramer_plus" >> "multimer_candidates.tsv";
            } else if (len >= trimer_cutoff) {
                trimer++;
                print NR "\\t" len "\\ttrimer_candidate" >> "multimer_candidates.tsv";
            } else if (len >= dimer_cutoff) {
                dimer++;
                print NR "\\t" len "\\tdimer_candidate" >> "multimer_candidates.tsv";
            } else {
                mono++;
            }
        }
        END {
            mean = (total > 0) ? (sum / total) : 0;
            print "metric\\tvalue" > "multimer_summary.tsv";
            print "total_reads\\t" total >> "multimer_summary.tsv";
            print "monomer_reads\\t" mono >> "multimer_summary.tsv";
            print "dimer_reads\\t" dimer >> "multimer_summary.tsv";
            print "trimer_reads\\t" trimer >> "multimer_summary.tsv";
            print "tetramer_plus_reads\\t" tetramer_plus >> "multimer_summary.tsv";
            print "expected_plasmid_size\\t" expected >> "multimer_summary.tsv";
            print "dimer_cutoff\\t" dimer_cutoff >> "multimer_summary.tsv";
            print "trimer_cutoff\\t" trimer_cutoff >> "multimer_summary.tsv";
            print "tetramer_cutoff\\t" tetramer_cutoff >> "multimer_summary.tsv";
            print "min_read_length\\t" minlen >> "multimer_summary.tsv";
            print "mean_read_length\\t" mean >> "multimer_summary.tsv";
        }
    ' read_lengths.tsv

    {
        echo "FASTQ multimer QC complete"
        echo "Expected plasmid size: ${expectedSize}"
        echo "Dimer cutoff (1.5x): \$(echo "${expectedSize} * 1.5" | bc)"
        echo "Trimer cutoff (2.5x): \$(echo "${expectedSize} * 2.5" | bc)"
        echo "Tetramer cutoff (3.5x): \$(echo "${expectedSize} * 3.5" | bc)"
        echo "Minimum read length: ${minReadLength}"
    } > multimer_qc.log
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 10: Dimer-focused FASTQ analysis (extract -> align -> consensus/junction)
// ═══════════════════════════════════════════════════════════════════════════════
process FastqDimerAnalysis {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/multimer_qc", mode: 'copy', saveAs: { filename ->
        def mode = (params.dimer_output_mode ?: 'core').toString().trim().toLowerCase()
        def emitLegacy = params.dimer_emit_legacy_outputs == true
        def legacyArtifacts = [
            'dimer_consensus.fasta',
            'dimer_consensus.log',
            'dimer_junction_profile.tsv',
            'dimer_read_junctions.tsv',
            'dimer_junction_events.tsv',
            'dimer_junction_clusters.tsv',
            'dimer_junction_hotspots.tsv',
            'dimer_junction_rotated_profile.tsv',
            'dimer_junction_rotation_summary.tsv',
            'dimer_breakpoint_screen.tsv',
            'dimer_breakpoint_start_counts.tsv',
            'dimer_read_ledger.tsv',
            'dimer_breakpoint_reads.tsv',
            'dimer_rotated_remap_summary.tsv',
            'dimer_rotated_remap_breakpoints.tsv',
            'dimer_single_ref_split_events.tsv',
            'dimer_single_ref_split_profile.tsv',
            'dimer_candidates.single_ref.aligned.bam',
            'dimer_candidates.single_ref.aligned.bam.bai',
            'dimer_single_ref_alignment.log',
            'dimer_alignment.log',
        ] as Set
        if (mode == 'debug' || emitLegacy || !legacyArtifacts.contains(filename.toString())) {
            return filename
        }
        return null
    }
    tag "dimer_analysis"

    input:
    path fastq
    path reference

    output:
    path "dimer_candidates.fastq", emit: dimer_fastq
    path "dimer_candidates.fasta", emit: dimer_fasta
    path "dimer_read_lengths.tsv", emit: dimer_lengths
    path "dimer_reference.fasta", emit: dimer_reference
    path "dimer_reference.fasta.fai", emit: dimer_reference_index
    path "dimer_analysis_summary.tsv", emit: summary
    path "dimer_analysis.log", emit: log
    path "dimer_candidates.aligned.bam", emit: dimer_bam, optional: true
    path "dimer_candidates.aligned.bam.bai", emit: dimer_bai, optional: true
    path "dimer_consensus.fasta", emit: consensus, optional: true
    path "dimer_consensus.log", emit: consensus_log, optional: true
    path "dominant_dimer_consensus.fasta", emit: dominant_consensus, optional: true
    path "dominant_dimer_consensus.log", emit: dominant_consensus_log, optional: true
    path "dominant_dimer_consensus_metadata.tsv", emit: dominant_consensus_metadata, optional: true
    path "dimer_junction_profile.tsv", emit: junction_profile, optional: true
    path "dimer_read_junctions.tsv", emit: junction_reads, optional: true
    path "dimer_junction_events.tsv", emit: junction_events, optional: true
    path "dimer_junction_clusters.tsv", emit: junction_clusters, optional: true
    path "dimer_junction_hotspots.tsv", emit: junction_hotspots, optional: true
    path "dimer_junction_rotated_profile.tsv", emit: junction_rotated_profile, optional: true
    path "dimer_junction_rotation_summary.tsv", emit: junction_rotation_summary, optional: true
    path "dimer_breakpoint_screen.tsv", emit: breakpoint_screen, optional: true
    path "dimer_breakpoint_start_counts.tsv", emit: breakpoint_start_counts, optional: true
    path "dimer_read_ledger.tsv", emit: read_ledger, optional: true
    path "dimer_breakpoint_reads.tsv", emit: breakpoint_reads, optional: true
    path "dimer_rotated_remap_summary.tsv", emit: rotated_remap_summary, optional: true
    path "dimer_rotated_remap_breakpoints.tsv", emit: rotated_remap_breakpoints, optional: true
    path "dimer_single_ref_split_events.tsv", emit: single_ref_split_events, optional: true
    path "dimer_single_ref_split_profile.tsv", emit: single_ref_split_profile, optional: true
    path "dimer_candidates.single_ref.aligned.bam", emit: single_ref_bam, optional: true
    path "dimer_candidates.single_ref.aligned.bam.bai", emit: single_ref_bai, optional: true
    path "dimer_single_ref_alignment.log", emit: single_ref_align_log, optional: true
    path "dimer_alignment.log", emit: align_log, optional: true

    script:
    def expectedSize = (params.expected_plasmid_size ?: 7000) as Integer
    def minReadLength = (params.min_fastq_read_length ?: 0) as Integer
    def enableRotation = params.enable_rotating_reference_frames == false ? 'false' : 'true'
    def rotationScanStep = (params.rotation_scan_step_bp ?: 1) as Integer
    def singleRefMinMapq = (params.single_ref_split_min_mapq ?: 20) as Integer
    def singleRefMinSegBp = (params.single_ref_split_min_segment_bp ?: 250) as Integer
    def singleRefMaxGapBp = (params.single_ref_split_max_query_gap_bp ?: 500) as Integer
    def minimapPreset = ((params.fastq_minimap2_preset ?: 'map-ont') as String).trim()
    def minimapAllowSecondary = (params.fastq_minimap2_allow_secondary == true) ? 'true' : 'false'
    def codeRoot = params.code_root ?: projectDir
    """
    set -euo pipefail

    MM2_ARGS=(-a -x "${minimapPreset}" -t ${task.cpus})
    if [[ "${minimapAllowSecondary}" != "true" ]]; then
        MM2_ARGS+=(--secondary=no)
    fi

    : > dimer_candidates.fastq
    : > dimer_candidates.fasta
    : > dimer_read_lengths.tsv
    : > dimer_read_junctions.tsv
    : > dimer_alignment.log
    : > dimer_single_ref_alignment.log
    : > dimer_consensus.log
    : > dominant_dimer_consensus.log
    printf "position_mod_ref\\tread_count\\tjunction_spanning_reads\\n" > dimer_junction_profile.tsv
    printf "read_id\\tstart\\tend\\tposition_mod_ref\\tcrosses_junction\\tevent_type\\tmethod\\tsegment_count\\tleft_ref\\tright_ref\\tleft_mod_ref\\tright_mod_ref\\tmissing_bp\\tmissing_left_bp\\tmissing_right_bp\\tsupport_bp\\torientation\\tcopy_transition\\n" > dimer_junction_events.tsv
    printf "position_mod_ref\\tread_count\\tcrossing_reads\\tsupport_percent\\tsupport_reads\\tsplit_reads\\tseam_reads\\tsingle_reads\\tmissing_bp_sum\\tmissing_bp_mean\\tmissing_bp_max\\tsupport_bp_sum\\tsupport_bp_mean\\n" > dimer_junction_clusters.tsv
    printf "position_mod_ref\\tsupport_reads\\tsupport_pct\\tin_boundary_window\\n" > dimer_junction_hotspots.tsv
    printf "position_rotated\\tposition_mod_ref\\tsupport_reads\\tsupport_pct\\tin_boundary_window\\n" > dimer_junction_rotated_profile.tsv
    printf "position_mod_ref\\ttotal_support_reads\\tseam_support_reads\\tsplit_support_reads\\tsupport_pct_all\\tsplit_pct_of_position\\tsplit_pct_of_all_split\\tin_boundary_window\\tboundary_start_reads\\tboundary_start_fraction\\tseam_fraction\\tsplit_to_seam_ratio\\tartifact_flag\\tconfidence\\n" > dimer_breakpoint_screen.tsv
    printf "position_mod_ref\\tboundary_start_reads\\tposition_event_reads\\tboundary_seam_or_single_start_reads\\n" > dimer_breakpoint_start_counts.tsv
    printf "read_id\\tlen_bp\\taligned\\tevent_type\\tmethod\\tcrosses\\tpos_mod\\tstart\\tend\\tleft_mod\\tright_mod\\tmissing_bp\\tmissing_left_bp\\tmissing_right_bp\\tsupport_bp\\torientation\\tcopy_transition\\n" > dimer_read_ledger.tsv
    printf "read_id\\tlen_bp\\tevent_type\\tmethod\\tpos_mod\\tleft_mod\\tright_mod\\tmissing_bp\\tsupport_bp\\tstart\\tend\\torientation\\tcopy_transition\\n" > dimer_breakpoint_reads.tsv
    printf "offset_bp\\tmode\\taligned_reads\\ttotal_support\\tboundary_support\\tboundary_pct\\tsplit_support\\tseam_support\\tsingle_support\\tdom_rot\\tdom_mod\\tdom_reads\\tdom_pct\\tseam_only\\n" > dimer_rotated_remap_summary.tsv
    printf "offset_bp\\tpos_mod\\tsupport\\tsplit\\tseam\\tpos_rot\\tin_boundary\\n" > dimer_rotated_remap_breakpoints.tsv
    printf "read_id\\tsegment_count\\tleft_ref\\tright_ref\\tposition_mod_ref\\tquery_gap_bp\\tsupport_bp\\torientation_pair\\tmethod\\n" > dimer_single_ref_split_events.tsv
    printf "position_mod_ref\\tsplit_support_reads\\tsupport_bp_sum\\tsupport_bp_mean\\tsupport_pct\\n" > dimer_single_ref_split_profile.tsv

    dimer_cutoff=\$(awk -v expected=${expectedSize} 'BEGIN { printf "%d\\n", int(expected * 1.5 + 0.5) }')
    trimer_cutoff=\$(awk -v expected=${expectedSize} 'BEGIN { printf "%d\\n", int(expected * 2.5 + 0.5) }')

    # Stage reference to deterministic local filename to avoid escaping issues
    # when upstream path includes spaces.
    cp ${reference} reference_input.fasta
    samtools faidx reference_input.fasta
    ref_name=\$(head -n1 reference_input.fasta.fai | cut -f1)
    ref_len=\$(head -n1 reference_input.fasta.fai | cut -f2)
    samtools faidx reference_input.fasta "\${ref_name}" > ref_single.fasta
    ref_seq=\$(tail -n +2 ref_single.fasta | tr -d '\\n')
    printf ">%s_dimer\\n%s%s\\n" "\${ref_name}" "\${ref_seq}" "\${ref_seq}" > dimer_reference.fasta
    samtools faidx dimer_reference.fasta
    if [[ ! -f "${codeRoot}/scripts/dimer_single_ref_split_events.awk" ]]; then
        echo "Missing parser script: ${codeRoot}/scripts/dimer_single_ref_split_events.awk" >&2
        exit 1
    fi
    cp "${codeRoot}/scripts/dimer_single_ref_split_events.awk" dimer_single_ref_split_events.awk

    if [[ "${fastq}" == *.gz ]]; then
        reader="zcat"
    else
        reader="cat"
    fi

    \${reader} "${fastq}" | awk -v minlen=${minReadLength} -v dimer="\${dimer_cutoff}" -v trimer="\${trimer_cutoff}" '
        NR % 4 == 1 { hdr = \$0 }
        NR % 4 == 2 { seq = \$0; len = length(\$0) }
        NR % 4 == 3 { plus = \$0 }
        NR % 4 == 0 {
            qual = \$0
            if (len < minlen) next
            if (len >= dimer && len < trimer) {
                print hdr >> "dimer_candidates.fastq"
                print seq >> "dimer_candidates.fastq"
                print plus >> "dimer_candidates.fastq"
                print qual >> "dimer_candidates.fastq"

                id = hdr
                sub(/^@/, "", id)
                split(id, parts, /[ \\t]/)
                read_id = parts[1]

                print read_id "\\t" len >> "dimer_read_lengths.tsv"
                print ">" read_id >> "dimer_candidates.fasta"
                print seq >> "dimer_candidates.fasta"
                count++
            }
        }
        END {
            print (count + 0) > "dimer_candidate_count.txt"
        }
    '

    dimer_count=\$(cat dimer_candidate_count.txt)
    aligned_reads=0
    junction_spanning_reads=0
    split_event_reads=0
    seam_event_reads=0
    single_event_reads=0
    dominant_junction_pos="NA"
    dominant_junction_support=0
    dominant_junction_support_pct=0
    dominant_nonboundary_junction_pos="NA"
    dominant_nonboundary_junction_support=0
    dominant_nonboundary_junction_support_pct=0
    dominant_split_junction_pos="NA"
    dominant_split_junction_support=0
    dominant_split_junction_support_pct=0
    dominant_split_junction_support_pct_of_split=0
    seam_support_fraction_pct=0
    split_support_fraction_pct=0
    boundary_dominant_artifact_flag=0
    screened_primary_breakpoint_position_mod_ref="NA"
    screened_primary_breakpoint_support_reads=0
    screened_primary_breakpoint_confidence="insufficient"
    screened_primary_breakpoint_boundary_start_fraction=0
    screened_primary_breakpoint_seam_fraction=0
    screened_primary_breakpoint_split_to_seam_ratio=0
    boundary_window_bp=0
    boundary_window_support=0
    boundary_window_support_pct=0
    total_junction_support=0
    event_split_support=0
    total_split_support=0
    single_ref_split_reads=0
    single_ref_split_support=0
    single_ref_dominant_split_pos="NA"
    single_ref_dominant_split_support=0
    single_ref_dominant_split_support_pct=0
    informative_breakpoint_count=0
    artifact_breakpoint_count=0
    seam_only_unresolved_flag=0
    breakpoint_model_status="not_evaluable"
    rotation_enabled="${enableRotation}"
    rotation_scan_step_requested=${rotationScanStep}
    rotation_scan_step_effective=1
    rotation_selected_offset_bp=0
    rotation_selected_boundary_support_reads=0
    rotation_selected_boundary_support_pct=0
    rotation_dominant_hotspot_position_rotated="NA"
    rotation_dominant_hotspot_position_mod_ref="NA"
    rotation_dominant_hotspot_support_reads=0
    rotation_dominant_hotspot_support_pct=0
    rotation_offsets_tested=1
    rotation_offsets_mode="disabled"
    consensus_status="not_run"
    dominant_consensus_status="not_run"
    dominant_consensus_breakpoint_pos="NA"
    dominant_consensus_breakpoint_source="none"
    dominant_consensus_support_reads=0
    dominant_consensus_support_pct=0
    dominant_consensus_read_id="NA"
    dominant_consensus_read_copies=0
    dominant_consensus_read_support_pct=0

    if [[ "\${dimer_count}" -gt 0 ]]; then
        minimap2 "\${MM2_ARGS[@]}" \\
            dimer_reference.fasta dimer_candidates.fastq \\
            2> dimer_alignment.log \\
            | samtools sort -@ ${task.cpus} -o dimer_candidates.aligned.bam
        samtools index dimer_candidates.aligned.bam

        samtools view -F 260 dimer_candidates.aligned.bam \\
            | LC_ALL=C sort -k1,1 -k4,4n \\
            | awk -v ref_len="\${ref_len}" '
                BEGIN {
                    OFS = "\\t"
                    print "read_id", "start", "end", "position_mod_ref", "crosses_junction", "event_type", "method", "segment_count", "left_ref", "right_ref", "left_mod_ref", "right_mod_ref", "missing_bp", "missing_left_bp", "missing_right_bp", "support_bp", "orientation", "copy_transition"
                }
                function ref_span(cigar,   i, c, n, span) {
                    n = ""; span = 0
                    for (i = 1; i <= length(cigar); i++) {
                        c = substr(cigar, i, 1)
                        if (c ~ /[0-9]/) {
                            n = n c
                            continue
                        }
                        if (n == "") continue
                        if (c ~ /[MDN=X]/) span += (n + 0)
                        n = ""
                    }
                    return span
                }
                function to_mod(pos) {
                    if (ref_len <= 0) return 0
                    return ((pos - 1) % ref_len) + 1
                }
                function circular_missing(left_mod, right_mod, gap) {
                    if (left_mod <= 0 || right_mod <= 0) return 0
                    gap = right_mod - left_mod - 1
                    while (gap < 0) gap += ref_len
                    while (gap >= ref_len) gap -= ref_len
                    return gap
                }
                function flush_read(   i, k, primary_idx, primary_start, primary_end, cross_idx, cross_support, left_idx, right_idx, left_ref, right_ref, left_mod, right_mod, missing_bp, missing_left_bp, missing_right_bp, support_bp, crosses, event_type, method, flank_l, flank_r, pos_mod, span_bp, orientation, copy_transition) {
                    if (curr_read == "") return
                    primary_idx = 1
                    for (i = 1; i <= seg_count; i++) {
                        if (seg_is_primary[i]) {
                            primary_idx = i
                            break
                        }
                    }
                    primary_start = seg_start[primary_idx]
                    primary_end = seg_end[primary_idx]

                    cross_idx = 0
                    cross_support = -1
                    for (i = 1; i <= seg_count; i++) {
                        if (seg_start[i] <= ref_len && seg_end[i] > ref_len) {
                            flank_l = ref_len - seg_start[i] + 1
                            flank_r = seg_end[i] - ref_len
                            if (flank_l < 0) flank_l = 0
                            if (flank_r < 0) flank_r = 0
                            support_bp = flank_l < flank_r ? flank_l : flank_r
                            if (support_bp > cross_support) {
                                cross_support = support_bp
                                cross_idx = i
                            }
                        }
                    }

                    left_idx = 0
                    right_idx = 0
                    for (i = 1; i <= seg_count; i++) {
                        if (seg_end[i] <= ref_len) {
                            if (left_idx == 0 || seg_end[i] > seg_end[left_idx]) left_idx = i
                        }
                        if (seg_start[i] > ref_len) {
                            if (right_idx == 0 || seg_start[i] < seg_start[right_idx]) right_idx = i
                        }
                    }

                    support_bp = 0
                    crosses = 0
                    if (cross_idx > 0) {
                        event_type = "seam"
                        method = "seam_cross_segment"
                        left_ref = seg_end[cross_idx]
                        right_ref = seg_start[cross_idx]
                        flank_l = ref_len - seg_start[cross_idx] + 1
                        flank_r = seg_end[cross_idx] - ref_len
                        if (flank_l < 0) flank_l = 0
                        if (flank_r < 0) flank_r = 0
                        support_bp = flank_l < flank_r ? flank_l : flank_r
                        crosses = 1
                        orientation = seg_strand[cross_idx]
                        copy_transition = "1->2"
                    } else if (left_idx > 0 && right_idx > 0) {
                        event_type = "split"
                        method = "split_primary_supplementary"
                        left_ref = seg_end[left_idx]
                        right_ref = seg_start[right_idx]
                        span_bp = seg_end[left_idx] - seg_start[left_idx] + 1
                        flank_l = seg_end[right_idx] - seg_start[right_idx] + 1
                        support_bp = span_bp < flank_l ? span_bp : flank_l
                        if (support_bp < 0) support_bp = 0
                        crosses = 1
                        orientation = seg_strand[left_idx] "/" seg_strand[right_idx]
                        copy_transition = seg_copy_end[left_idx] "->" seg_copy_start[right_idx]
                    } else {
                        event_type = "single"
                        method = (seg_count > 1) ? "single_disjoint_segments" : "single_primary_segment"
                        left_ref = primary_end
                        right_ref = primary_start
                        if (right_ref > (2 * ref_len)) right_ref = 2 * ref_len
                        orientation = seg_strand[primary_idx]
                        copy_transition = seg_copy_start[primary_idx] "->" seg_copy_end[primary_idx]
                    }

                    left_mod = to_mod(left_ref)
                    right_mod = to_mod(right_ref)
                    missing_bp = circular_missing(left_mod, right_mod)
                    missing_left_bp = ref_len - left_mod
                    if (missing_left_bp == ref_len) missing_left_bp = 0
                    missing_right_bp = right_mod - 1
                    if (missing_right_bp < 0) missing_right_bp = 0
                    pos_mod = right_mod

                    print curr_read, primary_start, primary_end, pos_mod, crosses, event_type, method, seg_count, left_ref, right_ref, left_mod, right_mod, missing_bp, missing_left_bp, missing_right_bp, support_bp, orientation, copy_transition

                    for (k in seg_start) delete seg_start[k]
                    for (k in seg_end) delete seg_end[k]
                    for (k in seg_is_primary) delete seg_is_primary[k]
                    for (k in seg_strand) delete seg_strand[k]
                    for (k in seg_copy_start) delete seg_copy_start[k]
                    for (k in seg_copy_end) delete seg_copy_end[k]
                }
                {
                    if (\$1 != curr_read) {
                        flush_read()
                        curr_read = \$1
                        seg_count = 0
                    }

                    flag = \$2 + 0
                    cigar = \$6
                    pos = \$4 + 0
                    if (cigar == "*" || pos <= 0) next

                    seg_count++
                    seg_start[seg_count] = pos
                    span_bp = ref_span(cigar)
                    if (span_bp <= 0) span_bp = 1
                    seg_end[seg_count] = pos + span_bp - 1
                    seg_is_primary[seg_count] = (int(flag / 2048) % 2 == 0) ? 1 : 0
                    seg_strand[seg_count] = (int(flag / 16) % 2 == 1) ? "-" : "+"
                    seg_copy_start[seg_count] = (seg_start[seg_count] <= ref_len) ? 1 : 2
                    seg_copy_end[seg_count] = (seg_end[seg_count] <= ref_len) ? 1 : 2
                }
                END {
                    flush_read()
                }
            ' > dimer_junction_events.tsv

        awk -F"\\t" '
            BEGIN {
                OFS = "\\t"
                print "read_id", "len_bp", "aligned", "event_type", "method", "crosses", "pos_mod", "start", "end", "left_mod", "right_mod", "missing_bp", "missing_left_bp", "missing_right_bp", "support_bp", "orientation", "copy_transition"
            }
            NR == FNR {
                if (FNR == 1) next
                event[\$1] = \$0
                next
            }
            {
                read_id = \$1
                read_len = \$2 + 0
                if (read_id in event) {
                    split(event[read_id], row, "\\t")
                    print read_id, read_len, 1, row[6], row[7], row[5], row[4], row[2], row[3], row[11], row[12], row[13], row[14], row[15], row[16], row[17], row[18]
                } else {
                    print read_id, read_len, 0, "unaligned", "none", 0, "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA"
                }
            }
        ' OFS="\\t" dimer_junction_events.tsv dimer_read_lengths.tsv > dimer_read_ledger.tsv

        awk -F"\\t" '
            BEGIN {
                OFS = "\\t"
                print "read_id", "len_bp", "event_type", "method", "pos_mod", "left_mod", "right_mod", "missing_bp", "support_bp", "start", "end", "orientation", "copy_transition"
            }
            NR == 1 { next }
            (\$6 + 0) > 0 {
                print \$1, \$2, \$4, \$5, \$7, \$10, \$11, \$12, \$15, \$8, \$9, \$16, \$17
            }
        ' OFS="\\t" dimer_read_ledger.tsv > dimer_breakpoint_reads.tsv

        awk -F"\\t" '
            NR > 1 {
                print \$1, \$2, \$3, \$4, \$5, \$7, \$17, \$14, \$15, \$6, \$8, \$11, \$12, \$16, \$18
            }
        ' OFS="\\t" dimer_junction_events.tsv > dimer_read_junctions.tsv

        awk -F"\\t" '
            BEGIN {
                OFS = "\\t"
                print "position_mod_ref", "read_count", "junction_spanning_reads"
            }
            {
                pos = \$4 + 0
                if (pos <= 0) next
                count[pos]++
                if ((\$5 + 0) > 0) span[pos]++
            }
            END {
                for (pos in count) {
                    print pos, count[pos], (span[pos] + 0)
                }
            }
        ' dimer_read_junctions.tsv > dimer_junction_profile.unsorted.tsv
        {
            head -n1 dimer_junction_profile.unsorted.tsv
            tail -n +2 dimer_junction_profile.unsorted.tsv | LC_ALL=C sort -k1,1n
        } > dimer_junction_profile.tsv
        rm -f dimer_junction_profile.unsorted.tsv

        awk -F"\\t" '
            BEGIN {
                OFS = "\\t"
                print "position_mod_ref", "read_count", "crossing_reads", "support_percent", "support_reads", "split_reads", "seam_reads", "single_reads", "missing_bp_sum", "missing_bp_mean", "missing_bp_max", "support_bp_sum", "support_bp_mean"
            }
            NR == 1 { next }
            {
                pos = \$4 + 0
                if (pos <= 0) next
                reads[pos]++
                if ((\$5 + 0) > 0) crossing[pos]++
                if ((\$16 + 0) > 0) support[pos]++
                if (\$6 == "split") {
                    split_count[pos]++
                } else if (\$6 == "seam") {
                    seam_count[pos]++
                } else {
                    single_count[pos]++
                }
                missing = \$13 + 0
                missing_sum[pos] += missing
                if (!(pos in missing_max) || missing > missing_max[pos]) missing_max[pos] = missing
                support_bp = \$16 + 0
                support_sum[pos] += support_bp
            }
            END {
                for (pos in reads) {
                    support_pct = (reads[pos] > 0) ? (100.0 * crossing[pos]) / reads[pos] : 0
                    missing_mean = (reads[pos] > 0) ? missing_sum[pos] / reads[pos] : 0
                    support_mean = (reads[pos] > 0) ? support_sum[pos] / reads[pos] : 0
                    print pos, reads[pos], (crossing[pos] + 0), sprintf("%.4f", support_pct), (support[pos] + 0), (split_count[pos] + 0), (seam_count[pos] + 0), (single_count[pos] + 0), (missing_sum[pos] + 0), sprintf("%.4f", missing_mean), (missing_max[pos] + 0), (support_sum[pos] + 0), sprintf("%.4f", support_mean)
                }
            }
        ' dimer_junction_events.tsv > dimer_junction_clusters.unsorted.tsv
        {
            head -n1 dimer_junction_clusters.unsorted.tsv
            tail -n +2 dimer_junction_clusters.unsorted.tsv | LC_ALL=C sort -k1,1n
        } > dimer_junction_clusters.tsv
        rm -f dimer_junction_clusters.unsorted.tsv

        aligned_reads=\$(awk -F"\\t" 'NR > 1 {c++} END {print c + 0}' dimer_junction_events.tsv)
        junction_spanning_reads=\$(awk -F"\\t" 'NR > 1 && (\$5 + 0) > 0 {c++} END {print c + 0}' dimer_junction_events.tsv)
        split_event_reads=\$(awk -F"\\t" 'NR > 1 && \$6 == "split" {c++} END {print c + 0}' dimer_junction_events.tsv)
        seam_event_reads=\$(awk -F"\\t" 'NR > 1 && \$6 == "seam" {c++} END {print c + 0}' dimer_junction_events.tsv)
        single_event_reads=\$(awk -F"\\t" 'NR > 1 && \$6 == "single" {c++} END {print c + 0}' dimer_junction_events.tsv)

        read dominant_junction_pos dominant_junction_support < <(
            awk -F"\\t" '
                NR == 1 { next }
                {
                    pos = \$1 + 0
                    support = \$3 + 0
                    if (support > best || (support == best && support > 0 && (best_pos == "" || pos < best_pos))) {
                        best = support
                        best_pos = pos
                    }
                }
                END {
                    if (best > 0) {
                        print best_pos, best
                    } else {
                        print "NA 0"
                    }
                }
            ' dimer_junction_clusters.tsv
        )
        total_junction_support=\$(awk -F"\\t" 'NR > 1 {s += (\$3 + 0)} END {print s + 0}' dimer_junction_clusters.tsv)
        dominant_junction_support_pct=\$(awk -v support="\${dominant_junction_support}" -v total="\${total_junction_support}" 'BEGIN {
            if (total > 0) {
                printf "%.4f", (100.0 * support) / total
            } else {
                printf "0"
            }
        }')

        read dominant_split_junction_pos dominant_split_junction_support < <(
            awk -F"\\t" '
                NR == 1 { next }
                (\$5 + 0) > 0 && \$6 == "split" {
                    pos = \$4 + 0
                    if (pos <= 0) next
                    count[pos]++
                }
                END {
                    for (pos in count) {
                        if (count[pos] > best || (count[pos] == best && count[pos] > 0 && (best_pos == "" || pos < best_pos))) {
                            best = count[pos]
                            best_pos = pos
                        }
                    }
                    if (best > 0) {
                        print best_pos, best
                    } else {
                        print "NA 0"
                    }
                }
            ' dimer_junction_events.tsv
        )
        event_split_support=\$(awk -F"\\t" 'NR > 1 && (\$5 + 0) > 0 && \$6 == "split" {c++} END {print c + 0}' dimer_junction_events.tsv)
        total_split_support="\${event_split_support}"

        # Secondary pass: remap dimer candidates to single-copy reference and
        # call split transitions between adjacent query segments.
        if minimap2 "\${MM2_ARGS[@]}" \\
            ref_single.fasta dimer_candidates.fastq \\
            2> dimer_single_ref_alignment.log \\
            | samtools sort -@ ${task.cpus} -o dimer_candidates.single_ref.aligned.bam; then
            samtools index dimer_candidates.single_ref.aligned.bam

            samtools view -F 260 dimer_candidates.single_ref.aligned.bam \\
                | awk -v ref_len="\${ref_len}" -v min_mapq=${singleRefMinMapq} -v min_seg_bp=${singleRefMinSegBp} -v max_gap=${singleRefMaxGapBp} \\
                    -f dimer_single_ref_split_events.awk \\
                > dimer_single_ref_split_events.unsorted.tsv

            {
                printf "read_id\\tsegment_count\\tleft_ref\\tright_ref\\tposition_mod_ref\\tquery_gap_bp\\tsupport_bp\\torientation_pair\\tmethod\\n"
                if [[ -s dimer_single_ref_split_events.unsorted.tsv ]]; then
                    LC_ALL=C sort -k5,5n -k7,7nr dimer_single_ref_split_events.unsorted.tsv
                fi
            } > dimer_single_ref_split_events.tsv.tmp
            mv dimer_single_ref_split_events.tsv.tmp dimer_single_ref_split_events.tsv
            rm -f dimer_single_ref_split_events.unsorted.tsv

            awk -F"\\t" '
                BEGIN {
                    OFS = "\\t"
                    print "position_mod_ref", "split_support_reads", "support_bp_sum", "support_bp_mean", "support_pct"
                }
                NR == 1 { next }
                {
                    pos = \$5 + 0
                    support_bp = \$7 + 0
                    if (pos <= 0) next
                    count[pos]++
                    support_sum[pos] += support_bp
                    total++
                }
                END {
                    for (pos in count) {
                        mean_bp = count[pos] > 0 ? support_sum[pos] / count[pos] : 0
                        pct = total > 0 ? (100.0 * count[pos]) / total : 0
                        print pos, count[pos], support_sum[pos], sprintf("%.4f", mean_bp), sprintf("%.4f", pct)
                    }
                }
            ' dimer_single_ref_split_events.tsv > dimer_single_ref_split_profile.unsorted.tsv
            {
                head -n1 dimer_single_ref_split_profile.unsorted.tsv
                tail -n +2 dimer_single_ref_split_profile.unsorted.tsv | LC_ALL=C sort -k1,1n
            } > dimer_single_ref_split_profile.tsv
            rm -f dimer_single_ref_split_profile.unsorted.tsv
        else
            echo "Single-reference remap failed; continuing with dimer-reference evidence only." >> dimer_single_ref_alignment.log
            rm -f dimer_candidates.single_ref.aligned.bam dimer_candidates.single_ref.aligned.bam.bai
        fi

        single_ref_split_reads=\$(awk -F"\\t" 'NR > 1 {c++} END {print c + 0}' dimer_single_ref_split_events.tsv)
        single_ref_split_support=\$(awk -F"\\t" 'NR > 1 {s += (\$2 + 0)} END {print s + 0}' dimer_single_ref_split_profile.tsv)
        total_split_support=\$((event_split_support + single_ref_split_support))
        read single_ref_dominant_split_pos single_ref_dominant_split_support < <(
            awk -F"\\t" '
                NR == 1 { next }
                {
                    pos = \$1 + 0
                    support = \$2 + 0
                    if (support > best || (support == best && support > 0 && (best_pos == "" || pos < best_pos))) {
                        best = support
                        best_pos = pos
                    }
                }
                END {
                    if (best > 0) {
                        print best_pos, best
                    } else {
                        print "NA 0"
                    }
                }
            ' dimer_single_ref_split_profile.tsv
        )
        single_ref_dominant_split_support_pct=\$(awk -v support="\${single_ref_dominant_split_support}" -v total="\${single_ref_split_support}" 'BEGIN {
            if (total > 0) {
                printf "%.4f", (100.0 * support) / total
            } else {
                printf "0"
            }
        }')
        if [[ "\${single_ref_dominant_split_support}" -gt "\${dominant_split_junction_support}" ]]; then
            dominant_split_junction_pos="\${single_ref_dominant_split_pos}"
            dominant_split_junction_support="\${single_ref_dominant_split_support}"
        fi
        dominant_split_junction_support_pct=\$(awk -v support="\${dominant_split_junction_support}" -v total="\${total_junction_support}" 'BEGIN {
            if (total > 0) {
                printf "%.4f", (100.0 * support) / total
            } else {
                printf "0"
            }
        }')
        dominant_split_junction_support_pct_of_split=\$(awk -v support="\${dominant_split_junction_support}" -v total="\${total_split_support}" 'BEGIN {
            if (total > 0) {
                printf "%.4f", (100.0 * support) / total
            } else {
                printf "0"
            }
        }')
        seam_support_fraction_pct=\$(awk -v seam="\${seam_event_reads}" -v total="\${total_junction_support}" 'BEGIN {
            if (total > 0) {
                printf "%.4f", (100.0 * seam) / total
            } else {
                printf "0"
            }
        }')
        split_support_fraction_pct=\$(awk -v split_reads="\${total_split_support}" -v total="\${total_junction_support}" 'BEGIN {
            if (total > 0) {
                printf "%.4f", (100.0 * split_reads) / total
            } else {
                printf "0"
            }
        }')

        # Position 1 can be inflated when a circular reference is linearized.
        # Track support near the reference boundaries separately.
        boundary_window_bp=\$(awk -v L="\${ref_len}" 'BEGIN {
            w = int((L * 0.02) + 0.5)
            if (w < 25) w = 25
            if (w > 250) w = 250
            print w
        }')
        boundary_window_support=\$(awk -F"\\t" -v w="\${boundary_window_bp}" -v L="\${ref_len}" '
            NR == 1 { next }
            {
                pos = \$1 + 0
                support = \$3 + 0
                if (pos <= w || pos > (L - w)) total += support
            }
            END {
                print total + 0
            }
        ' dimer_junction_clusters.tsv)
        boundary_window_support_pct=\$(awk -v support="\${boundary_window_support}" -v total="\${total_junction_support}" 'BEGIN {
            if (total > 0) {
                printf "%.4f", (100.0 * support) / total
            } else {
                printf "0"
            }
        }')
        boundary_dominant_artifact_flag=\$(awk -v bpct="\${boundary_window_support_pct}" -v split_reads="\${total_split_support}" 'BEGIN {
            if (bpct + 0 >= 40 && split_reads + 0 < 3) {
                print 1
            } else {
                print 0
            }
        }')
        read dominant_nonboundary_junction_pos dominant_nonboundary_junction_support < <(
            awk -F"\\t" -v w="\${boundary_window_bp}" -v L="\${ref_len}" '
                NR == 1 { next }
                {
                    pos = \$1 + 0
                    support = \$3 + 0
                    if (pos <= w || pos > (L - w)) next
                    if (support > best || (support == best && support > 0 && (best_pos == "" || pos < best_pos))) {
                        best = support
                        best_pos = pos
                    }
                }
                END {
                    if (best > 0) {
                        print best_pos, best
                    } else {
                        print "NA 0"
                    }
                }
            ' dimer_junction_clusters.tsv
        )
        dominant_nonboundary_junction_support_pct=\$(awk -v support="\${dominant_nonboundary_junction_support}" -v total="\${total_junction_support}" 'BEGIN {
            if (total > 0) {
                printf "%.4f", (100.0 * support) / total
            } else {
                printf "0"
            }
        }')

        awk -F"\\t" -v total="\${total_junction_support}" -v w="\${boundary_window_bp}" -v L="\${ref_len}" '
            BEGIN {
                OFS = "\\t"
                print "position_mod_ref", "support_reads", "support_pct", "in_boundary_window"
            }
            NR == 1 { next }
            {
                pos = \$1 + 0
                support = \$3 + 0
                if (support <= 0) next
                pct = (total > 0) ? (100.0 * support) / total : 0
                in_window = (pos <= w || pos > (L - w)) ? 1 : 0
                print pos, support, sprintf("%.4f", pct), in_window
            }
        ' dimer_junction_clusters.tsv > dimer_junction_hotspots.unsorted.tsv
        {
            head -n1 dimer_junction_hotspots.unsorted.tsv
            tail -n +2 dimer_junction_hotspots.unsorted.tsv | LC_ALL=C sort -k2,2nr -k1,1n
        } > dimer_junction_hotspots.tsv
        rm -f dimer_junction_hotspots.unsorted.tsv

        awk -F"\\t" -v w="\${boundary_window_bp}" -v L="\${ref_len}" '
            BEGIN {
                OFS = "\\t"
                print "position_mod_ref", "boundary_start_reads", "position_event_reads", "boundary_seam_or_single_start_reads"
            }
            NR == 1 { next }
            {
                pos = \$4 + 0
                start = \$2 + 0
                if (pos <= 0 || start <= 0) next
                in_boundary_start = (start <= w || start > (L - w)) ? 1 : 0
                boundary_start[pos] += in_boundary_start
                total_events[pos]++
                seam_or_single = (\$6 == "seam" || \$6 == "single") ? 1 : 0
                if (in_boundary_start && seam_or_single) boundary_seam_or_single[pos]++
            }
            END {
                for (pos in total_events) {
                    print pos, (boundary_start[pos] + 0), (total_events[pos] + 0), (boundary_seam_or_single[pos] + 0)
                }
            }
        ' dimer_junction_events.tsv > dimer_breakpoint_start_counts.unsorted.tsv
        {
            head -n1 dimer_breakpoint_start_counts.unsorted.tsv
            tail -n +2 dimer_breakpoint_start_counts.unsorted.tsv | LC_ALL=C sort -k1,1n
        } > dimer_breakpoint_start_counts.tsv
        rm -f dimer_breakpoint_start_counts.unsorted.tsv

        awk -F"\\t" -v w="\${boundary_window_bp}" -v L="\${ref_len}" -v total="\${total_junction_support}" -v total_split="\${total_split_support}" '
            BEGIN {
                OFS = "\\t"
            }
            FNR == 1 { file_idx++ }
            file_idx == 1 {
                if (FNR == 1) next
                pos = \$1 + 0
                if (pos <= 0) next
                total_support[pos] = \$3 + 0
                pos_seen[pos] = 1
                next
            }
            file_idx == 2 {
                if (FNR == 1) next
                if ((\$5 + 0) <= 0) next
                pos = \$4 + 0
                if (pos <= 0) next
                if (\$6 == "split") split_support[pos]++
                if (\$6 == "seam") seam_support[pos]++
                pos_seen[pos] = 1
                next
            }
            file_idx == 3 {
                if (FNR == 1) next
                pos = \$1 + 0
                if (pos <= 0) next
                split_support[pos] += (\$2 + 0)
                pos_seen[pos] = 1
                next
            }
            file_idx == 4 {
                if (FNR == 1) next
                pos = \$1 + 0
                if (pos <= 0) next
                boundary_start_reads[pos] = \$2 + 0
                position_event_reads[pos] = \$3 + 0
                boundary_seam_or_single_reads[pos] = \$4 + 0
                pos_seen[pos] = 1
                next
            }
            END {
                for (pos in pos_seen) {
                    total_pos = total_support[pos] + 0
                    split_pos = split_support[pos] + 0
                    if (total_pos == 0 && split_pos > 0) total_pos = split_pos
                    seam_pos = seam_support[pos] + 0
                    start_boundary = boundary_start_reads[pos] + 0
                    start_total = position_event_reads[pos] + 0
                    support_pct_all = (total > 0) ? (100.0 * total_pos) / total : 0
                    split_pct_pos = (total_pos > 0) ? (100.0 * split_pos) / total_pos : 0
                    split_pct_all_split = (total_split > 0) ? (100.0 * split_pos) / total_split : 0
                    boundary_start_fraction = (start_total > 0) ? (100.0 * start_boundary) / start_total : 0
                    seam_fraction = (total_pos > 0) ? (100.0 * seam_pos) / total_pos : 0
                    split_to_seam_ratio = (seam_pos > 0) ? split_pos / seam_pos : ((split_pos > 0) ? 999 : 0)
                    in_boundary = (pos <= w || pos > (L - w)) ? 1 : 0
                    artifact_flag = 0
                    if (in_boundary == 1 && split_pos < 3 && (seam_fraction >= 50.0 || boundary_start_fraction >= 60.0)) {
                        artifact_flag = 1
                    }
                    confidence = "unconfirmed"
                    if (artifact_flag == 1) {
                        confidence = "artifact_likely"
                    } else if (split_pos >= 8 && seam_fraction <= 35.0 && boundary_start_fraction <= 40.0 && split_to_seam_ratio >= 0.5) {
                        confidence = "high"
                    } else if (split_pos >= 5 && seam_fraction <= 50.0 && boundary_start_fraction <= 50.0) {
                        confidence = "medium"
                    } else if (split_pos >= 3 && (in_boundary == 0 || seam_fraction <= 70.0)) {
                        confidence = "low"
                    } else if (split_pos >= 1) {
                        confidence = "exploratory"
                    }
                    print pos, total_pos, seam_pos, split_pos, sprintf("%.4f", support_pct_all), sprintf("%.4f", split_pct_pos), sprintf("%.4f", split_pct_all_split), in_boundary, (start_boundary + 0), sprintf("%.4f", boundary_start_fraction), sprintf("%.4f", seam_fraction), sprintf("%.4f", split_to_seam_ratio), artifact_flag, confidence
                }
            }
        ' dimer_junction_clusters.tsv dimer_junction_events.tsv dimer_single_ref_split_profile.tsv dimer_breakpoint_start_counts.tsv > dimer_breakpoint_screen.unsorted.tsv
        {
            head -n1 dimer_breakpoint_screen.tsv
            LC_ALL=C sort -k4,4nr -k2,2nr -k1,1n dimer_breakpoint_screen.unsorted.tsv
        } > dimer_breakpoint_screen.tsv.tmp
        mv dimer_breakpoint_screen.tsv.tmp dimer_breakpoint_screen.tsv
        rm -f dimer_breakpoint_screen.unsorted.tsv

        informative_breakpoint_count=\$(awk -F"\\t" '
            NR == 1 { next }
            (\$13 + 0) == 0 && (\$14 == "high" || \$14 == "medium") {
                c++
            }
            END { print c + 0 }
        ' dimer_breakpoint_screen.tsv)
        artifact_breakpoint_count=\$(awk -F"\\t" '
            NR == 1 { next }
            (\$13 + 0) == 1 { c++ }
            END { print c + 0 }
        ' dimer_breakpoint_screen.tsv)
        seam_only_unresolved_flag=\$(awk -v split_reads="\${total_split_support}" -v seam_reads="\${seam_event_reads}" 'BEGIN {
            if (split_reads + 0 == 0 && seam_reads + 0 > 0) {
                print 1
            } else {
                print 0
            }
        }')
        breakpoint_model_status=\$(awk -v split_reads="\${total_split_support}" -v informative="\${informative_breakpoint_count}" -v seam_only="\${seam_only_unresolved_flag}" 'BEGIN {
            if (split_reads + 0 >= 5 && informative + 0 > 0) {
                print "split_supported"
            } else if (split_reads + 0 >= 1 && informative + 0 > 0) {
                print "provisional_split_supported"
            } else if (split_reads + 0 >= 1) {
                print "split_detected_unresolved"
            } else if (seam_only + 0 == 1) {
                print "seam_only_unresolved"
            } else {
                print "no_junction_evidence"
            }
        }')

        if [[ "\${total_split_support}" -ge 3 ]]; then
            read screened_primary_breakpoint_position_mod_ref screened_primary_breakpoint_support_reads screened_primary_breakpoint_boundary_start_fraction screened_primary_breakpoint_seam_fraction screened_primary_breakpoint_split_to_seam_ratio screened_primary_breakpoint_confidence < <(
                awk -F"\\t" '
                    NR == 1 { next }
                    (\$4 + 0) > 0 && (\$13 + 0) == 0 && (\$14 == "high" || \$14 == "medium" || \$14 == "low") {
                        print \$1, \$4, \$10, \$11, \$12, \$14
                        exit
                    }
                ' dimer_breakpoint_screen.tsv
            ) || true
            if [[ -z "\${screened_primary_breakpoint_position_mod_ref:-}" || "\${screened_primary_breakpoint_position_mod_ref}" == "NA" ]]; then
                read screened_primary_breakpoint_position_mod_ref screened_primary_breakpoint_support_reads screened_primary_breakpoint_boundary_start_fraction screened_primary_breakpoint_seam_fraction screened_primary_breakpoint_split_to_seam_ratio screened_primary_breakpoint_confidence < <(
                    awk -F"\\t" '
                        NR == 1 { next }
                        (\$4 + 0) > 0 && (\$13 + 0) == 0 {
                            print \$1, \$4, \$10, \$11, \$12, \$14
                            exit
                        }
                    ' dimer_breakpoint_screen.tsv
                ) || true
            fi
            if [[ -z "\${screened_primary_breakpoint_position_mod_ref:-}" ]]; then
                screened_primary_breakpoint_position_mod_ref="NA"
                screened_primary_breakpoint_support_reads=0
                screened_primary_breakpoint_boundary_start_fraction=0
                screened_primary_breakpoint_seam_fraction=0
                screened_primary_breakpoint_split_to_seam_ratio=0
                screened_primary_breakpoint_confidence="insufficient"
            fi
        elif [[ "\${total_split_support}" -ge 1 ]]; then
            read screened_primary_breakpoint_position_mod_ref screened_primary_breakpoint_support_reads screened_primary_breakpoint_boundary_start_fraction screened_primary_breakpoint_seam_fraction screened_primary_breakpoint_split_to_seam_ratio screened_primary_breakpoint_confidence < <(
                awk -F"\\t" '
                    NR == 1 { next }
                    (\$4 + 0) > 0 {
                        print \$1, \$4, \$10, \$11, \$12, \$14
                        exit
                    }
                ' dimer_breakpoint_screen.tsv
            ) || true
            if [[ -z "\${screened_primary_breakpoint_position_mod_ref:-}" ]]; then
                screened_primary_breakpoint_position_mod_ref="NA"
                screened_primary_breakpoint_support_reads=0
                screened_primary_breakpoint_boundary_start_fraction=0
                screened_primary_breakpoint_seam_fraction=0
                screened_primary_breakpoint_split_to_seam_ratio=0
                screened_primary_breakpoint_confidence="insufficient"
            fi
        fi
        if [[ -z "\${screened_primary_breakpoint_confidence:-}" ]]; then
            screened_primary_breakpoint_confidence="insufficient"
        fi

        rotation_scan_step_effective=\$(awk -v req="\${rotation_scan_step_requested}" -v L="\${ref_len}" 'BEGIN {
            step = int(req + 0)
            if (step < 1) step = 1
            if (L > 0 && step > L) step = L
            max_offsets = 512
            total_offsets = (L > 0) ? int((L + step - 1) / step) : 1
            if (L > 0 && total_offsets > max_offsets) {
                step = int((L + max_offsets - 1) / max_offsets)
                if (step < 1) step = 1
            }
            print step
        }')
        rotation_selected_boundary_support_reads="\${boundary_window_support}"
        rotation_selected_boundary_support_pct="\${boundary_window_support_pct}"
        rotation_dominant_hotspot_position_rotated="\${dominant_junction_pos}"
        rotation_dominant_hotspot_position_mod_ref="\${dominant_junction_pos}"
        rotation_dominant_hotspot_support_reads="\${dominant_junction_support}"
        rotation_dominant_hotspot_support_pct="\${dominant_junction_support_pct}"

        if [[ "\${rotation_enabled}" == "true" && "\${total_junction_support}" -gt 0 && "\${ref_len}" -gt 0 ]]; then
            rotation_offsets_mode="scan"
            awk -F"\\t" -v L="\${ref_len}" -v W="\${boundary_window_bp}" -v step="\${rotation_scan_step_effective}" -v aligned="\${aligned_reads}" '
                BEGIN {
                    OFS = "\\t"
                    for (offset = 0; offset < L; offset += step) {
                        offsets[++n_offsets] = offset
                    }
                    if (n_offsets == 0) offsets[++n_offsets] = 0
                }
                NR == 1 { next }
                (\$5 + 0) > 0 {
                    pos_mod = \$4 + 0
                    if (pos_mod <= 0) next
                    event_type = \$6
                    for (i = 1; i <= n_offsets; i++) {
                        offset = offsets[i]
                        pos_rot = ((pos_mod - 1 - offset) % L + L) % L + 1
                        key = offset SUBSEP pos_mod
                        support[key]++
                        if (event_type == "split") {
                            split_support[key]++
                            split_total[offset]++
                        } else if (event_type == "seam") {
                            seam_support[key]++
                            seam_total[offset]++
                        } else {
                            single_total[offset]++
                        }
                        total_support[offset]++
                        in_boundary = (pos_rot <= W || pos_rot > (L - W)) ? 1 : 0
                        if (in_boundary) boundary_support[offset]++
                        if (!(offset in top_support) || support[key] > top_support[offset] || (support[key] == top_support[offset] && pos_rot < top_rot[offset])) {
                            top_support[offset] = support[key]
                            top_mod[offset] = pos_mod
                            top_rot[offset] = pos_rot
                        }
                    }
                }
                END {
                    print "offset_bp", "mode", "aligned_reads", "total_support", "boundary_support", "boundary_pct", "split_support", "seam_support", "single_support", "dom_rot", "dom_mod", "dom_reads", "dom_pct", "seam_only" > "dimer_rotated_remap_summary.unsorted.tsv"
                    print "offset_bp", "pos_mod", "support", "split", "seam", "pos_rot", "in_boundary" > "dimer_rotated_remap_breakpoints.unsorted.tsv"
                    for (i = 1; i <= n_offsets; i++) {
                        offset = offsets[i]
                        total = total_support[offset] + 0
                        boundary = boundary_support[offset] + 0
                        split_sum = split_total[offset] + 0
                        seam_sum = seam_total[offset] + 0
                        single_sum = single_total[offset] + 0
                        dominant_rot = (offset in top_rot) ? top_rot[offset] + 0 : 0
                        dominant_mod = (offset in top_mod) ? top_mod[offset] + 0 : 0
                        dominant_reads = (offset in top_support) ? top_support[offset] + 0 : 0
                        boundary_pct = (total > 0) ? (100.0 * boundary) / total : 0
                        dominant_pct = (total > 0) ? (100.0 * dominant_reads) / total : 0
                        seam_only = (split_sum == 0 && seam_sum > 0) ? 1 : 0
                        print offset, "scan", (aligned + 0), total, boundary, sprintf("%.4f", boundary_pct), split_sum, seam_sum, single_sum, dominant_rot, dominant_mod, dominant_reads, sprintf("%.4f", dominant_pct), seam_only >> "dimer_rotated_remap_summary.unsorted.tsv"
                    }
                    for (key in support) {
                        split(key, parts, SUBSEP)
                        offset = parts[1] + 0
                        pos_mod = parts[2] + 0
                        supp = support[key] + 0
                        split_sum = split_support[key] + 0
                        seam_sum = seam_support[key] + 0
                        pos_rot = ((pos_mod - 1 - offset) % L + L) % L + 1
                        in_boundary = (pos_rot <= W || pos_rot > (L - W)) ? 1 : 0
                        print offset, pos_mod, supp, split_sum, seam_sum, pos_rot, in_boundary >> "dimer_rotated_remap_breakpoints.unsorted.tsv"
                    }
                }
            ' dimer_junction_events.tsv

            {
                head -n1 dimer_rotated_remap_summary.unsorted.tsv
                tail -n +2 dimer_rotated_remap_summary.unsorted.tsv | LC_ALL=C sort -k1,1n
            } > dimer_rotated_remap_summary.tsv
            {
                head -n1 dimer_rotated_remap_breakpoints.unsorted.tsv
                tail -n +2 dimer_rotated_remap_breakpoints.unsorted.tsv | LC_ALL=C sort -k1,1n -k3,3nr -k6,6n
            } > dimer_rotated_remap_breakpoints.tsv
            rm -f dimer_rotated_remap_summary.unsorted.tsv dimer_rotated_remap_breakpoints.unsorted.tsv

            rotation_offsets_tested=\$(awk -F"\\t" 'NR > 1 {c++} END {print c + 0}' dimer_rotated_remap_summary.tsv)
            rotation_selected_total_support=0
            read rotation_selected_offset_bp rotation_selected_boundary_support_reads rotation_selected_total_support rotation_dominant_hotspot_position_rotated rotation_dominant_hotspot_position_mod_ref rotation_dominant_hotspot_support_reads < <(
                awk -F"\\t" '
                    NR == 1 { next }
                    {
                        offset = \$1 + 0
                        total = \$4 + 0
                        boundary = \$5 + 0
                        dominant_reads = \$12 + 0
                        dominant_rot = \$10 + 0
                        dominant_mod = \$11 + 0
                        if (!seen || boundary < best_boundary || (boundary == best_boundary && dominant_reads > best_dominant_reads) || (boundary == best_boundary && dominant_reads == best_dominant_reads && offset < best_offset)) {
                            seen = 1
                            best_offset = offset
                            best_boundary = boundary
                            best_total = total
                            best_dominant_rot = dominant_rot
                            best_dominant_mod = dominant_mod
                            best_dominant_reads = dominant_reads
                        }
                    }
                    END {
                        if (!seen) {
                            print "0 0 0 0 0 0"
                        } else {
                            print best_offset, best_boundary, best_total, best_dominant_rot, best_dominant_mod, best_dominant_reads
                        }
                    }
                ' dimer_rotated_remap_summary.tsv
            )

            rotation_selected_boundary_support_pct=\$(awk -v support="\${rotation_selected_boundary_support_reads}" -v total="\${rotation_selected_total_support}" 'BEGIN {
                if (total > 0) {
                    printf "%.4f", (100.0 * support) / total
                } else {
                    printf "0"
                }
            }')
            rotation_dominant_hotspot_support_pct=\$(awk -v support="\${rotation_dominant_hotspot_support_reads}" -v total="\${rotation_selected_total_support}" 'BEGIN {
                if (total > 0) {
                    printf "%.4f", (100.0 * support) / total
                } else {
                    printf "0"
                }
            }')

            awk -F"\\t" -v target="\${rotation_selected_offset_bp}" -v total="\${rotation_selected_total_support}" '
                BEGIN {
                    OFS = "\\t"
                    print "position_rotated", "position_mod_ref", "support_reads", "support_pct", "in_boundary_window"
                }
                NR == 1 { next }
                (\$1 + 0) == (target + 0) {
                    pct = (total > 0) ? (100.0 * (\$3 + 0)) / total : 0
                    print (\$6 + 0), (\$2 + 0), (\$3 + 0), sprintf("%.4f", pct), (\$7 + 0)
                }
            ' dimer_rotated_remap_breakpoints.tsv > dimer_junction_rotated_profile.unsorted.tsv
            {
                head -n1 dimer_junction_rotated_profile.unsorted.tsv
                tail -n +2 dimer_junction_rotated_profile.unsorted.tsv | LC_ALL=C sort -k1,1n -k3,3nr
            } > dimer_junction_rotated_profile.tsv
            rm -f dimer_junction_rotated_profile.unsorted.tsv
        else
            rotation_offsets_mode="disabled_or_no_support"
            rotation_offsets_tested=1
            echo -e "0\t\${rotation_offsets_mode}\t\${aligned_reads}\t\${total_junction_support}\t\${boundary_window_support}\t\${boundary_window_support_pct}\t\${total_split_support}\t\${seam_event_reads}\t\${single_event_reads}\t\${dominant_junction_pos}\t\${dominant_junction_pos}\t\${dominant_junction_support}\t\${dominant_junction_support_pct}\t\${seam_only_unresolved_flag}" >> dimer_rotated_remap_summary.tsv
            awk -F"\\t" -v total="\${total_junction_support}" -v W="\${boundary_window_bp}" -v L="\${ref_len}" '
                BEGIN {
                    OFS = "\\t"
                    print "position_rotated", "position_mod_ref", "support_reads", "support_pct", "in_boundary_window"
                }
                NR == 1 { next }
                {
                    pos = \$1 + 0
                    support = \$3 + 0
                    if (pos <= 0 || support <= 0) next
                    pct = (total > 0) ? (100.0 * support) / total : 0
                    in_window = (pos <= W || pos > (L - W)) ? 1 : 0
                    print pos, pos, support, sprintf("%.4f", pct), in_window
                }
            ' dimer_junction_clusters.tsv > dimer_junction_rotated_profile.tsv
        fi

        if samtools consensus -f fasta dimer_candidates.aligned.bam > dimer_consensus.fasta 2> dimer_consensus.log; then
            consensus_status="ok"
        else
            echo "samtools consensus unavailable or failed; attempting mpileup-majority fallback" >> dimer_consensus.log
            if samtools mpileup -aa -A -d 1000000 -f dimer_reference.fasta dimer_candidates.aligned.bam 2>> dimer_consensus.log \
                | awk -f "${codeRoot}/scripts/mpileup_majority_consensus.awk" \
                > dimer_consensus.fasta.tmp && [[ -s dimer_consensus.fasta.tmp ]]; then
                mv dimer_consensus.fasta.tmp dimer_consensus.fasta
                consensus_status="pileup_majority_fallback"
            else
                rm -f dimer_consensus.fasta.tmp
                cp dimer_reference.fasta dimer_consensus.fasta
                consensus_status="fallback_reference_copy"
            fi
        fi

        bash "${codeRoot}/scripts/dominant_dimer_consensus.sh" \\
            --events dimer_junction_events.tsv \\
            --bam dimer_candidates.aligned.bam \\
            --fastq dimer_candidates.fastq \\
            --dimer-consensus dimer_consensus.fasta \\
            --dimer-reference dimer_reference.fasta \\
            --dimer-count "\${dimer_count}" \\
            --screened-pos "\${screened_primary_breakpoint_position_mod_ref}" \\
            --screened-support "\${screened_primary_breakpoint_support_reads}" \\
            --dominant-split-pos "\${dominant_split_junction_pos}" \\
            --dominant-split-support "\${dominant_split_junction_support}" \\
            --single-ref-pos "\${single_ref_dominant_split_pos}" \\
            --single-ref-support "\${single_ref_dominant_split_support}" \\
            --dominant-junction-pos "\${dominant_junction_pos}" \\
            --dominant-junction-support "\${dominant_junction_support}" \\
            --threads ${task.cpus} \\
            --out-consensus dominant_dimer_consensus.fasta \\
            --out-log dominant_dimer_consensus.log \\
            --out-metadata dominant_dimer_consensus_metadata.tsv

        meta_value() {
            local key="\$1"
            awk -F"\\t" -v k="\${key}" 'NR > 1 && \$1 == k {print \$2; exit}' dominant_dimer_consensus_metadata.tsv
        }

        dominant_consensus_status="\$(meta_value dominant_consensus_status)"
        dominant_consensus_breakpoint_pos="\$(meta_value dominant_consensus_breakpoint_position_mod_ref)"
        dominant_consensus_breakpoint_source="\$(meta_value dominant_consensus_breakpoint_source)"
        dominant_consensus_support_reads="\$(meta_value dominant_consensus_support_reads)"
        dominant_consensus_support_pct="\$(meta_value dominant_consensus_support_pct)"
        dominant_consensus_read_id="\$(meta_value dominant_consensus_read_id)"
        dominant_consensus_read_copies="\$(meta_value dominant_consensus_read_copies)"
        dominant_consensus_read_support_pct="\$(meta_value dominant_consensus_read_support_pct)"

        dominant_consensus_status="\${dominant_consensus_status:-not_run}"
        dominant_consensus_breakpoint_pos="\${dominant_consensus_breakpoint_pos:-NA}"
        dominant_consensus_breakpoint_source="\${dominant_consensus_breakpoint_source:-none}"
        dominant_consensus_support_reads="\${dominant_consensus_support_reads:-0}"
        dominant_consensus_support_pct="\${dominant_consensus_support_pct:-0}"
        dominant_consensus_read_id="\${dominant_consensus_read_id:-NA}"
        dominant_consensus_read_copies="\${dominant_consensus_read_copies:-0}"
        dominant_consensus_read_support_pct="\${dominant_consensus_read_support_pct:-0}"
    fi

    {
        echo -e "metric\\tvalue"
        echo -e "rotation_enabled\\t\${rotation_enabled}"
        echo -e "rotation_offsets_mode\\t\${rotation_offsets_mode}"
        echo -e "rotation_offsets_tested\\t\${rotation_offsets_tested}"
        echo -e "rotation_scan_step_requested_bp\\t\${rotation_scan_step_requested}"
        echo -e "rotation_scan_step_effective_bp\\t\${rotation_scan_step_effective}"
        echo -e "rotation_selected_offset_bp\\t\${rotation_selected_offset_bp}"
        echo -e "rotation_selected_boundary_support_reads\\t\${rotation_selected_boundary_support_reads}"
        echo -e "rotation_selected_boundary_support_pct\\t\${rotation_selected_boundary_support_pct}"
        echo -e "rotation_dominant_hotspot_position_rotated\\t\${rotation_dominant_hotspot_position_rotated}"
        echo -e "rotation_dominant_hotspot_position_mod_ref\\t\${rotation_dominant_hotspot_position_mod_ref}"
        echo -e "rotation_dominant_hotspot_support_reads\\t\${rotation_dominant_hotspot_support_reads}"
        echo -e "rotation_dominant_hotspot_support_pct\\t\${rotation_dominant_hotspot_support_pct}"
    } > dimer_junction_rotation_summary.tsv

    {
        echo -e "metric\\tvalue"
        echo -e "expected_plasmid_size\\t${expectedSize}"
        echo -e "min_read_length\\t${minReadLength}"
        echo -e "dimer_cutoff\\t\${dimer_cutoff}"
        echo -e "trimer_cutoff\\t\${trimer_cutoff}"
        echo -e "reference_name\\t\${ref_name}"
        echo -e "reference_length\\t\${ref_len}"
        echo -e "dimer_candidate_reads\\t\${dimer_count}"
        echo -e "aligned_dimer_reads\\t\${aligned_reads}"
        echo -e "junction_spanning_reads\\t\${junction_spanning_reads}"
        echo -e "junction_event_split_reads\\t\${split_event_reads}"
        echo -e "junction_event_split_reads_dimer_ref\\t\${event_split_support}"
        echo -e "junction_event_seam_reads\\t\${seam_event_reads}"
        echo -e "junction_event_single_reads\\t\${single_event_reads}"
        echo -e "single_ref_split_reads\\t\${single_ref_split_reads}"
        echo -e "single_ref_split_support_reads\\t\${single_ref_split_support}"
        echo -e "single_ref_dominant_split_position_mod_ref\\t\${single_ref_dominant_split_pos}"
        echo -e "single_ref_dominant_split_support_reads\\t\${single_ref_dominant_split_support}"
        echo -e "single_ref_dominant_split_support_pct\\t\${single_ref_dominant_split_support_pct}"
        echo -e "split_support_reads\\t\${total_split_support}"
        echo -e "seam_support_fraction_pct\\t\${seam_support_fraction_pct}"
        echo -e "split_support_fraction_pct\\t\${split_support_fraction_pct}"
        echo -e "dominant_split_junction_position_mod_ref\\t\${dominant_split_junction_pos}"
        echo -e "dominant_split_junction_support_reads\\t\${dominant_split_junction_support}"
        echo -e "dominant_split_junction_support_pct\\t\${dominant_split_junction_support_pct}"
        echo -e "dominant_split_junction_support_pct_of_split\\t\${dominant_split_junction_support_pct_of_split}"
        echo -e "screened_primary_breakpoint_position_mod_ref\\t\${screened_primary_breakpoint_position_mod_ref}"
        echo -e "screened_primary_breakpoint_support_reads\\t\${screened_primary_breakpoint_support_reads}"
        echo -e "screened_primary_breakpoint_confidence\\t\${screened_primary_breakpoint_confidence}"
        echo -e "screened_primary_breakpoint_boundary_start_fraction\\t\${screened_primary_breakpoint_boundary_start_fraction}"
        echo -e "screened_primary_breakpoint_seam_fraction\\t\${screened_primary_breakpoint_seam_fraction}"
        echo -e "screened_primary_breakpoint_split_to_seam_ratio\\t\${screened_primary_breakpoint_split_to_seam_ratio}"
        echo -e "informative_breakpoint_count\\t\${informative_breakpoint_count}"
        echo -e "artifact_breakpoint_count\\t\${artifact_breakpoint_count}"
        echo -e "seam_only_unresolved_flag\\t\${seam_only_unresolved_flag}"
        echo -e "breakpoint_model_status\\t\${breakpoint_model_status}"
        echo -e "boundary_dominant_artifact_flag\\t\${boundary_dominant_artifact_flag}"
        echo -e "dominant_junction_position_mod_ref\\t\${dominant_junction_pos}"
        echo -e "dominant_junction_support_reads\\t\${dominant_junction_support}"
        echo -e "dominant_junction_support_pct\\t\${dominant_junction_support_pct}"
        echo -e "boundary_window_bp\\t\${boundary_window_bp}"
        echo -e "boundary_window_support_reads\\t\${boundary_window_support}"
        echo -e "boundary_window_support_pct\\t\${boundary_window_support_pct}"
        echo -e "dominant_nonboundary_junction_position_mod_ref\\t\${dominant_nonboundary_junction_pos}"
        echo -e "dominant_nonboundary_junction_support_reads\\t\${dominant_nonboundary_junction_support}"
        echo -e "dominant_nonboundary_junction_support_pct\\t\${dominant_nonboundary_junction_support_pct}"
        echo -e "rotation_enabled\\t\${rotation_enabled}"
        echo -e "rotation_offsets_mode\\t\${rotation_offsets_mode}"
        echo -e "rotation_offsets_tested\\t\${rotation_offsets_tested}"
        echo -e "rotation_scan_step_requested_bp\\t\${rotation_scan_step_requested}"
        echo -e "rotation_scan_step_effective_bp\\t\${rotation_scan_step_effective}"
        echo -e "rotation_selected_offset_bp\\t\${rotation_selected_offset_bp}"
        echo -e "rotation_selected_boundary_support_reads\\t\${rotation_selected_boundary_support_reads}"
        echo -e "rotation_selected_boundary_support_pct\\t\${rotation_selected_boundary_support_pct}"
        echo -e "rotation_dominant_hotspot_position_rotated\\t\${rotation_dominant_hotspot_position_rotated}"
        echo -e "rotation_dominant_hotspot_position_mod_ref\\t\${rotation_dominant_hotspot_position_mod_ref}"
        echo -e "rotation_dominant_hotspot_support_reads\\t\${rotation_dominant_hotspot_support_reads}"
        echo -e "rotation_dominant_hotspot_support_pct\\t\${rotation_dominant_hotspot_support_pct}"
        echo -e "consensus_status\\t\${consensus_status}"
        echo -e "dominant_consensus_status\\t\${dominant_consensus_status}"
        echo -e "dominant_consensus_breakpoint_position_mod_ref\\t\${dominant_consensus_breakpoint_pos}"
        echo -e "dominant_consensus_breakpoint_source\\t\${dominant_consensus_breakpoint_source}"
        echo -e "dominant_consensus_support_reads\\t\${dominant_consensus_support_reads}"
    } > dimer_analysis_summary.tsv

    {
        echo "FASTQ dimer analysis complete"
        echo "Reference: \${ref_name} (\${ref_len} bp)"
        echo "Dimer candidate/aligned/crossing: \${dimer_count}/\${aligned_reads}/\${junction_spanning_reads}"
        echo "Breakpoint model: \${breakpoint_model_status}; screened=\${screened_primary_breakpoint_position_mod_ref} support=\${screened_primary_breakpoint_support_reads} conf=\${screened_primary_breakpoint_confidence}"
        echo "Consensus: \${consensus_status}; dominant: \${dominant_consensus_status}"
    } > dimer_analysis.log
        """
    }


// ═══════════════════════════════════════════════════════════════════════════════
// Process 11: Build canonical dimer outputs from detailed analysis tables
// ═══════════════════════════════════════════════════════════════════════════════
process BuildDimerCanonicalOutputs {
    label 'local_cpu'
    publishDir "${params.out_dir}/multimer_qc", mode: 'copy'
    tag "dimer_canonicalize"

    input:
    path summary
    path events
    path single_ref_events
    path single_ref_profile
    path breakpoint_screen
    path reference_fasta

    output:
    path "dimer_breakpoint_call.tsv", emit: breakpoint_call
    path "dimer_evidence_by_position.tsv", emit: evidence_by_position
    path "dimer_read_events.tsv", emit: read_events
    path "dimer_breakpoint_sequences.tsv", emit: breakpoint_sequences
    path "dimer_secondary_anomalies.tsv", emit: secondary_anomalies
    path "dimer_secondary_summary.tsv", emit: secondary_summary
    path "dimer_diagnostics.tar.gz", emit: diagnostics, optional: true

    script:
    def codeRoot = params.code_root ?: projectDir
    def requestedDimerOutputMode = (params.dimer_output_mode ?: 'core').toString().trim().toLowerCase()
    def dimerOutputMode = (requestedDimerOutputMode in ['core', 'debug']) ? requestedDimerOutputMode : 'core'
    def emitLegacyOutputs = params.dimer_emit_legacy_outputs == true ? 'true' : 'false'
    """
    set -euo pipefail

    if [[ ! -f "${codeRoot}/scripts/build_dimer_canonical_outputs.py" ]]; then
        echo "Missing parser script: ${codeRoot}/scripts/build_dimer_canonical_outputs.py" >&2
        exit 1
    fi

    python3 "${codeRoot}/scripts/build_dimer_canonical_outputs.py" \\
        --summary ${summary} \\
        --events ${events} \\
        --single-ref-events ${single_ref_events} \\
        --single-ref-profile ${single_ref_profile} \\
        --breakpoint-screen ${breakpoint_screen} \\
        --reference-fasta ${reference_fasta} \\
        --window-bp 50 \\
        --out-call dimer_breakpoint_call.tsv \\
        --out-evidence dimer_evidence_by_position.tsv \\
        --out-read-events dimer_read_events.tsv \\
        --out-breakpoint-sequences dimer_breakpoint_sequences.tsv \\
        --out-secondary-anomalies dimer_secondary_anomalies.tsv \\
        --out-secondary-summary dimer_secondary_summary.tsv

    if [[ "${dimerOutputMode}" == "core" && "${emitLegacyOutputs}" != "true" ]]; then
        tar -czf dimer_diagnostics.tar.gz ${events} ${single_ref_events} ${single_ref_profile} ${breakpoint_screen} 2>/dev/null || true
    fi
    """
}


// ═══════════════════════════════════════════════════════════════════════════════
// Process 8: EPI2ME wf-clone-validation assembly + construct QC
// ═══════════════════════════════════════════════════════════════════════════════
process RunCloneValidation {
    // Host-side process; runs a nested Nextflow workflow that uses its own containers.
    label 'wf_clone'
    publishDir "${params.out_dir}/assembly", mode: 'copy'
    tag "clone_validation"

    input:
    tuple path(bam), val(reference_fasta)

    output:
    path "wf_clone_out", emit: out
    path "wf_clone.log", emit: log
    path "wf_clone_out/wf-clone-validation-report.html", emit: report, optional: true
    path "wf_clone_out/sample_status.txt", emit: sample_status, optional: true

    script:
    def sampleName = (params.wf_clone_sample ?: params.name ?: (params.job_id ? "nanopore_${params.job_id}" : "nanopore"))
        .toString()
        .replaceAll(/[^A-Za-z0-9._-]/, "_")
    def approxSize = (params.wf_clone_approx_size ?: 7000) as Integer
    def assmCoverage = (params.wf_clone_assm_coverage ?: 60) as Integer
    def minQuality = (params.wf_clone_min_quality != null ? params.wf_clone_min_quality : (params.min_qscore ?: 9)) as Integer
    def trimLength = (params.wf_clone_trim_length ?: 0) as Integer
    def assemblyTool = (params.wf_clone_assembly_tool ?: 'flye').toString()
    def largeConstruct = params.wf_clone_large_construct ? "--large_construct" : ""
    def wfCloneDir = (params.wf_clone_workflow_dir ?: "${System.getenv('HOME') ?: '/tmp'}/.nextflow/assets/epi2me-labs/wf-clone-validation").toString()
    def wfCloneSource = (params.wf_clone_source ?: "epi2me-labs/wf-clone-validation").toString()
    def wfCloneRevision = (params.wf_clone_revision ?: "").toString()
    def wfCloneRevisionLabel = wfCloneRevision ? "@${wfCloneRevision}" : ""
    def wfCloneProfileRaw = (params.wf_clone_profile ?: "singularity").toString()
    def wfCloneProfiles = wfCloneProfileRaw
        .split(',')
        .collect { it.trim() }
        .findAll { it }
    // Standard+singularity can enable multiple container engines in nested runs.
    // Force singularity-only for stable local/apptainer execution.
    def wfCloneProfile = wfCloneProfiles.contains('singularity') ? 'singularity' : (wfCloneProfiles ? wfCloneProfiles.join(',') : 'singularity')
    def referencePath = reference_fasta ? reference_fasta.toString().trim() : ""
    def dataRoot = (params.data_root ?: "/mnt/BioModStack").toString()
    def defaultContainerRoot = (params.container_dir ?: "${dataRoot}/apptainer").toString()
    def wfCloneSingularityCache = (params.wf_clone_singularity_cache ?: "${defaultContainerRoot}/singularity_cache").toString()
    def wfCloneNxfHome = (params.wf_clone_nxf_home ?: "${dataRoot}/nextflow").toString()
    def wfCloneOverrideBasecallerCfg = (params.wf_clone_override_basecaller_cfg ?: "").toString().trim()
    if (!wfCloneOverrideBasecallerCfg) {
        def doradoModel = (params.dorado_model ?: "sup").toString().toLowerCase()
        if (doradoModel == "hac") {
            wfCloneOverrideBasecallerCfg = "dna_r10.4.1_e8.2_400bps_hac@v5.0.0"
        } else if (doradoModel == "fast") {
            // wf-clone-validation schema does not accept a FAST profile here;
            // use HAC v5.0.0 as the closest supported fallback.
            wfCloneOverrideBasecallerCfg = "dna_r10.4.1_e8.2_400bps_hac@v5.0.0"
        } else {
            wfCloneOverrideBasecallerCfg = "dna_r10.4.1_e8.2_400bps_sup@v5.0.0"
        }
    }
    """
    set -euo pipefail
    export NXF_DISABLE_CHECK_LATEST=true
    export NXF_DOCKER_ENABLED=false
    export NXF_SINGULARITY_ENABLED=true
    export NXF_SINGULARITY_CACHEDIR="${wfCloneSingularityCache}"
    export NXF_HOME="${wfCloneNxfHome}"
    mkdir -p "\${NXF_SINGULARITY_CACHEDIR}" "\${NXF_HOME}"
    echo "wf-clone using NXF_SINGULARITY_CACHEDIR=\${NXF_SINGULARITY_CACHEDIR}" >&2

    resolved_wf_clone_dir="${wfCloneDir}"

    # Attempt to self-bootstrap the EPI2ME workflow if local assets are missing.
    if [[ ! -d "\${resolved_wf_clone_dir}" ]]; then
        echo "wf-clone-validation workflow directory not found: ${wfCloneDir}" >&2
        echo "Attempting to pull ${wfCloneSource}${wfCloneRevisionLabel}..." >&2
        pull_cmd=(nextflow pull "${wfCloneSource}")
        if [[ -n "${wfCloneRevision}" ]]; then
            pull_cmd+=(-r "${wfCloneRevision}")
        fi
        "\${pull_cmd[@]}" || true
        pulled_dir="\${HOME:-/tmp}/.nextflow/assets/${wfCloneSource}"
        if [[ -d "\${pulled_dir}" ]]; then
            resolved_wf_clone_dir="\${pulled_dir}"
        fi
    fi

    if [[ ! -d "\${resolved_wf_clone_dir}" ]]; then
        echo "wf-clone-validation workflow directory not found: ${wfCloneDir}" >&2
        echo "Set --wf_clone_workflow_dir to a valid local checkout of epi2me-labs/wf-clone-validation." >&2
        exit 1
    fi

    mkdir -p wf_clone_assets wf_clone_out
    cp -a "\${resolved_wf_clone_dir}/." wf_clone_assets/

    # Compatibility patch for wf-clone-validation on Nextflow >=25:
    # report process re-declares a variable named 'metadata' in the same scope.
    if grep -q "String metadata = metadata_obj.toPrettyString()" wf_clone_assets/main.nf; then
        python3 - <<'PY'
from pathlib import Path

p = Path("wf_clone_assets/main.nf")
text = p.read_text()
text = text.replace(
    "String metadata = metadata_obj.toPrettyString()",
    "String metadata_json = metadata_obj.toPrettyString()",
)
text = text.replace(
    "echo '\${metadata}' > metadata.json",
    "echo '\${metadata_json}' > metadata.json",
)
p.write_text(text)
PY
    fi

    # Patch Flye overlap heuristic for short plasmids:
    # upstream rule only lowers min-overlap for <=3kb constructs, which can
    # fail for ~5-10kb constructs with read lengths near construct size.
    flye_module="wf_clone_assets/modules/local/flye_assembly.nf"
    if [[ -f "\${flye_module}" ]] && grep -q "def min_overlap = meta.approx_size.toInteger() <= 3000 ? '--min-overlap 1000' : ''" "\${flye_module}"; then
        python3 - <<'PY'
from pathlib import Path

p = Path("wf_clone_assets/modules/local/flye_assembly.nf")
text = p.read_text()
old = "def min_overlap = meta.approx_size.toInteger() <= 3000 ? '--min-overlap 1000' : ''"
new = (
    "int approx_size_bp = meta.approx_size.toInteger()\\n"
    "        int min_overlap_bp = approx_size_bp <= 10000 ? Math.max(1000, (int)(approx_size_bp * 0.25)) : 0\\n"
    "        def min_overlap = min_overlap_bp > 0 ? '--min-overlap ' + min_overlap_bp : ''"
)
if old in text:
    text = text.replace(old, new)
    p.write_text(text)
PY
    fi

    full_ref_arg=()
    if [[ -n "${referencePath}" ]]; then
        cp "${referencePath}" wf_clone_reference.fasta
        full_ref_arg=(--full_reference "wf_clone_reference.fasta")
    fi
    override_model_arg=()
    if [[ -n "${wfCloneOverrideBasecallerCfg}" ]]; then
        override_model_arg=(--override_basecaller_cfg "${wfCloneOverrideBasecallerCfg}")
    fi

    run_wf_clone() {
        local tool="\$1"
        local log_file="wf_clone_\${tool}.log"
        nextflow -log "\${log_file}" run wf_clone_assets \\
            -profile "${wfCloneProfile}" \\
            -w "wf_clone_work_\${tool}" \\
            --bam "${bam}" \\
            --sample "${sampleName}" \\
            --out_dir wf_clone_out \\
            --approx_size ${approxSize} \\
            --assm_coverage ${assmCoverage} \\
            --min_quality ${minQuality} \\
            --trim_length ${trimLength} \\
            --assembly_tool "\${tool}" \\
            ${largeConstruct} \\
            "\${override_model_arg[@]}" \\
            "\${full_ref_arg[@]}"
    }

    wf_clone_rc=0
    chosen_tool="${assemblyTool}"

    set +e
    run_wf_clone "\${chosen_tool}"
    wf_clone_rc=\$?
    set -e

    # Flye can fail on near-genome-length reads where overlap heuristics over-shoot.
    # Retry once with CANU to improve robustness for plasmid workflows.
    if [[ \${wf_clone_rc} -ne 0 && "\${chosen_tool}" == "flye" ]]; then
        echo "Flye assembly failed (exit=\${wf_clone_rc}); retrying wf-clone-validation with CANU." >&2
        rm -rf wf_clone_out
        mkdir -p wf_clone_out
        chosen_tool="canu"
        set +e
        run_wf_clone "\${chosen_tool}"
        wf_clone_rc=\$?
        set -e
    fi

    # Consolidate nested wf logs into stable output path for UI/API consumers.
    if [[ -f "wf_clone_\${chosen_tool}.log" ]]; then
        cp "wf_clone_\${chosen_tool}.log" wf_clone.log
    elif [[ -f "wf_clone_flye.log" ]]; then
        cp "wf_clone_flye.log" wf_clone.log
    else
        : > wf_clone.log
    fi

    if [[ \${wf_clone_rc} -ne 0 ]]; then
        echo "wf-clone-validation failed after retries (final_tool=\${chosen_tool}, exit=\${wf_clone_rc})." >&2
        exit \${wf_clone_rc}
    fi
    """
}
