#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Standalone RNA basecalling workflow.
// POD5 → BAM via Dorado basecaller with RNA model selection, optional alignment.
//
// Input: POD5 only
//   POD5: DoradoBasecall (RNA model) → DoradoAlign (if ref) / BamPrepare (if no ref)

include { DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { DoradoAlign } from '../../modules/ngs/dorado_align.nf'
include { PrepareBamForAnalysis } from '../../modules/ngs/bam_prepare.nf'

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

workflow ONT_BASECALL_RNA {
    main:
    println("Running ONT RNA basecalling workflow")
    println("  POD5 dir:    ${params.pod5_dir ?: '(none)'}")
    println("  Reference:   ${params.reference_fasta ?: '(none)'}")
    println("  Dorado model:${params.dorado_model ?: 'rna004_sup'}")

    // --- Input validation ---
    def has_pod5 = params.pod5_dir && params.pod5_dir.toString().trim()
    if (!has_pod5) {
        error("POD5 input is required (--pod5_dir)")
    }

    def pod5_input = file(params.pod5_dir)
    if (!pod5_input.exists()) {
        error("POD5 directory not found: ${params.pod5_dir}")
    }

    def has_reference = params.reference_fasta && params.reference_fasta.toString().trim()
    def reference_file = null
    if (has_reference) {
        reference_file = file(params.reference_fasta)
        if (!reference_file.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }
    }

    // --- Dorado basecalling (RNA model) ---
    DoradoBasecall(Channel.of(pod5_input))
    DoradoBasecall.out.bam.subscribe { _ ->
        reportStage(params, "dorado_basecall", [
            "${params.out_dir}/basecall/calls.bam",
            "${params.out_dir}/basecall/basecall.log",
            "${params.out_dir}/basecall/sequencing_summary.tsv",
        ])
    }

    // --- Optional: align or prepare ---
    if (has_reference) {
        DoradoAlign(DoradoBasecall.out.bam, Channel.of(reference_file))
        DoradoAlign.out.aligned.subscribe { _, _ ->
            reportStage(params, "dorado_align", [
                "${params.out_dir}/align/aligned.bam",
                "${params.out_dir}/align/aligned.bam.bai",
                "${params.out_dir}/align/reference.fasta",
                "${params.out_dir}/align/reference.fasta.fai",
                "${params.out_dir}/align/align.log",
            ])
        }
    } else {
        PrepareBamForAnalysis(DoradoBasecall.out.bam)
        PrepareBamForAnalysis.out.aligned.subscribe { _, _ ->
            reportStage(params, "bam_prepare", [
                "${params.out_dir}/align/aligned.bam",
                "${params.out_dir}/align/aligned.bam.bai",
                "${params.out_dir}/align/bam_prepare.log",
            ])
        }
    }
}
