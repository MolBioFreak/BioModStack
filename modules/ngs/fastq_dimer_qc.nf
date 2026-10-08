/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

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
    path "qc_manifest.json", emit: qc_manifest
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
    def manifestJobId = ((params.job_id ?: '') as String).trim()
    if (!(manifestJobId ==~ /[A-Za-z0-9][A-Za-z0-9._ -]{0,255}/) || manifestJobId.contains('..')) {
        error('FASTQ dimer analysis requires an exact safe job_id')
    }
    def declaredReferenceSha256 = params.reference_sequence_sha256?.toString()?.trim()?.toLowerCase() ?: ''
    if (!(declaredReferenceSha256 ==~ /[0-9a-f]{64}/)) {
        throw new IllegalArgumentException('reference_sequence_sha256 must be exactly 64 hexadecimal characters')
    }
    def manifestJobIdArg = shellQuote(manifestJobId)
    def referenceSequenceSha256Arg = shellQuote(declaredReferenceSha256)
    def workflowId = ((params.workflow_id ?: params.ont_workflow_id ?: 'ont_fastq_qc') as String).trim()
    if (!(workflowId in ['ont_fastq_qc', 'ont_plasmid_qc', 'ont_construct_screening', 'wf_clone_validation'])) {
        error('FASTQ dimer analysis requires a canonical workflow_id')
    }
    def workflowIdArg = shellQuote(workflowId)
    def inputMode = ((params.input_mode ?: params.ont_input_mode ?: 'fastq') as String).trim()
    if (!(inputMode in ['fastq', 'bam', 'pod5'])) {
        error('FASTQ dimer analysis requires a canonical input_mode')
    }
    def inputModeArg = shellQuote(inputMode)
    def runnerArg = shellQuote("${codeRoot}/scripts/run_fastq_dimer_analysis.sh")
    def fastqArg = shellQuote(fastq)
    def referenceArg = shellQuote(reference)
    def codeRootArg = shellQuote(codeRoot)
    def minimapPresetArg = shellQuote(minimapPreset)
    """
    set -euo pipefail
    if [[ ! -f ${runnerArg} ]]; then
        echo "Missing dimer analysis runner: ${codeRoot}/scripts/run_fastq_dimer_analysis.sh" >&2
        exit 1
    fi
    bash ${runnerArg} \\
        ${fastqArg} \\
        ${referenceArg} \\
        ${codeRootArg} \\
        ${expectedSize} \\
        ${minReadLength} \\
        ${enableRotation} \\
        ${rotationScanStep} \\
        ${singleRefMinMapq} \\
        ${singleRefMinSegBp} \\
        ${singleRefMaxGapBp} \\
        ${minimapPresetArg} \\
        ${minimapAllowSecondary} \\
        ${manifestJobIdArg} \\
        ${referenceSequenceSha256Arg} \\
        ${workflowIdArg} \\
        ${inputModeArg} \\
        ${task.cpus}
    """
    }
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
