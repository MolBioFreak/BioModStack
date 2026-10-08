#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Standalone DNA basecalling workflow.
// POD5 → BAM via Dorado basecaller, optional alignment.
//
// Input: POD5 only
//   POD5: DoradoBasecall → optional DoradoAlign; unreferenced output remains an unaligned BAM.

include { DoradoPreflight; DoradoBasecall; DoradoDemux } from '../../modules/ngs/dorado_basecall.nf'
include { DoradoAlign } from '../../modules/ngs/dorado_align.nf'


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

workflow ONT_BASECALL_DNA {
    main:
    println("Running ONT DNA basecalling workflow")
    println("  POD5 dir:    ${params.pod5_dir ?: '(none)'}")
    println("  Reference:   ${params.reference_fasta ?: '(none)'}")
    println("  Dorado quality:${params.dorado_quality_mode ?: 'sup'}")

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

    // --- Dorado basecalling ---
    def pod5_channel = Channel.value(pod5_input)
    DoradoPreflight(pod5_channel)
    DoradoBasecall(pod5_channel, DoradoPreflight.out.manifest)
    DoradoBasecall.out.bam.subscribe { ignoredValue ->
        reportStage(params, "dorado_basecall", [
            "${params.out_dir}/basecall/calls.bam",
            "${params.out_dir}/basecall/basecall.log",
            "${params.out_dir}/basecall/dorado_preflight.json",
            "${params.out_dir}/basecall/dorado_runtime_provenance.json",
        ])
    }

    // --- Optional: demultiplex classified reads, or align/prepare unbarcoded reads ---
    def is_barcoded = params.barcode_kit && params.barcode_kit.toString().trim()
    if (is_barcoded) {
        DoradoDemux(DoradoBasecall.out.bam, DoradoBasecall.out.preflight)
    } else if (has_reference) {
        DoradoAlign(DoradoBasecall.out.bam, Channel.of(reference_file))
        DoradoAlign.out.aligned.subscribe { bam, bai ->
            reportStage(params, "dorado_align", [
                "${params.out_dir}/align/aligned.bam",
                "${params.out_dir}/align/aligned.bam.bai",
                "${params.out_dir}/align/reference.fasta",
                "${params.out_dir}/align/reference.fasta.fai",
                "${params.out_dir}/align/align.log",
            ])
        }
    }
}

// Entry point for standalone Ont Basecall Dna workflow
workflow {
    ONT_BASECALL_DNA()
}

// publishDir is asynchronous with respect to process-output subscriptions.
// Anchor terminal demux products only after Nextflow has completed publication.
workflow.onComplete {
    def is_barcoded = params.barcode_kit && params.barcode_kit.toString().trim()
    if (workflow.success && is_barcoded) {
        reportStage(params, "dorado_demux", [
            "${params.out_dir}/demux/demux_manifest.json",
            "${params.out_dir}/demux/per_barcode_units.json",
            "${params.out_dir}/demux/demux/units",
        ])
    }
}
