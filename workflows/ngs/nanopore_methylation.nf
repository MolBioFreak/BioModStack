#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { DoradoAlign } from '../../modules/ngs/dorado_align.nf'
include { PrepareBamForAnalysis; ValidateMappedBam; PrepareReferenceForIGV } from '../../modules/ngs/bam_prepare.nf'
include { ModkitPileup } from '../../modules/ngs/modkit_pileup.nf'
include { ModkitSummary } from '../../modules/ngs/modkit_summary.nf'
include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'
include { RunCloneValidation } from '../../modules/ngs/clone_validation.nf'

def reportNanoporeStage(params, stageName, outputs) {
    def jobId = params.containsKey('job_id') ? params.job_id : null
    if (!jobId) {
        return
    }
    try {
        def reportFiles = outputs
            .findAll { output -> output != null }
            .collect { output -> output.toString() }
        if (reportFiles.isEmpty()) {
            return
        }
        def args = [jobId.toString(), stageName, "complete"] + reportFiles
        def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
        proc.waitFor()
    }
    catch (Exception error) {
        println "Warning: Failed to report stage ${stageName}: ${error.message}"
    }
}

workflow NANOPORE_METHYLATION {
    main:
    def runFastqQc = params.run_fastq_qc != null
        ? (params.run_fastq_qc != false)
        : (params.run_multimer_qc != false)
    def forceBamRealign = params.bam_force_realign == true

    println("Running Nanopore Methylation Workflow")
    println("* POD5 dir: ${params.pod5_dir}")
    println("* BAM path: ${params.bam_path}")
    println("* BAM force realign: ${forceBamRealign}")
    println("* BAM min MAPQ: ${(params.bam_min_mapq ?: 0)}")
    println("* FASTQ path: ${params.fastq_path}")
    if (params.fastq_path && params.fastq_path.toString().trim()) {
        println("* FASTQ minimap2 preset: ${(params.fastq_minimap2_preset ?: 'map-ont')}")
        println("* FASTQ keep secondary alignments: ${(params.fastq_minimap2_allow_secondary == true)}")
    }
    println("* Dorado model: ${params.dorado_model ?: 'sup'}")
    println("* Modified bases: ${params.modified_bases ?: 'none'}")
    println("* Run modkit: ${params.run_modkit != false}")
    println("* Run FASTQ plasmid QC: ${runFastqQc}")
    println("* Run assembly: ${params.run_assembly ?: false}")

    def has_pod5 = params.pod5_dir && params.pod5_dir.toString().trim()
    def has_bam = params.bam_path && params.bam_path.toString().trim()
    def has_fastq = params.fastq_path && params.fastq_path.toString().trim()
    def selected_input_count = [has_pod5, has_bam, has_fastq].count { it }
    if (selected_input_count == 0) {
        error("One primary input is required for nanopore_methylation mode (--pod5_dir or --bam_path or --fastq_path)")
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
        error("FASTQ analysis requires --reference_fasta for alignment, consensus, and plasmid QC outputs")
    }

    if (has_fastq) {
        def allowed_fastq_minimap_presets = ['map-ont', 'map-hifi', 'map-pb', 'sr'] as Set
        def fastq_minimap_preset = (params.fastq_minimap2_preset ?: 'map-ont').toString().trim()
        if (!allowed_fastq_minimap_presets.contains(fastq_minimap_preset)) {
            error("Unsupported --fastq_minimap2_preset '${fastq_minimap_preset}'. Bundled minimap2 2.24 supports: ${allowed_fastq_minimap_presets.join(', ')}")
        }
    }

    def analysis_bam = null

    if (has_pod5) {
        def pod5_input = file(params.pod5_dir)
        if (!pod5_input.exists()) {
            error("POD5 directory not found: ${params.pod5_dir}")
        }

        DoradoBasecall(Channel.of(pod5_input))
        DoradoBasecall.out.bam.subscribe { ignoredBam ->
            reportNanoporeStage(params, "dorado_basecall", [
                "${params.out_dir}/basecall/calls.bam",
                "${params.out_dir}/basecall/basecall.log",
                "${params.out_dir}/basecall/sequencing_summary.tsv",
            ])
        }

        if (has_reference) {
            DoradoAlign(
                DoradoBasecall.out.bam,
                Channel.of(reference_file)
            )
            DoradoAlign.out.aligned.subscribe { alignedBam, alignedBai ->
                reportNanoporeStage(params, "dorado_align", [
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
            PrepareBamForAnalysis.out.aligned.subscribe { preparedBam, preparedBai ->
                reportNanoporeStage(params, "bam_prepare", [
                    "${params.out_dir}/align/aligned.bam",
                    "${params.out_dir}/align/aligned.bam.bai",
                    "${params.out_dir}/align/bam_prepare.log",
                ])
            }
            analysis_bam = PrepareBamForAnalysis.out.aligned
        }
    }

    if (has_bam) {
        def bam_input = file(params.bam_path)
        if (!bam_input.exists()) {
            error("BAM file not found: ${params.bam_path}")
        }

        if (has_reference && forceBamRealign) {
            DoradoAlign(
                Channel.of(bam_input),
                Channel.of(reference_file)
            )
            DoradoAlign.out.aligned.subscribe { alignedBam, alignedBai ->
                reportNanoporeStage(params, "dorado_align", [
                    "${params.out_dir}/align/aligned.bam",
                    "${params.out_dir}/align/aligned.bam.bai",
                    "${params.out_dir}/align/reference.fasta",
                    "${params.out_dir}/align/reference.fasta.fai",
                    "${params.out_dir}/align/align.log",
                ])
            }
            analysis_bam = DoradoAlign.out.aligned
        } else {
            PrepareBamForAnalysis(Channel.of(bam_input))
            PrepareBamForAnalysis.out.aligned.subscribe { preparedBam, preparedBai ->
                reportNanoporeStage(params, "bam_prepare", [
                    "${params.out_dir}/align/aligned.bam",
                    "${params.out_dir}/align/aligned.bam.bai",
                    "${params.out_dir}/align/bam_prepare.log",
                    has_reference ? "${params.out_dir}/align/reference.fasta" : null,
                    has_reference ? "${params.out_dir}/align/reference.fasta.fai" : null,
                    has_reference ? "${params.out_dir}/align/reference_prepare.log" : null,
                ])
            }

            analysis_bam = PrepareBamForAnalysis.out.aligned

            if (has_reference) {
                PrepareReferenceForIGV(Channel.of(reference_file))
                PrepareReferenceForIGV.out.log.subscribe { ignoredLog -> }
            }

            if (params.run_modkit != false || params.run_assembly) {
                ValidateMappedBam(analysis_bam)
                analysis_bam = ValidateMappedBam.out.aligned
            }
        }
    }

    if (has_fastq) {
        def fastq_input = file(params.fastq_path)
        if (!fastq_input.exists()) {
            error("FASTQ file not found: ${params.fastq_path}")
        }

        FastqAlign(
            Channel.of(fastq_input),
            Channel.of(reference_file)
        )
        FastqAlign.out.aligned.subscribe { alignedBam, alignedBai ->
            reportNanoporeStage(params, "fastq_align", [
                "${params.out_dir}/align/aligned.bam",
                "${params.out_dir}/align/aligned.bam.bai",
                "${params.out_dir}/align/reference.fasta",
                "${params.out_dir}/align/reference.fasta.fai",
                "${params.out_dir}/align/fastq_align.log",
            ])
        }
        analysis_bam = FastqAlign.out.aligned

        if (runFastqQc) {
            FastqPlasmidQC(
                FastqAlign.out.aligned,
                Channel.of(reference_file),
                Channel.of(fastq_input)
            )
            FastqPlasmidQC.out.summary.subscribe { qcSummary ->
                reportNanoporeStage(params, "fastq_qc", [
                    "${params.out_dir}/fastq_qc/read_lengths.tsv",
                    "${params.out_dir}/fastq_qc/fastq_qc_summary.tsv",
                    "${params.out_dir}/fastq_qc/fastq_alignment_stats.tsv",
                    "${params.out_dir}/fastq_qc/fastq_coverage.tsv",
                    "${params.out_dir}/fastq_qc/per_base_support.tsv",
                    "${params.out_dir}/fastq_qc/qc_manifest.json",
                    "${params.out_dir}/fastq_qc/reference_qc.fasta",
                    "${params.out_dir}/fastq_qc/reference_qc.fasta.fai",
                    "${params.out_dir}/fastq_qc/igv_coverage_depth.bedgraph",
                    "${params.out_dir}/fastq_qc/igv_position_gradient.bedgraph",
                    "${params.out_dir}/fastq_qc/igv_gc_content.bedgraph",
                    "${params.out_dir}/fastq_qc/igv_gc_zscore.bedgraph",
                    "${params.out_dir}/fastq_qc/igv_split_read_density.bedgraph",
                    "${params.out_dir}/fastq_qc/igv_softclip_density.bedgraph",
                    "${params.out_dir}/fastq_qc/igv_junction_hotspots.bed",
                    "${params.out_dir}/fastq_qc/igv_report_sites.bed",
                    "${params.out_dir}/fastq_qc/igv_report_sites.tsv",
                    "${params.out_dir}/fastq_qc/igv_track_config.json",
                    "${params.out_dir}/fastq_qc/igv_report.html",
                    "${params.out_dir}/fastq_qc/igv_report.log",
                    "${params.out_dir}/fastq_qc/fastq_consensus.fasta",
                    "${params.out_dir}/fastq_qc/fastq_consensus.fasta.fai",
                    "${params.out_dir}/fastq_qc/fastq_consensus.log",
                    "${params.out_dir}/fastq_qc/fastq_qc.log",
                ])
            }
        } else {
            println("Skipping FASTQ plasmid QC (run_fastq_qc=false)")
        }
    }

    if (params.run_modkit != false && analysis_bam != null && (has_pod5 || has_bam)) {
        if (has_reference) {
            ModkitPileup(
                analysis_bam,
                Channel.of(reference_file)
            )
        } else {
            println("No reference_fasta provided: running modkit summary only (pileup skipped)")
        }

        ModkitSummary(analysis_bam)
        ModkitSummary.out.summary.subscribe { summaryFile ->
            reportNanoporeStage(params, "modkit", [
                has_reference ? "${params.out_dir}/methylation/methylation.bed" : null,
                has_reference ? "${params.out_dir}/methylation/pileup.log" : null,
                "${params.out_dir}/methylation/modkit_summary.tsv",
                "${params.out_dir}/methylation/summary.log",
            ])
        }
    } else if (params.run_modkit == false) {
        println("Skipping modkit analysis (run_modkit=false)")
    }

    if (params.run_assembly) {
        if (analysis_bam == null) {
            error("run_assembly requires BAM-capable input (--pod5_dir or --bam_path)")
        }
        println("Running wf-clone-validation assembly stage")
        def clone_input = analysis_bam.map { bam, _bai -> [bam, (params.reference_fasta ?: "").toString()] }
        RunCloneValidation(clone_input)
        RunCloneValidation.out.out.subscribe { cloneValidationOutput ->
            reportNanoporeStage(params, "wf_clone_validation", [
                "${params.out_dir}/assembly/wf_clone_out",
                "${params.out_dir}/assembly/wf_clone.log",
                "${params.out_dir}/assembly/wf_clone_out/wf-clone-validation-report.html",
                "${params.out_dir}/assembly/wf_clone_out/sample_status.txt",
            ])
        }
    }
}
