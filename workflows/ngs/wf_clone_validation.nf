#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Standalone plasmid QC workflow with wf-clone-validation assembly.
// Full and total nanopore plasmid QC pipeline.
//
// Input modes: POD5, BAM, FASTQ
//   POD5: DoradoBasecall → DoradoAlign/BamPrepare → CloneValidation
//   BAM:  BamPrepare → CloneValidation
//   FASTQ: FastqAlign → CloneValidation + FastqPlasmidQC

include { DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { DoradoAlign } from '../../modules/ngs/dorado_align.nf'
include { PrepareBamForAnalysis; ValidateMappedBam } from '../../modules/ngs/bam_prepare.nf'
include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'
include { FastqDimerAnalysis; BuildDimerCanonicalOutputs } from '../../modules/ngs/fastq_dimer_qc.nf'
include { RunCloneValidation } from '../../modules/ngs/clone_validation.nf'

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

workflow WF_CLONE_VALIDATION {
    main:
    def runFastqQc = params.run_fastq_qc != null
        ? (params.run_fastq_qc != false)
        : (params.run_multimer_qc != false)
    def forceBamRealign = params.bam_force_realign == true

    println("Running wf-clone-validation (full plasmid QC pipeline)")
    println("  POD5 dir:    ${params.pod5_dir ?: '(none)'}")
    println("  BAM path:    ${params.bam_path ?: '(none)'}")
    println("  FASTQ path:  ${params.fastq_path ?: '(none)'}")
    println("  Reference:   ${params.reference_fasta ?: '(none)'}")
    println("  Assembly:    ${params.wf_clone_assembly_tool ?: 'flye'}")
    println("  Run QC:      ${runFastqQc}")
    println("  Approx size: ${params.wf_clone_approx_size ?: 7000}")
    println("  Dorado model:${params.dorado_model ?: 'sup'}")

    // --- Input validation ---
    def has_pod5 = params.pod5_dir && params.pod5_dir.toString().trim()
    def has_bam = params.bam_path && params.bam_path.toString().trim()
    def has_fastq = params.fastq_path && params.fastq_path.toString().trim()
    def selected_input_count = [has_pod5, has_bam, has_fastq].count { it }

    if (selected_input_count == 0) {
        error("One primary input is required (--pod5_dir or --bam_path or --fastq_path)")
    }
    if (selected_input_count > 1) {
        error("Specify exactly one primary input: --pod5_dir OR --bam_path OR --fastq_path")
    }

    def has_reference = params.reference_fasta && params.reference_fasta.toString().trim()
    def reference_file = null
    if (has_reference) {
        reference_file = file(params.reference_fasta)
        if (!reference_file.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }
    }

    if (has_fastq && !has_reference) {
        error("FASTQ analysis requires --reference_fasta for alignment and QC")
    }

    // Validate minimap2 preset for FASTQ
    if (has_fastq) {
        def allowed_presets = ['map-ont', 'map-hifi', 'map-pb', 'sr'] as Set
        def preset = (params.fastq_minimap2_preset ?: 'map-ont').toString().trim()
        if (!allowed_presets.contains(preset)) {
            error("Unsupported --fastq_minimap2_preset '${preset}'. Supported: ${allowed_presets.join(', ')}")
        }
    }

    def analysis_bam = null

    // --- POD5 input: Dorado basecalling + alignment ---
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
            analysis_bam = DoradoAlign.out.aligned
        } else {
            PrepareBamForAnalysis(DoradoBasecall.out.bam)
            PrepareBamForAnalysis.out.aligned.subscribe { bam, bai ->
                reportStage(params, "bam_prepare", [
                    "${params.out_dir}/align/aligned.bam",
                    "${params.out_dir}/align/aligned.bam.bai",
                    "${params.out_dir}/align/bam_prepare.log",
                ])
            }
            analysis_bam = PrepareBamForAnalysis.out.aligned
        }
    }

    // --- BAM input: prepare (sort/index) or realign ---
    if (has_bam) {
        def bam_input = file(params.bam_path)
        if (!bam_input.exists()) {
            error("BAM file not found: ${params.bam_path}")
        }

        if (has_reference && forceBamRealign) {
            DoradoAlign(Channel.of(bam_input), Channel.of(reference_file))
            DoradoAlign.out.aligned.subscribe { bam, bai ->
                reportStage(params, "dorado_align", [
                    "${params.out_dir}/align/aligned.bam",
                    "${params.out_dir}/align/aligned.bam.bai",
                    "${params.out_dir}/align/align.log",
                ])
            }
            analysis_bam = DoradoAlign.out.aligned
        } else {
            PrepareBamForAnalysis(Channel.of(bam_input))
            PrepareBamForAnalysis.out.aligned.subscribe { bam, bai ->
                reportStage(params, "bam_prepare", [
                    "${params.out_dir}/align/aligned.bam",
                    "${params.out_dir}/align/aligned.bam.bai",
                    "${params.out_dir}/align/bam_prepare.log",
                    has_reference ? "${params.out_dir}/align/reference.fasta" : null,
                    has_reference ? "${params.out_dir}/align/reference.fasta.fai" : null,
                ])
            }
            analysis_bam = PrepareBamForAnalysis.out.aligned

            // Validate mapped reads before assembly
            ValidateMappedBam(analysis_bam)
            analysis_bam = ValidateMappedBam.out.aligned
        }
    }

    // --- FASTQ input: minimap2 alignment ---
    if (has_fastq) {
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
        analysis_bam = FastqAlign.out.aligned
    }

    // --- wf-clone-validation assembly (required for this workflow) ---
    if (analysis_bam == null) {
        error("wf-clone-validation requires BAM-capable output from basecall/align stage")
    }

    println("Running wf-clone-validation assembly stage")
    def clone_input = analysis_bam.map { bam, bai -> [bam, (params.reference_fasta ?: "").toString()] }
    RunCloneValidation(clone_input)
    RunCloneValidation.out.out.subscribe { ignoredValue ->
        reportStage(params, "wf_clone_validation", [
            "${params.out_dir}/assembly/wf_clone_out",
            "${params.out_dir}/assembly/wf_clone.log",
            "${params.out_dir}/assembly/wf_clone_out/wf-clone-validation-report.html",
            "${params.out_dir}/assembly/wf_clone_out/sample_status.txt",
        ])
    }

    // --- FASTQ plasmid QC (only for FASTQ input with reference) ---
    if (has_fastq && runFastqQc) {
        FastqDimerAnalysis(Channel.of(file(params.fastq_path)), Channel.of(reference_file))
        BuildDimerCanonicalOutputs(
            FastqDimerAnalysis.out.summary,
            FastqDimerAnalysis.out.junction_events,
            FastqDimerAnalysis.out.single_ref_split_events,
            FastqDimerAnalysis.out.single_ref_split_profile,
            FastqDimerAnalysis.out.breakpoint_screen,
            FastqDimerAnalysis.out.dimer_reference
        )
        FastqPlasmidQC(FastqAlign.out.aligned, Channel.of(reference_file), Channel.of(file(params.fastq_path)))
        FastqPlasmidQC.out.summary.subscribe { ignoredValue ->
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
    }
}

// Entry point for standalone Wf Clone Validation workflow
workflow {
    WF_CLONE_VALIDATION()
}
