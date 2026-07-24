#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Standalone plasmid QC workflow.
// Reference-optional plasmid QC supporting all input modes.
//
// Input modes: POD5, BAM, FASTQ
//   POD5: DoradoBasecall → DoradoAlign/BamPrepare → FastqPlasmidQC (if ref)
//   BAM:  BamPrepare → FastqPlasmidQC (if ref)
//   FASTQ: FastqAlign → FastqPlasmidQC

include { DoradoPreflight; DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { DoradoAlign } from '../../modules/ngs/dorado_align.nf'
include { PrepareBamForAnalysis; PrepareReferenceForIGV; BamToFastqForQC as Pod5BamToFastqForQC; BamToFastqForQC as BamInputToFastqForQC } from '../../modules/ngs/bam_prepare.nf'
include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC as Pod5PlasmidQC; FastqPlasmidQC as BamPlasmidQC; FastqPlasmidQC as InputFastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'
include { FastqDimerAnalysis as Pod5DimerAnalysis; FastqDimerAnalysis as BamDimerAnalysis; FastqDimerAnalysis as InputFastqDimerAnalysis; BuildDimerCanonicalOutputs as Pod5DimerCanonicalOutputs; BuildDimerCanonicalOutputs as BamDimerCanonicalOutputs; BuildDimerCanonicalOutputs as InputFastqDimerCanonicalOutputs } from '../../modules/ngs/fastq_dimer_qc.nf'
include { ConstructVerify as Pod5ConstructVerify; ConstructVerify as BamConstructVerify; ConstructVerify as InputFastqConstructVerify } from '../../modules/ngs/construct_verify.nf'

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

workflow ONT_PLASMID_QC {
    main:
    def runFastqQc = params.run_fastq_qc != null
        ? (params.run_fastq_qc != false)
        : (params.run_multimer_qc != false)
    def forceBamRealign = params.bam_force_realign == true

    println("Running ONT plasmid QC workflow")
    println("  POD5 dir:    ${params.pod5_dir ?: '(none)'}")
    println("  BAM path:    ${params.bam_path ?: '(none)'}")
    println("  FASTQ path:  ${params.fastq_path ?: '(none)'}")
    println("  Reference:   ${params.reference_fasta ?: '(none)'}")
    println("  Run QC:      ${runFastqQc}")
    println("  Dorado quality:${params.dorado_quality_mode ?: 'sup'}")

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

    // --- POD5 input: Dorado basecalling + alignment ---
    if (has_pod5) {
        def pod5_input = file(params.pod5_dir)
        if (!pod5_input.exists()) {
            error("POD5 directory not found: ${params.pod5_dir}")
        }

        if (params.barcode_kit && params.barcode_kit.toString().trim()) {
            error("barcoded POD5 must be demultiplexed by ont_basecall_dna before downstream per-barcode submission")
        }
        def pod5_channel = Channel.value(pod5_input)
        DoradoPreflight(pod5_channel)
        DoradoBasecall(pod5_channel, DoradoPreflight.out.manifest)
        DoradoBasecall.out.bam.subscribe { _ignored ->
            reportStage(params, "dorado_basecall", [
                "${params.out_dir}/basecall/calls.bam",
                "${params.out_dir}/basecall/basecall.log",
                "${params.out_dir}/basecall/dorado_preflight.json",
                "${params.out_dir}/basecall/dorado_runtime_provenance.json",
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

            if (runFastqQc) {
                Pod5BamToFastqForQC(DoradoAlign.out.aligned)
                Pod5DimerAnalysis(Pod5BamToFastqForQC.out.fastq, Channel.of(reference_file))
                Pod5DimerCanonicalOutputs(
                    Pod5DimerAnalysis.out.summary,
                    Pod5DimerAnalysis.out.junction_events,
                    Pod5DimerAnalysis.out.single_ref_split_events,
                    Pod5DimerAnalysis.out.single_ref_split_profile,
                    Pod5DimerAnalysis.out.breakpoint_screen,
                    Pod5DimerAnalysis.out.dimer_reference
                )
                Pod5PlasmidQC(DoradoAlign.out.aligned, Channel.of(reference_file), Pod5BamToFastqForQC.out.fastq)
                Pod5ConstructVerify(
                    Pod5PlasmidQC.out.reference,
                    Pod5PlasmidQC.out.verification_input,
                    Pod5PlasmidQC.out.per_base_support,
                    DoradoAlign.out.aligned,
                    Pod5PlasmidQC.out.alignment_stats,
                    Pod5DimerCanonicalOutputs.out.breakpoint_call,
                    Pod5DimerCanonicalOutputs.out.secondary_summary,
                )
                Pod5PlasmidQC.out.summary.subscribe { _ignored ->
                    reportStage(params, "fastq_qc", [
                        "${params.out_dir}/fastq_qc/reads_for_qc.fastq",
                        "${params.out_dir}/fastq_qc/fastq_qc_summary.tsv",
                        "${params.out_dir}/fastq_qc/fastq_alignment_stats.tsv",
                        "${params.out_dir}/fastq_qc/per_base_support.tsv",
                        "${params.out_dir}/fastq_qc/qc_manifest.json",
                        "${params.out_dir}/fastq_qc/igv_report.html",
                        "${params.out_dir}/fastq_qc/fastq_consensus.fasta",
                        "${params.out_dir}/multimer_qc/dimer_breakpoint_call.tsv",
                    ])
                }
            }
        } else {
            PrepareBamForAnalysis(DoradoBasecall.out.bam)
            PrepareBamForAnalysis.out.aligned.subscribe { bam, bai ->
                reportStage(params, "bam_prepare", [
                    "${params.out_dir}/align/aligned.bam",
                    "${params.out_dir}/align/aligned.bam.bai",
                    "${params.out_dir}/align/bam_prepare.log",
                ])
            }
        }
    }

    // --- BAM input: prepare (sort/index) or realign ---
    if (has_bam) {
        def bam_input = file(params.bam_path)
        if (!bam_input.exists()) {
            error("BAM file not found: ${params.bam_path}")
        }

        def analysis_bam = null
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
        }

        if (has_reference) {
            PrepareReferenceForIGV(Channel.of(reference_file))
            PrepareReferenceForIGV.out.log.subscribe { _ignored -> }
        }

        if (has_reference && runFastqQc && analysis_bam != null) {
            BamInputToFastqForQC(analysis_bam)
            BamDimerAnalysis(BamInputToFastqForQC.out.fastq, Channel.of(reference_file))
            BamDimerCanonicalOutputs(
                BamDimerAnalysis.out.summary,
                BamDimerAnalysis.out.junction_events,
                BamDimerAnalysis.out.single_ref_split_events,
                BamDimerAnalysis.out.single_ref_split_profile,
                BamDimerAnalysis.out.breakpoint_screen,
                BamDimerAnalysis.out.dimer_reference
            )
            BamPlasmidQC(analysis_bam, Channel.of(reference_file), BamInputToFastqForQC.out.fastq)
            BamConstructVerify(
                BamPlasmidQC.out.reference,
                BamPlasmidQC.out.verification_input,
                BamPlasmidQC.out.per_base_support,
                analysis_bam,
                BamPlasmidQC.out.alignment_stats,
                BamDimerCanonicalOutputs.out.breakpoint_call,
                BamDimerCanonicalOutputs.out.secondary_summary,
            )
            BamPlasmidQC.out.summary.subscribe { _ignored ->
                reportStage(params, "fastq_qc", [
                    "${params.out_dir}/fastq_qc/reads_for_qc.fastq",
                    "${params.out_dir}/fastq_qc/fastq_qc_summary.tsv",
                    "${params.out_dir}/fastq_qc/fastq_alignment_stats.tsv",
                    "${params.out_dir}/fastq_qc/per_base_support.tsv",
                    "${params.out_dir}/fastq_qc/qc_manifest.json",
                    "${params.out_dir}/fastq_qc/igv_report.html",
                    "${params.out_dir}/fastq_qc/fastq_consensus.fasta",
                    "${params.out_dir}/multimer_qc/dimer_breakpoint_call.tsv",
                ])
            }
        }
    }

    // --- FASTQ input: minimap2 alignment + plasmid QC ---
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

        if (runFastqQc) {
            InputFastqDimerAnalysis(Channel.of(fastq_input), Channel.of(reference_file))
            InputFastqDimerCanonicalOutputs(
                InputFastqDimerAnalysis.out.summary,
                InputFastqDimerAnalysis.out.junction_events,
                InputFastqDimerAnalysis.out.single_ref_split_events,
                InputFastqDimerAnalysis.out.single_ref_split_profile,
                InputFastqDimerAnalysis.out.breakpoint_screen,
                InputFastqDimerAnalysis.out.dimer_reference
            )
            InputFastqPlasmidQC(FastqAlign.out.aligned, Channel.of(reference_file), Channel.of(fastq_input))
            InputFastqConstructVerify(
                InputFastqPlasmidQC.out.reference,
                InputFastqPlasmidQC.out.verification_input,
                InputFastqPlasmidQC.out.per_base_support,
                FastqAlign.out.aligned,
                InputFastqPlasmidQC.out.alignment_stats,
                InputFastqDimerCanonicalOutputs.out.breakpoint_call,
                InputFastqDimerCanonicalOutputs.out.secondary_summary,
            )
            InputFastqPlasmidQC.out.summary.subscribe { _ignored ->
                reportStage(params, "fastq_qc", [
                    "${params.out_dir}/fastq_qc/read_lengths.tsv",
                    "${params.out_dir}/fastq_qc/fastq_qc_summary.tsv",
                    "${params.out_dir}/fastq_qc/fastq_alignment_stats.tsv",
                    "${params.out_dir}/fastq_qc/fastq_coverage.tsv",
                    "${params.out_dir}/fastq_qc/per_base_support.tsv",
                    "${params.out_dir}/fastq_qc/qc_manifest.json",
                    "${params.out_dir}/fastq_qc/igv_report.html",
                    "${params.out_dir}/fastq_qc/fastq_consensus.fasta",
                    "${params.out_dir}/multimer_qc/dimer_breakpoint_call.tsv",
                ])
            }
        }
    }
}

// Entry point for standalone Ont Plasmid Qc workflow
workflow {
    ONT_PLASMID_QC()
}
