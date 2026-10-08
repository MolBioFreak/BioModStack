#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Standalone methylation analysis workflow.
// POD5/BAM → Dorado/modkit for modified base detection.
//
// Input modes: POD5, BAM
//   POD5: DoradoBasecall → ModkitPileup + ModkitSummary
//   BAM:  BamPrepare → ModkitPileup + ModkitSummary

include { DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { PrepareBamForAnalysis as Pod5PrepareBamForAnalysis; PrepareBamForAnalysis as BamPrepareBamForAnalysis; ValidateMappedBam; PrepareReferenceForIGV } from '../../modules/ngs/bam_prepare.nf'
include { ValidateModifiedBaseBam as Pod5ValidateModifiedBaseBam; ValidateModifiedBaseBam as BamValidateModifiedBaseBam; ModkitPileup as Pod5ModkitPileup; ModkitPileup as BamModkitPileup } from '../../modules/ngs/modkit_pileup.nf'
include { ModkitSummary as Pod5ModkitSummary; ModkitSummary as BamModkitSummary } from '../../modules/ngs/modkit_summary.nf'

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
        DoradoBasecall.out.bam.subscribe { ignoredValue ->
            reportStage(params, "dorado_basecall", [
                "${params.out_dir}/basecall/calls.bam",
                "${params.out_dir}/basecall/basecall.log",
                "${params.out_dir}/basecall/sequencing_summary.tsv",
            ])
        }

        if (has_reference) {
            PrepareReferenceForIGV(Channel.of(reference_file))
            PrepareReferenceForIGV.out.log.subscribe { ignoredValue -> }
        }

        if (runModkit) {
            Pod5PrepareBamForAnalysis(DoradoBasecall.out.bam)
            Pod5ValidateModifiedBaseBam(Pod5PrepareBamForAnalysis.out.aligned)
            Pod5ModkitPileup(Pod5ValidateModifiedBaseBam.out.bam, Channel.of(reference_file))
            Pod5ModkitPileup.out.bed.subscribe { bed, bedgz ->
                reportStage(params, "modkit_pileup", [
                    "${params.out_dir}/methylation/methylation.bed",
                    "${params.out_dir}/methylation/modified_sites.tsv",
                    "${params.out_dir}/methylation/modkit_pileup.log",
                    "${params.out_dir}/methylation/modified_base_tag_check.log",
                ])
            }

            Pod5ModkitSummary(Pod5ValidateModifiedBaseBam.out.bam)
            Pod5ModkitSummary.out.summary.subscribe { summary, ignoredValue ->
                reportStage(params, "modkit_summary", [
                    "${params.out_dir}/methylation/summary.tsv",
                    "${params.out_dir}/methylation/modkit_summary.log",
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

        BamPrepareBamForAnalysis(Channel.of(bam_input))
        BamPrepareBamForAnalysis.out.aligned.subscribe { bam, bai ->
            reportStage(params, "bam_prepare", [
                "${params.out_dir}/align/aligned.bam",
                "${params.out_dir}/align/aligned.bam.bai",
                "${params.out_dir}/align/bam_prepare.log",
                has_reference ? "${params.out_dir}/align/reference.fasta" : null,
                has_reference ? "${params.out_dir}/align/reference.fasta.fai" : null,
            ])
        }

        // Validate mapped reads before modkit
        def prepared_bam = BamPrepareBamForAnalysis.out.aligned
        ValidateMappedBam(prepared_bam)
        prepared_bam = ValidateMappedBam.out.aligned

        if (has_reference) {
            PrepareReferenceForIGV(Channel.of(reference_file))
            PrepareReferenceForIGV.out.log.subscribe { ignoredValue -> }
        }

        if (runModkit) {
            BamValidateModifiedBaseBam(prepared_bam)
            BamModkitPileup(BamValidateModifiedBaseBam.out.bam, Channel.of(reference_file))
            BamModkitPileup.out.bed.subscribe { bed, bedgz ->
                reportStage(params, "modkit_pileup", [
                    "${params.out_dir}/methylation/methylation.bed",
                    "${params.out_dir}/methylation/modified_sites.tsv",
                    "${params.out_dir}/methylation/modkit_pileup.log",
                    "${params.out_dir}/methylation/modified_base_tag_check.log",
                ])
            }

            BamModkitSummary(BamValidateModifiedBaseBam.out.bam)
            BamModkitSummary.out.summary.subscribe { summary, ignoredValue ->
                reportStage(params, "modkit_summary", [
                    "${params.out_dir}/methylation/summary.tsv",
                    "${params.out_dir}/methylation/modkit_summary.log",
                ])
            }
        }
    }
}

// Entry point for standalone Ont Methylation Analysis workflow
workflow {
    ONT_METHYLATION_ANALYSIS()
}
