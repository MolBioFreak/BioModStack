#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Standalone FASTQ plasmid QC workflow.
// FASTQ-only plasmid QC with alignment, coverage, consensus, per-base support.
//
// Input: FASTQ only
//   FASTQ: FastqAlign → FastqPlasmidQC

include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'
include { FastqDimerAnalysis; BuildDimerCanonicalOutputs } from '../../modules/ngs/fastq_dimer_qc.nf'
include { ConstructVerify } from '../../modules/ngs/construct_verify.nf'
include { ComparisonPanelAttribution } from '../../modules/ngs/comparison_panel_attribution.nf'

def reportStage(params, stageName, files) {
    def jobId = params.containsKey('job_id') ? params.job_id : null
    if (!jobId) return
    try {
        def reportFiles = files.findAll { it != null && it.toString().trim() }
        if (reportFiles.isEmpty()) return
        def args = [jobId.toString(), stageName, "complete"] + reportFiles.collect { it.toString() }
        def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
        def rc = proc.waitFor()
        if (rc != 0) throw new IllegalStateException("Stage reporting failed for ${stageName} (exit ${rc})")
    } catch (Exception e) {
        throw new IllegalStateException("Stage reporting failed for ${stageName}", e)
    }
}

workflow ONT_FASTQ_QC {
    main:
    def runFastqQc = params.run_fastq_qc != null
        ? (params.run_fastq_qc != false)
        : (params.run_multimer_qc != false)

    println("Running ONT FASTQ plasmid QC workflow")
    println("  FASTQ path:  ${params.fastq_path ?: '(none)'}")
    println("  Reference:   ${params.reference_fasta ?: '(none)'}")
    println("  Run QC:      ${runFastqQc}")

    // --- Input validation ---
    def has_fastq = params.fastq_path && params.fastq_path.toString().trim()

    if (!has_fastq) {
        error("FASTQ input is required (--fastq_path)")
    }

    def has_reference = params.reference_fasta && params.reference_fasta.toString().trim()
    def reference_file = null
    if (has_reference) {
        reference_file = file(params.reference_fasta)
        if (!reference_file.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }
    } else {
        error("FASTQ analysis requires --reference_fasta for alignment and QC")
    }

    // Validate minimap2 preset
    def allowed_presets = ['map-ont', 'map-hifi', 'map-pb', 'sr'] as Set
    def preset = (params.fastq_minimap2_preset ?: 'map-ont').toString().trim()
    if (!allowed_presets.contains(preset)) {
        error("Unsupported --fastq_minimap2_preset '${preset}'. Supported: ${allowed_presets.join(', ')}")
    }

    // --- FASTQ input: minimap2 alignment + plasmid QC ---
    def fastq_input = file(params.fastq_path)
    if (!fastq_input.exists()) {
        error("FASTQ file not found: ${params.fastq_path}")
    }

    FastqAlign(Channel.of(fastq_input), Channel.of(reference_file))
    FastqAlign.out.aligned.subscribe { bam, bai ->
        reportStage(params, "fastq_align", [
            "${params.out_dir}/align/aligned.bam",
            "${params.out_dir}/align/aligned.bam.bai",
            "${params.out_dir}/align/reference.fasta",
            "${params.out_dir}/align/reference.fasta.fai",
            "${params.out_dir}/align/fastq_align.log",
        ])
    }

    FastqDimerAnalysis(Channel.of(fastq_input), Channel.of(reference_file))
    BuildDimerCanonicalOutputs(
        FastqDimerAnalysis.out.summary,
        FastqDimerAnalysis.out.junction_events,
        FastqDimerAnalysis.out.single_ref_split_events,
        FastqDimerAnalysis.out.single_ref_split_profile,
        FastqDimerAnalysis.out.breakpoint_screen,
        FastqDimerAnalysis.out.dimer_reference,
    )
    BuildDimerCanonicalOutputs.out.breakpoint_call.subscribe { _ignored ->
        reportStage(params, "dimer_qc", [
            "${params.out_dir}/multimer_qc/dimer_breakpoint_call.tsv",
            "${params.out_dir}/multimer_qc/dimer_evidence_by_position.tsv",
            "${params.out_dir}/multimer_qc/dimer_read_events.tsv",
            "${params.out_dir}/multimer_qc/dimer_breakpoint_sequences.tsv",
            "${params.out_dir}/multimer_qc/dimer_secondary_anomalies.tsv",
            "${params.out_dir}/multimer_qc/dimer_secondary_summary.tsv",
        ])
    }

    if (runFastqQc) {
        FastqPlasmidQC(FastqAlign.out.aligned, Channel.of(reference_file), Channel.of(fastq_input))
        ConstructVerify(
            FastqPlasmidQC.out.reference,
            FastqPlasmidQC.out.verification_input,
            FastqPlasmidQC.out.per_base_support,
            FastqAlign.out.aligned,
            FastqPlasmidQC.out.alignment_stats,
            BuildDimerCanonicalOutputs.out.breakpoint_call,
            BuildDimerCanonicalOutputs.out.secondary_summary,
        )
        ConstructVerify.out.manifest.subscribe { _ignored ->
            reportStage(params, "construct_verification", [
                "${params.out_dir}/verification/qc_manifest.json",
                "${params.out_dir}/verification/verification_summary.tsv",
                "${params.out_dir}/verification/variants.vcf",
                "${params.out_dir}/verification/per_base_metrics.tsv",
                "${params.out_dir}/verification/evidence.html",
                "${params.out_dir}/verification/topology_evidence.json",
            ])
        }
        FastqPlasmidQC.out.summary.subscribe { _ignored ->
            reportStage(params, "fastq_qc", [
                "${params.out_dir}/fastq_qc/read_lengths.tsv",
                "${params.out_dir}/fastq_qc/fastq_qc_summary.tsv",
                "${params.out_dir}/fastq_qc/fastq_alignment_stats.tsv",
                "${params.out_dir}/fastq_qc/fastq_coverage.tsv",
                "${params.out_dir}/fastq_qc/per_base_support.tsv",
                "${params.out_dir}/fastq_qc/qc_manifest.json",
                "${params.out_dir}/fastq_qc/igv_report.html",
                "${params.out_dir}/fastq_qc/fastq_consensus.fasta",
            ])
        }
        if (params.comparison_panel_snapshot && params.comparison_panel_snapshot.toString().trim()) {
            def comparisonSnapshot = file(params.comparison_panel_snapshot)
            if (!comparisonSnapshot.exists()) error("Comparison panel snapshot not found")
            ComparisonPanelAttribution(Channel.of(fastq_input), Channel.of(reference_file), Channel.of(comparisonSnapshot))
            ComparisonPanelAttribution.out.summary.subscribe { _ignored ->
                reportStage(params, "comparison_panel", [
                    "${params.out_dir}/comparison_panel/comparison_panel_summary.json",
                    "${params.out_dir}/comparison_panel/comparison_panel.bam",
                    "${params.out_dir}/comparison_panel/comparison_panel.bam.bai",
                ])
            }
        }
    }
}

// Entry point for standalone FASTQ QC workflow
workflow {
    ONT_FASTQ_QC()
}
