#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Standalone methylation analysis workflow.
// POD5/BAM → Dorado/modkit for modified base detection.
//
// Input modes: POD5, BAM
//   POD5: DoradoBasecall → ModkitPileup + ModkitSummary
//   BAM:  BamPrepare → ModkitPileup + ModkitSummary

include { DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { PrepareBamForAnalysis; ValidateMappedBam; PrepareReferenceForIGV } from '../../modules/ngs/bam_prepare.nf'
include { ModkitPileup } from '../../modules/ngs/modkit_pileup.nf'
include { ModkitSummary } from '../../modules/ngs/modkit_summary.nf'

def reportStage(params, stageName, files) {
    def jobId = params.containsKey('job_id') ? params.job_id : null
    if (!jobId) return
    try {
        def reportFiles = files.findAll { it != null && it.toString().trim() }
        if (reportFiles.isEmpty()) return
        def args = [jobId.toString(), stageName, "complete"] + reportFiles.collect { it.toString() }
        def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
        proc.waitFor()
    } catch (Exception e) {
        println "Warning: Failed to report stage ${stageName}: ${e.message}"
    }
}

workflow ONT_METHYLATION_ANALYSIS {
    main:
    def runModkit = (params.run_modkit != false)

    println("Running ONT methylation analysis workflow")
    println("  POD5 dir:    ${params.pod5_dir ?: '(none)'}")
    println("  BAM path:    ${params.bam_path ?: '(none)'}")
    println("  Reference:   ${params.reference_fasta ?: '(none)'}")
    println("  Modified bases: ${params.modified_bases ?: 'none'}")
    println("  Run modkit:  ${runModkit}")
    println("  Dorado model:${params.dorado_model ?: 'sup'}")

    // --- Input validation ---
    def has_pod5 = params.pod5_dir && params.pod5_dir.toString().trim()
    def has_bam = params.bam_path && params.bam_path.toString().trim()
    def selected_input_count = [has_pod5, has_bam].count { it }

    if (selected_input_count == 0) {
        error("One primary input is required (--pod5_dir or --bam_path)")
    }
    if (selected_input_count > 1) {
        error("Specify exactly one primary input: --pod5_dir OR --bam_path")
    }

    def has_reference = params.reference_fasta && params.reference_fasta.toString().trim()
    def reference_file = null
    if (has_reference) {
        reference_file = file(params.reference_fasta)
        if (!reference_file.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }
    }

    if (runModkit && !has_reference) {
        error("Modkit methylation analysis requires --reference_fasta")
    }

    // --- POD5 input: Dorado basecalling ---
    if (has_pod5) {
        def pod5_input = file(params.pod5_dir)
        if (!pod5_input.exists()) {
            error("POD5 directory not found: ${params.pod5_dir}")
        }

        DoradoBasecall(Channel.of(pod5_input))
        DoradoBasecall.out.bam.subscribe { _ ->
            reportStage(params, "dorado_basecall", [
                "${params.out_dir}/basecall/calls.bam",
                "${params.out_dir}/basecall/basecall.log",
                "${params.out_dir}/basecall/sequencing_summary.tsv",
            ])
        }

        if (has_reference) {
            PrepareReferenceForIGV(Channel.of(reference_file))
            PrepareReferenceForIGV.out.log.subscribe { _ -> }
        }

        if (runModkit) {
            ModkitPileup(DoradoBasecall.out.bam, Channel.of(reference_file))
            ModkitPileup.out.bed.subscribe { _, _ ->
                reportStage(params, "modkit_pileup", [
                    "${params.out_dir}/modkit/methylation.bed",
                    "${params.out_dir}/modkit/modified_sites.tsv",
                    "${params.out_dir}/modkit/modkit_pileup.log",
                ])
            }

            ModkitSummary(DoradoBasecall.out.bam)
            ModkitSummary.out.summary.subscribe { _, _ ->
                reportStage(params, "modkit_summary", [
                    "${params.out_dir}/modkit/summary.tsv",
                    "${params.out_dir}/modkit/modkit_summary.log",
                ])
            }
        }
    }

    // --- BAM input: prepare + modkit ---
    if (has_bam) {
        def bam_input = file(params.bam_path)
        if (!bam_input.exists()) {
            error("BAM file not found: ${params.bam_path}")
        }

        PrepareBamForAnalysis(Channel.of(bam_input))
        PrepareBamForAnalysis.out.aligned.subscribe { _, _ ->
            reportStage(params, "bam_prepare", [
                "${params.out_dir}/align/aligned.bam",
                "${params.out_dir}/align/aligned.bam.bai",
                "${params.out_dir}/align/bam_prepare.log",
                has_reference ? "${params.out_dir}/align/reference.fasta" : null,
                has_reference ? "${params.out_dir}/align/reference.fasta.fai" : null,
            ])
        }

        // Validate mapped reads before modkit
        def prepared_bam = PrepareBamForAnalysis.out.aligned
        ValidateMappedBam(prepared_bam)
        prepared_bam = ValidateMappedBam.out.aligned

        if (has_reference) {
            PrepareReferenceForIGV(Channel.of(reference_file))
            PrepareReferenceForIGV.out.log.subscribe { _ -> }
        }

        if (runModkit) {
            ModkitPileup(prepared_bam, Channel.of(reference_file))
            ModkitPileup.out.bed.subscribe { _, _ ->
                reportStage(params, "modkit_pileup", [
                    "${params.out_dir}/modkit/methylation.bed",
                    "${params.out_dir}/modkit/modified_sites.tsv",
                    "${params.out_dir}/modkit/modkit_pileup.log",
                ])
            }

            ModkitSummary(prepared_bam)
            ModkitSummary.out.summary.subscribe { _, _ ->
                reportStage(params, "modkit_summary", [
                    "${params.out_dir}/modkit/summary.tsv",
                    "${params.out_dir}/modkit/modkit_summary.log",
                ])
            }
        }
    }
}
