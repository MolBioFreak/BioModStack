#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { RFDiffusionWorkflow } from './workflows/rfdiffusion.nf'
include { FilterRFD ; RunRFDiffusion } from './modules/rfdiffusion.nf'
include { PrepRFD3Input ; RunRFD3 ; FilterRFD3 } from './modules/rfd3.nf'
include { RunRF3 ; FilterRF3 } from './modules/rf3.nf'
include { PrepFAMPNN ; FilterFAMPNN ; RunFAMPNN } from './modules/fampnn.nf'
include { IdentifyAnchorResidues ; RunPartialFlow ; PrepMaturationRedesign ; RunMaturationFAMPNN ; ScoreMaturationImprovement ; ScorePartialFlowImprovement ; FilterByMaturation } from './modules/ppiflow.nf'
include { FilterMPNN ; PrepMPNN ; RunMPNN } from './modules/proteinmpnn.nf'
include { AlignAF2 ; FilterAF2 ; RunAF2 } from './modules/af2.nf'
include { AnalyseBestDesigns } from './modules/analysis.nf'
include { PublishResults } from './modules/publish.nf'
include { AlignBoltz ; FilterBoltz ; PrepBoltz ; RunBoltz } from './modules/boltz.nf'
include { PrepBoltzGenInput ; RunBoltzGen ; FilterBoltzGen ; SpawnBoltzGenJobs ; WaitForBoltzGenChildren ; CollectBoltzGenOutputs ; AggregateBoltzGenResults } from './modules/boltzgen.nf'
include { PrepDiffDock ; RunDiffDock ; FilterDiffDock } from './modules/diffdock.nf'
include { PrepUniDock ; RunUniDock ; FilterUniDock } from './modules/unidock.nf'
include { CombineMetadata } from './modules/combine_metadata.nf'
include { Compress as CompressRFD } from './modules/compress'
include { Compress as CompressMPNN } from './modules/compress'
include { Compress as CompressFAMPNN } from './modules/compress'
include { Compress as CompressAF2 } from './modules/compress'
include { Compress as CompressBoltz } from './modules/compress'
include { MergeUncroppedTarget } from './modules/merge_uncropped_target.nf'
include { BoltzFromSequence } from './modules/structure_prediction.nf'
include { PrepareComplexWithMSA ; BoltzFromComplex } from './modules/structure_prediction.nf'
include { RF3FromSequence } from './modules/structure_prediction.nf'
include { structure_prediction_wf ; complex_prediction_wf } from './modules/structure_prediction.nf'
include { OpenMMRelaxation ; OpenMMScore } from './modules/openmm.nf'
include { ANARCII } from './modules/utils/anarci'
include { FrustrampnnQC ; AggregateFrustrationReports } from './modules/frustrampnn.nf'
include { DoradoBasecall ; DoradoAlign ; PrepareBamForAnalysis ; ValidateMappedBam ; PrepareReferenceForIGV ; ModkitPileup ; ModkitSummary ; FastqAlign ; FastqPlasmidQC ; RunCloneValidation } from './modules/dorado.nf'

include { OLIGO_DESIGNER } from './workflows/oligo_design.nf'

include { ANTIBODY_DESIGN } from './workflows/antibody_design.nf'

include { ANTIBODY_DENOVO } from './workflows/antibody_denovo.nf'

include { ANTIBODY_CHILD } from './workflows/antibody_child.nf'

include { BINDCRAFT_DESIGN } from './workflows/bindcraft_design.nf'

include { RFANTIBODY } from './modules/rfantibody'

workflow {
    try {
        nextflow.preview.topic = true
    }
    catch (Exception e) {
    }

    def outputDirectory = params.out_dir

    if (params.run_rfd_only && (params.skip_rfd_seq || params.skip_rfd_seq_pred)) {
        error("Cannot use --run_rfd_only with skip flags --skip_rfd_seq or --skip_rfd_seq_pred. These options are contradictory.")
    }
    if (params.run_rfd_only && params.skip_rfd) {
        error("Cannot use --run_rfd_only with --skip_rfd. These options are contradictory.")
    }

    def num_batches = Math.min(params.gpus, params.rfd_num_designs).intValue()
    def batch_size = Math.ceil(params.rfd_num_designs / num_batches).intValue()
    def num_designs = num_batches * batch_size

    println("Pipeline Mode: ${params.rfd_mode}")
    println("Number of RFdiffusion designs: ${num_designs}")
    println("Number of sequences for each design: ${params.seqs_per_design}")
    println("Output Directory: ${outputDirectory}")


    def configDir = file("${outputDirectory}/configs")
    configDir.mkdirs()
    workflow.configFiles.each { configFile ->
        configFile.copyTo("${configDir}/${configFile.getName()}")
    }

    def inputsDir = file("${outputDirectory}/inputs")
    inputsDir.mkdirs()

    if (params.nanopore_enabled || params.rfd_mode == 'nanopore_methylation') {
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
            println("* FASTQ minimap2 preset: ${(params.fastq_minimap2_preset ?: 'lr:hq')}")
            println("* FASTQ keep secondary alignments: ${(params.fastq_minimap2_allow_secondary == true)}")
        }
        println("* Dorado model: ${params.dorado_model ?: 'sup'}")
        println("* Modified bases: ${params.modified_bases ?: 'none'}")
        println("* Run modkit: ${params.run_modkit != false}")
        println("* Run FASTQ plasmid QC: ${runFastqQc}")
        println("* Run assembly: ${params.run_assembly ?: false}")

        def reportNanoporeStage = { String stageName, List outputs ->
            if (!params.job_id) {
                return
            }
            try {
                def reportFiles = outputs
                    .findAll { it != null }
                    .collect { it.toString() }
                if (reportFiles.isEmpty()) {
                    return
                }
                def args = [params.job_id.toString(), stageName, "complete"] + reportFiles
                def proc = ["python3", "${params.code_root}/scripts/stage_reporter.py", *args].execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage ${stageName}: ${e.message}"
            }
        }

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

        def analysis_bam = null

        if (has_pod5) {
            def pod5_input = file(params.pod5_dir)
            if (!pod5_input.exists()) {
                error("POD5 directory not found: ${params.pod5_dir}")
            }

            DoradoBasecall(Channel.of(pod5_input))
            DoradoBasecall.out.bam.subscribe { _ ->
                reportNanoporeStage("dorado_basecall", [
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
                DoradoAlign.out.aligned.subscribe { _bam, _bai ->
                    reportNanoporeStage("dorado_align", [
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
                PrepareBamForAnalysis.out.aligned.subscribe { _bam, _bai ->
                    reportNanoporeStage("bam_prepare", [
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
                DoradoAlign.out.aligned.subscribe { _bam, _bai ->
                    reportNanoporeStage("dorado_align", [
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
                PrepareBamForAnalysis.out.aligned.subscribe { _bam, _bai ->
                    reportNanoporeStage("bam_prepare", [
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
                    PrepareReferenceForIGV.out.log.subscribe { _ -> }
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
            FastqAlign.out.aligned.subscribe { _bam, _bai ->
                reportNanoporeStage("fastq_align", [
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
                FastqPlasmidQC.out.summary.subscribe { _ ->
                    reportNanoporeStage("fastq_qc", [
                        "${params.out_dir}/fastq_qc/read_lengths.tsv",
                        "${params.out_dir}/fastq_qc/fastq_qc_summary.tsv",
                        "${params.out_dir}/fastq_qc/fastq_alignment_stats.tsv",
                        "${params.out_dir}/fastq_qc/fastq_coverage.tsv",
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
            ModkitSummary.out.summary.subscribe { _ ->
                reportNanoporeStage("modkit", [
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
            RunCloneValidation.out.out.subscribe { _ ->
                reportNanoporeStage("wf_clone_validation", [
                    "${params.out_dir}/assembly/wf_clone_out",
                    "${params.out_dir}/assembly/wf_clone.log",
                    "${params.out_dir}/assembly/wf_clone_out/wf-clone-validation-report.html",
                    "${params.out_dir}/assembly/wf_clone_out/sample_status.txt",
                ])
            }
        }

        return null
    }

    /////////////////////////////
    // ANTIBODY CHILD JOB      //
    /////////////////////////////
    // Single design validation job spawned by parent in exploration mode
    // Single or Batch design validation job spawned by parent
    if (params.rfd_mode == 'antibody_child') {
        println("Running Antibody Child Validation Job")

        def pdb_list = []
        if (params.pdb_paths) {
            // Parse batch list (comes as string "[path1, path2]" or "path1,path2")
            def clean = params.pdb_paths.toString().replace('[', '').replace(']', '').split(',')
            pdb_list = clean.collect { it.strip().replaceAll(/['"]/, '') }.findAll { it }.collect { file(it) }
            println("* Mode: Batch (${pdb_list.size()} designs)")
        }
        else if (params.pdb_path) {
            // Legacy single mode
            pdb_list = [file(params.pdb_path)]
            println("* Mode: Single (${params.pdb_path})")
        }
        else {
            error("No PDB inputs provided for antibody_child mode")
        }

        ANTIBODY_CHILD(
            pdb_list,
            params.msa_path ?: "",
        )

        return null
    }

    /////////////////////////////
    // BINDCRAFT DE NOVO       //
    /////////////////////////////
    // BindCraft minibinder design workflow
    if (params.rfd_mode == 'bindcraft') {
        println("Running BindCraft De Novo Binder Design")
        println("* Target PDB: ${params.bindcraft_target_pdb}")
        println("* Hotspots: ${params.bindcraft_hotspot_residues ?: 'auto-detect'}")
        println("* Binder lengths: ${params.bindcraft_binder_lengths}")
        println("* Algorithm: ${params.bindcraft_design_algorithm}")
        println("* Final designs: ${params.bindcraft_num_final_designs}")

        if (!params.bindcraft_target_pdb) {
            error("Target PDB required for bindcraft mode")
        }

        BINDCRAFT_DESIGN()

        return null
    }

    // BindCraft child job (spawned by SWA parent)
    if (params.rfd_mode == 'bindcraft_child') {
        println("Running BindCraft Child Job (SWA)")
        println("* Child index: ${params.child_index}")
        println("* Trajectories: ${params.bindcraft_num_final_designs}")

        // Child runs directly without further spawning
        BINDCRAFT_DESIGN()

        return null
    }

    /////////////////////////////
    // OLIGO DESIGNER          //
    /////////////////////////////
    // Multi-polymer design (DNA, RNA, protein) using RFDpoly
    if (params.rfd_mode == 'oligo_design' || params.rfdpoly_enabled) {
        println("Running Oligo Designer (RFDpoly + Boltz-2)")
        println("* Contigs: ${params.rfdpoly_contigs}")
        println("* Polymer chains: ${params.rfdpoly_polymer_chains}")
        println("* Num designs: ${params.rfdpoly_num_designs}")
        println("* Checkpoint: ${params.rfdpoly_checkpoint}")
        println("* Noise schedule: ${params.rfdpoly_noise_schedule ?: 'linear'}")
        println("* Binding guidance: ${params.binding_guidance ?: false}")
        println("* Validate with Boltz: ${params.oligo_validate_boltz}")
        if (params.target_pdb) {
            println("* Target PDB: ${params.target_pdb}")
        }

        // Prepare scaffold PDB channel (optional)
        def input_pdb = params.scaffold_pdb 
            ? channel.fromPath(params.scaffold_pdb)
            : (params.rfdpoly_input_pdb
                ? channel.fromPath(params.rfdpoly_input_pdb)
                : channel.of(file("${params.code_root}/NO_FILE")))

        // Prepare target PDB channel (optional, for protein-binding aptamer mode)
        def target_pdb = params.target_pdb 
            ? channel.fromPath(params.target_pdb)
            : channel.of(file("${params.code_root}/NO_FILE"))

        OLIGO_DESIGNER(
            channel.of(params.design_id ?: 'oligo_design'),
            channel.of(params.rfdpoly_contigs),
            channel.of(params.rfdpoly_polymer_chains),
            input_pdb,
            target_pdb
        )

        return null
    }

    /////////////////////////////
    // RFANTIBODY STANDALONE    //
    /////////////////////////////
    // Standalone RFantibody backbone generation for orchestrator-spawned child jobs
    if (params.rfd_mode == 'rfantibody_backbone') {
        println("Running RFantibody Backbone Generation (Child Job)")
        println("* Target PDB: ${params.target_pdb}")
        println("* Epitope: ${params.epitope_residues}")
        println("* Num designs: ${params.rfantibody_num_designs}")
        println("* GPU: ${params.gpu_id}")

        if (!params.target_pdb) {
            error("Target PDB required for rfantibody_backbone mode")
        }

        def jobName = params.sequence_name ?: 'rfantibody_child'
        def meta = [id: jobName]

        // Prepare input tuple: [meta, target_pdb, hotspots, gpu_id, num_designs]
        def hotspots = params.epitope_residues ?: ""
        def gpu_id = params.gpu_id ?: 0
        def rfantibody_num_designs = params.rfantibody_num_designs ?: 10

        def rfantibody_input = channel.of(
            tuple(meta, file(params.target_pdb), hotspots, gpu_id, rfantibody_num_designs)
        )

        // Use framework from params or dummy file for default
        def framework_for_rfantibody = params.framework_pdb
            ? file(params.framework_pdb)
            : file("${params.code_root}/lib/NO_FRAMEWORK")

        RFANTIBODY(rfantibody_input, framework_for_rfantibody)

        return null
    }

    /////////////////////////////
    // FAMPNN CHILD JOB        //
    /////////////////////////////
    // Standalone FAMPNN sequence design for orchestrator-spawned child jobs
    if (params.rfd_mode == 'fampnn_child') {
        println("Running FAMPNN Sequence Design (Child Job)")
        println("* PDB paths: ${params.pdb_paths}")
        println("* Seqs per design: ${params.seqs_per_design}")
        println("* GPU: ${params.gpu_id}")

        if (!params.pdb_paths) {
            error("PDB paths required for fampnn_child mode")
        }

        // Parse PDB paths (comes as comma-separated string)
        def pdb_paths_raw = params.pdb_paths.toString()
        def pdb_list = pdb_paths_raw.split(',').collect { it.strip().replaceAll(/[\[\]'"]/, '') }.findAll { it }.collect { file(it) }

        if (pdb_list.isEmpty()) {
            error("No valid PDB files found in pdb_paths: ${params.pdb_paths}")
        }

        println("* Processing ${pdb_list.size()} PDBs")

        // Prepare FAMPNN input - PrepFAMPNN expects tuple [pdbs, jsons]
        fampnn_prep_input = Channel.of(tuple(pdb_list, file("${params.code_root}/lib/NO_JSON")))

        PrepFAMPNN(fampnn_prep_input)

        // RunFAMPNN expects tuple [batch_id, pdbs, csv, gpu_id], analysis_chain_id
        // Build input by joining PrepFAMPNN outputs with gpu_id
        def gpu_id_val = params.gpu_id ?: 0

        // Collect PDFs as-is (they're already in a collection from the glob)
        // PrepFAMPNN.out.pdbs emits path objects matching the glob
        // Use collect to group them, then merge with CSV
        fampnn_run_input = PrepFAMPNN.out.pdbs
            .collect()
            .merge(PrepFAMPNN.out.csv)
            .map { collected_items ->
                // collected_items is [List<Path>, Path] from merge
                def pdbs = collected_items[0]
                // First is the collected PDBs list
                def csv = collected_items[1]
                // Second is the CSV
                tuple(0, pdbs, csv, gpu_id_val)
            }

        RunFAMPNN(fampnn_run_input, params.analysis_chain_id ?: 'all_chains')

        // Optional filtering
        def filterEnabled = params.enable_fampnn_filter != false && (params.fampnn_max_psce != null || params.fampnn_max_residue_psce != null)

        if (filterEnabled) {
            println("  Filtering FAMPNN designs...")
            FilterFAMPNN(RunFAMPNN.out.pdbs_jsons)
        }

        println("FAMPNN child job complete")
        return null
    }

    /////////////////////////////
    // PPIFlow MATURATION CHILD //
    /////////////////////////////
    if (params.rfd_mode == 'maturation_child') {
        println("Running PPIFlow Maturation (Child Job)")
        println("* PDB paths: ${params.pdb_paths}")
        println("* GPU: ${params.gpu_id}")

        if (!params.pdb_paths) {
            error("PDB paths required for maturation_child mode")
        }

        def pdb_paths_raw = params.pdb_paths.toString()
        def pdb_list = pdb_paths_raw.split(',').collect { it.strip().replaceAll(/[\[\]'"]/, '') }.findAll { it }.collect { file(it) }

        if (pdb_list.isEmpty()) {
            error("No valid PDB files found in pdb_paths: ${params.pdb_paths}")
        }

        println("* Processing ${pdb_list.size()} PDBs")

        def anchor_inputs = Channel.from(pdb_list).map { pdb ->
            def meta = [id: pdb.baseName]
            tuple(meta, pdb)
        }

        IdentifyAnchorResidues(anchor_inputs)
        RunPartialFlow(IdentifyAnchorResidues.out.anchor_inputs)

        def anchor_lookup = IdentifyAnchorResidues.out.anchor_inputs
            .map { meta, original_pdb, anchors_json, cdr_positions ->
                tuple(meta, original_pdb, anchors_json, cdr_positions)
            }
        def anchor_original_lookup = anchor_lookup
            .map { meta, original_pdb, anchors_json, cdr_positions -> tuple(meta, original_pdb) }
        def anchor_redesign_lookup = anchor_lookup
            .map { meta, original_pdb, anchors_json, cdr_positions -> tuple(meta, anchors_json, cdr_positions) }

        def partial_score_inputs = RunPartialFlow.out.backbones.join(anchor_original_lookup)
            .map { meta, backbone_pdb, original_pdb ->
                tuple(meta, original_pdb, backbone_pdb)
            }

        ScorePartialFlowImprovement(partial_score_inputs)

        // ANARCII loop positions for selective redesign (uses IMGT numbering)
        ANARCII(RunPartialFlow.out.backbones)
        def cdr_loop_lookup = ANARCII.out.cdr_positions
            .map { meta, cdr_positions_by_loop -> tuple(meta, cdr_positions_by_loop) }

        def partial_scored = ScorePartialFlowImprovement.out.scores
            .join(RunPartialFlow.out.backbones)
            .map { meta, score_json, backbone_pdb ->
                def score = 0.0
                try {
                    score = new groovy.json.JsonSlurper().parse(score_json).delta_interface_score ?: 0.0
                } catch (Exception e) {
                    score = 0.0
                }
                tuple(meta, backbone_pdb, score_json, score)
            }

        def redesign_top_n = params.maturation_redesign_top_n ?: 0
        def partial_selected = partial_scored
        if (redesign_top_n > 0) {
            partial_selected = partial_scored.collect().flatMap { items ->
                def sorted = items.sort { a, b -> a[3] <=> b[3] }
                return sorted.take(redesign_top_n)
            }
        }

        def redesign_enabled = params.maturation_redesign_enabled != false

        def final_matured
        def final_scores

        if (redesign_enabled) {
            def redesign_inputs = partial_selected
                .map { meta, backbone_pdb, score_json, score -> tuple(meta, backbone_pdb) }
                .join(anchor_redesign_lookup.join(cdr_loop_lookup)
                    .map { meta, anchors_json, cdr_positions, cdr_positions_by_loop ->
                        tuple(meta, anchors_json, cdr_positions, cdr_positions_by_loop)
                    }
                )
                .map { meta, backbone_pdb, anchors_json, cdr_positions, cdr_positions_by_loop ->
                    tuple(meta, backbone_pdb, anchors_json, cdr_positions, cdr_positions_by_loop)
                }

            PrepMaturationRedesign(redesign_inputs)
            RunMaturationFAMPNN(PrepMaturationRedesign.out.prep)

            final_matured = RunMaturationFAMPNN.out.redesigned.map { meta, matured_pdb, matured_json ->
                tuple(meta, matured_pdb)
            }

            def score_inputs = RunMaturationFAMPNN.out.redesigned.join(anchor_original_lookup)
                .map { meta, matured_pdb, matured_json, original_pdb ->
                    tuple(meta, original_pdb, matured_pdb)
                }

            ScoreMaturationImprovement(score_inputs)
            final_scores = ScoreMaturationImprovement.out.scores
        } else {
            final_matured = partial_selected.map { meta, backbone_pdb, score_json, score -> tuple(meta, backbone_pdb) }
            final_scores = ScorePartialFlowImprovement.out.scores
        }

        def filter_inputs = final_scores.join(final_matured)
            .map { meta, score_json, matured_pdb ->
                tuple(meta, matured_pdb, score_json)
            }

        FilterByMaturation(filter_inputs)

        println("PPIFlow maturation child job complete")
        return null
    }

    /////////////////////////////
    // ANTIBODY DESIGN STACK   //
    /////////////////////////////
    if (params.rfd_mode in ['structure_prediction', 'inverse_folding', 'stability_prediction', 'de_novo', 'antibody_denovo_pipeline']) {
        println("Running Antibody Design Toolkit")
        println("* Mode: ${params.rfd_mode}")

        def jobName = params.sequence_name ?: 'antibody_job'
        def meta = [id: jobName]
        def input_ch

        // Mode-specific input preparation
        if (params.rfd_mode == 'structure_prediction') {
            def h_seq = params.heavy_sequence ?: ''
            def l_seq = params.light_sequence ?: ''
            if (!h_seq && !l_seq) {
                error("Must provide heavy or light sequence")
            }

            def fastaContent = ""
            if (h_seq) {
                fastaContent += ">H\n${h_seq}\n"
            }
            if (l_seq) {
                fastaContent += ">L\n${l_seq}\n"
            }

            input_ch = channel.of(fastaContent)
                .collectFile(name: 'input.fasta')
                .map { tuple(meta, it) }
        }
        else if (params.rfd_mode == 'de_novo') {
            if (!params.target_pdb) {
                error("Antigen PDB required for de novo design")
            }
            def antigenChains = params.antigen_chains ?: "A"
            input_ch = channel.of(tuple(meta, file(params.target_pdb), antigenChains))
        }
        else if (params.rfd_mode == 'antibody_denovo_pipeline') {
            // Full de novo antibody design pipeline 
            // RFantibody -> FAMPNN/AntiFold -> Boltz2 -> AntiBERTy -> IgGM
            if (!params.target_pdb) {
                error("Antigen PDB required for antibody_denovo_pipeline")
            }
            def epitope = params.epitope_residues ?: ""
            input_ch = channel.of(tuple(meta, file(params.target_pdb)))

            // Framework is optional
            def framework_ch = params.framework_pdb
                ? channel.of(tuple(meta, file(params.framework_pdb)))
                : channel.empty()

            // Call de novo antibody workflow
            ANTIBODY_DENOVO(input_ch, epitope, framework_ch)

            return null
        }
        else {
            // inverse_folding, stability_prediction
            if (!params.target_pdb) {
                error("Target PDB required for ${params.rfd_mode}")
            }
            input_ch = channel.of(tuple(meta, file(params.target_pdb)))
        }

        // Call unified subworkflow (for non-pipeline modes)
        ANTIBODY_DESIGN(input_ch, params.rfd_mode)

        return null
    }

    ///////////////////////////////////
    // COMPLEX-BASED STRUCTURE PRED  //
    ///////////////////////////////////

    // If complex_json_path is provided, run complex prediction (multi-chain + ligands)
    if (params.complex_json_path) {
        def numParallelJobs = params.num_parallel_jobs ?: 1
        println("Running complex-based structure prediction (multi-chain + ligands)")
        println("* Complex definition: ${params.complex_json_path}")
        println("* Predictor: ${params.pred_method ?: 'boltz'}")
        println("* Number of simulations: ${numParallelJobs}")

        def complex_name = params.sequence_name ?: 'complex_pred'
        def complex_json = file(params.complex_json_path)

        // Create parallel job channels
        def job_indices = Channel.from(0..<numParallelJobs)
        def msa_file = params.msa_path ? file(params.msa_path) : file("${params.code_root}/NO_MSA")
        def complex_ch = job_indices.map { idx ->
            def jobName = numParallelJobs > 1 ? "${complex_name}_job${idx}" : complex_name
            tuple(jobName, complex_json, msa_file)
        }

        // Centralized routing — dispatches based on params.pred_method
        complex_prediction_wf(complex_ch)

        complex_prediction_wf.out.structures
            .flatten()
            .collect()
            .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
            .set { final_pdbs }

        // Optional post-run FrustraMPNN QC for complex prediction
        if (params.run_frustrampnn == true) {
            println("Running FrustraMPNN post-analysis on complex predictions")
            def frustra_input = final_pdbs
                .flatten()
                .map { pdb -> tuple([id: pdb.baseName], pdb) }
            FrustrampnnQC(frustra_input)
            AggregateFrustrationReports(FrustrampnnQC.out.summary.map { meta, summary -> summary }.collect())
        }

        // Skip all other stages for complex prediction
        return null
    }

    ///////////////////////////
    // UNI-DOCK STANDALONE   //
    ///////////////////////////

    // Uni-Dock standalone docking mode (activated by unidock profile or params)
    if (params.unidock_ligand_smiles || params.unidock_ntp_type) {
        println("Running Uni-Dock standalone docking")
        println("* Receptor: ${params.skip_input_dir}")
        println("* Ligand SMILES: ${params.unidock_ligand_smiles ?: 'N/A'}")
        println("* NTP Type: ${params.unidock_ntp_type ?: 'N/A'}")
        println("* Box Size: ${params.unidock_box_size}Å")
        println("* Exhaustiveness: ${params.unidock_exhaustiveness}")

        // Get receptor PDB(s) from input dir
        def inputPath = file(params.skip_input_dir)
        if (!inputPath.exists()) {
            error("Receptor PDB not found at: ${params.skip_input_dir}")
        }

        def receptor_pdbs = inputPath.isFile() ? [inputPath] : inputPath.listFiles().findAll { it.name.endsWith('.pdb') }
        if (receptor_pdbs.isEmpty()) {
            error("No PDB files found in: ${params.skip_input_dir}")
        }

        // Create channel from receptor PDBs
        def receptor_ch = Channel.from(receptor_pdbs)

        // Prepare Uni-Dock inputs
        PrepUniDock(
            receptor_ch,
            params.unidock_ligand_smiles ?: '',
            params.unidock_ntp_type ?: '',
            params.unidock_box_size,
            params.unidock_box_center ?: '',
            params.unidock_flexible_residues ?: '',
        )

        // Run Uni-Dock
        def flex_receptor = PrepUniDock.out.flex_receptor.ifEmpty(file('NO_FLEX'))

        def unidock_input = PrepUniDock.out.receptor
            .combine(flex_receptor)
            .combine(PrepUniDock.out.ligand_dir)
            .combine(PrepUniDock.out.box)
            .map { receptor, flex, ligands, box ->
                tuple("unidock_0", receptor, flex, ligands, box)
            }

        RunUniDock(unidock_input)

        // Filter results
        FilterUniDock(RunUniDock.out.poses.collect(), RunUniDock.out.scores)

        println("Uni-Dock docking complete. Results in: ${params.out_dir}/run/unidock")
        return null
    }

    ///////////////////////////////////
    // SEQUENCE-BASED STRUCTURE PRED //
    ///////////////////////////////////

    // If sequence_input is provided, run sequence-based prediction only
    if (params.sequence_input) {
        def numParallelJobs = params.num_parallel_jobs ?: 1
        println("Running sequence-based structure prediction")
        println("* Sequence: ${params.sequence_input.take(50)}...")
        println("* Predictor: ${params.pred_method ?: 'boltz'}")
        println("* Parallel jobs: ${numParallelJobs}")

        def seq_name = params.sequence_name ?: 'predicted'

        // Create a channel with job indices for parallel execution
        // Each job gets a unique name suffix (job_0, job_1, etc.)
        def job_indices = Channel.from(0..<numParallelJobs)

        // Create sequence channel that pairs with each job index
        def parallel_jobs_ch = job_indices.map { idx ->
            def jobName = numParallelJobs > 1 ? "${seq_name}_job${idx}" : seq_name
            tuple(params.sequence_input, jobName)
        }

        if (params.pred_method in ['boltz', 'rf3', 'both', 'protenix', 'all']) {
            // Use the unified workflow which handles all predictors, MSA generation, and tuple inputs
            structure_prediction_wf(parallel_jobs_ch)

            structure_prediction_wf.out.structures
                .flatten()
                .collect()
                .set { final_pdbs }
        }
        else {
            // Unknown pred_method — route through workflow anyway (will use defaults)
            structure_prediction_wf(parallel_jobs_ch)
            structure_prediction_wf.out.structures.flatten().collect().set { final_pdbs }
        }

        // Optional post-run FrustraMPNN QC
        if (params.run_frustrampnn == true) {
            def frustra_input = final_pdbs
                .flatten()
                .map { pdb -> tuple([id: pdb.baseName], pdb) }
            FrustrampnnQC(frustra_input)
            // Extract just the path from (meta, path) tuples before collecting
            AggregateFrustrationReports(FrustrampnnQC.out.summary.map { meta, summary -> summary }.collect())
        }

        // Skip all other stages for sequence-only prediction
        return null
    }

    ///////////////////////
    // FOLD DESIGN STAGE //
    ///////////////////////

    // Run RFdiffusion if not skipped
    if (!params.skip_rfd & !params.skip_rfd_seq & !params.skip_rfd_seq_pred & params.diffusion_method != 'boltzgen') {
        // Check if num_designs has been provided
        if (!params.rfd_num_designs) {
            error("Please provide the number of designs for RFdiffusion to generate")
        }

        // Route based on diffusion method
        if (params.diffusion_method == "rfd3") {
            // RFdiffusion3 via Foundry container
            println("Using RFdiffusion3 (Foundry) for structure generation")

            // Collect input files
            def inputFiles = collectInputFiles(params)
            inputFiles.each { inputFile ->
                "rsync -r ${inputFile} ${inputsDir}/.".execute()
            }

            // Create JSON input for RFD3
            def rfd3_input_ch = Channel.of(
                [
                    params.rfd_mode,
                    params.rfd_contigs ?: '[100-100]',
                    params.rfd_input_pdb ? file(params.rfd_input_pdb) : file("${params.code_root}/lib/NO_FILE"),
                    params.rfd_hotspots ?: '',
                    params.rfd_num_designs,
                    0,
                ]
            )

            // Prepare RFD3 JSON input
            PrepRFD3Input(rfd3_input_ch)

            // Run RFD3 in batches
            RunRFD3(PrepRFD3Input.out.input_json)

            // Set output channels (RFD3 outputs CIF, downstream expects PDB - may need conversion)
            RunRFD3.out.structures_metadata.set { rfd_pdbs_jsons }

            // Batch for CPU filtering
            Utils
                .rebatchTuples(rfd_pdbs_jsons, 200)
                .set { rfd_tuples }

            // Filter RFD3 outputs
            FilterRFD3(rfd_tuples)

            if (params.run_rfd_only) {
                FilterRFD3.out.structures_metadata
                    .flatten()
                    .collect()
                    .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
                    .set { final_pdbs }
            }
            else {
                FilterRFD3.out.structures_metadata.set { filt_rfd_pdbs_jsons }
            }
        }
        else {
            // Legacy RFdiffusion path (DEPRECATED - use rfd3 instead)
            log.warn("DEPRECATION WARNING: diffusion_method='rfd' is deprecated. Consider using 'rfd3' (RFdiffusion3) instead.")
            def rfdParams = new RFDiffusionParams(params)
            def rfdCommand = rfdParams.generateCommandString()
            log.info("RFdiffusion command: ${rfdCommand} inference.num_designs=${batch_size}")

            def inputFiles = collectInputFiles(params)
            inputFiles.each { inputFile ->
                "rsync -r ${inputFile} ${inputsDir}/.".execute()
            }

            RFDiffusionWorkflow(
                rfdCommand,
                params.rfd_num_designs,
                batch_size,
                params.rfd_mode,
                inputFiles,
            )

            RFDiffusionWorkflow.out.pdbs_jsons.set { rfd_pdbs_jsons }
            CompressRFD("rfd", rfd_pdbs_jsons.flatten().collect())

            Utils
                .rebatchTuples(rfd_pdbs_jsons, 200)
                .set { rfd_tuples }
            FilterRFD(rfd_tuples)

            if (params.run_rfd_only) {
                FilterRFD.out.pdbs_jsons
                    .flatten()
                    .collect()
                    .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
                    .set { final_pdbs }
            }
            else {
                FilterRFD.out.pdbs_jsons.set { filt_rfd_pdbs_jsons }
            }
        }
    }
    else if (params.diffusion_method == "boltzgen") {
        // BoltzGen Workflow (Replaces Backbone + Sequence stages)
        // Can be run standalone or as part of pipeline

        println("Using BoltzGen for all-atom binder generation")

        // Prepare input config
        PrepBoltzGenInput(
            params.boltzgen_ligand_smiles ?: '',
            params.boltzgen_ntp_type ?: '',
            params.boltzgen_scaffold_length,
            params.boltzgen_num_designs,
            params.boltzgen_binding_site_residues ?: '',
            params.boltzgen_catalytic_site ?: false,
            params.boltzgen_protein_sequence ?: '',
            params.boltzgen_dna_template_seq ?: '',
            params.boltzgen_dna_primer_seq ?: '',
            params.boltzgen_secondary_structure ?: '',
            params.boltzgen_protocol ?: 'protein-anything',
            params.boltzgen_covalent_bonds ?: '',
            params.boltzgen_nanobody_framework ?: '',
            params.boltzgen_cdr_h1_length ?: '5-8',
            params.boltzgen_cdr_h2_length ?: '6-10',
            params.boltzgen_cdr_h3_length ?: '12-18',
            params.boltzgen_input_pdb ? file(params.boltzgen_input_pdb) : file("${params.code_root}/lib/NO_INPUT_PDB"),
            params.boltzgen_ligand_pdb ? file(params.boltzgen_ligand_pdb) : file("${params.code_root}/lib/NO_LIGAND_PDB"),
            params.boltzgen_dna_structure ? file(params.boltzgen_dna_structure) : file("${params.code_root}/lib/NO_DNA_STRUCT"),
            params.boltzgen_target_pdb_path ? file(params.boltzgen_target_pdb_path) : file("${params.code_root}/lib/NO_TARGET_PDB"),
        )

        // =========================================================================
        // PARALLEL MODE: Use SWA pattern for large campaigns
        // =========================================================================
        def parallel_mode_set = params.containsKey('parallel_mode')
        def use_orchestrator = parallel_mode_set
            ? (params.parallel_mode == 'full_orchestrator')
            : (params.boltzgen_parallel_mode == true)
        if (use_orchestrator) {
            println("BoltzGen PARALLEL MODE: Spawning ${Math.ceil(params.boltzgen_num_designs / params.boltzgen_designs_per_job)} child jobs")

            def target_pdb = params.boltzgen_target_pdb_path ? file(params.boltzgen_target_pdb_path) : file("${params.code_root}/lib/NO_TARGET_PDB")

            // Spawn child jobs via API
            SpawnBoltzGenJobs(
                params.job_id ?: 'unknown',
                params.boltzgen_num_designs,
                params.boltzgen_designs_per_job ?: 100,
                PrepBoltzGenInput.out.yaml,
                target_pdb,
                params.boltzgen_mode ?: 'nanobody_binder',
                params.name ?: 'boltzgen_campaign',
            )

            // Wait for all children to complete
            WaitForBoltzGenChildren(
                params.job_id ?: 'unknown',
                SpawnBoltzGenJobs.out.result,
            )

            // Collect outputs from children
            CollectBoltzGenOutputs(WaitForBoltzGenChildren.out.result)

            // Aggregate and ingest results
            AggregateBoltzGenResults(
                params.job_id ?: 'unknown',
                CollectBoltzGenOutputs.out.pdbs.collect(),
                CollectBoltzGenOutputs.out.jsons.collect(),
                CollectBoltzGenOutputs.out.manifest,
            )

            // Set final outputs
            CollectBoltzGenOutputs.out.pdbs
                .flatten()
                .collect()
                .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
                .set { final_pdbs }

            // Set empty channels for downstream
            rfd_tuples = Channel.empty()
            filt_rfd_pdbs_jsons = Channel.empty()
            filt_seq_pdbs = Channel.empty()
            analysis_input_pdbs = Channel.empty()
        }
        else {
            // Run generation
            RunBoltzGen(PrepBoltzGenInput.out.yaml)

            // Filter designs
            FilterBoltzGen(RunBoltzGen.out.pdbs, RunBoltzGen.out.jsons)

            // Branching logic
            if (params.run_boltzgen_only) {
                println("BoltzGen standalone mode complete. Exiting.")
                // Assign to final_pdbs for publishing
                FilterBoltzGen.out.pdbs
                    .flatten()
                    .collect()
                    .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
                    .set { final_pdbs }

                // Set other channels to empty/defaults to avoid errors
                rfd_tuples = Channel.empty()
                filt_rfd_pdbs_jsons = Channel.empty()
                filt_seq_pdbs = Channel.empty()
                analysis_input_pdbs = Channel.empty()

                // Skip downstream stages
                params.skip_rfd_seq = true
                params.skip_rfd_seq_pred = true
            }
            else {
                // Pipeline mode: Output flows into Stage 3 (Prediction) or Stage 4 (Docking)
                // We set filt_seq_pdbs so it gets picked up by prediction stage if active
                FilterBoltzGen.out.pdbs
                    .flatten()
                    .collect()
                    .set { filt_seq_pdbs }

                // Also set analysis_input_pdbs for analysis stage (BoltzGen skips prediction)
                FilterBoltzGen.out.pdbs
                    .flatten()
                    .collect()
                    .set { analysis_input_pdbs }

                // Set RFD/Seq channels empty since we skipped them
                rfd_tuples = Channel.empty()
                filt_rfd_pdbs_jsons = Channel.empty()
            }
        }
    }
    else if (params.skip_rfd & !params.skip_rfd_seq & !params.skip_rfd_seq_pred) {
        // Skip RFDiffusion and use existing PDBs and JSONs from specified directory
        println("Skipping RFDiffusion stage as skip_rfd=true.")
        println("Running Sequence Design, Prediction, and Analysis stages only.")
        println("Looking for PDBs and JSONs in: ${params.skip_input_dir}")
        // Check if directory exists
        if (!file(params.skip_input_dir).exists()) {
            throw new FileNotFoundException("Skip input file directory not found at: ${params.skip_input_dir}. Please ensure the path is correct.")
        }
        def previous_pdbs = file("${params.skip_input_dir}").listFiles().findAll { it.name.endsWith('.pdb') }
        def previous_jsons = file("${params.skip_input_dir}").listFiles().findAll { it.name.endsWith('.json') }
        // Error handling for missing files
        if (previous_pdbs.isEmpty()) {
            throw new FileNotFoundException("No PDB files found in directory: ${params.skip_input_dir}. Please provide PDB files to proceed with the workflow.")
        }
        if (previous_jsons.isEmpty()) {
            throw new FileNotFoundException("No JSON files found in directory: ${params.skip_input_dir}. Please provide JSON files to proceed with the workflow.")
        }
        println("Found ${previous_pdbs.size()} PDB files")
        println("Found ${previous_jsons.size()} JSON files\n")

        // Copy PDB and JSON files from the previous results directory to inputs directory
        previous_pdbs.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }
        previous_jsons.each { jsonFile ->
            jsonFile.copyTo("${inputsDir}/${jsonFile.getName()}")
        }

        // Create channel with PDB-JSON tuples from specified directory
        Channel
            .of([previous_pdbs, previous_jsons])
            .set { rfd_pdbs_jsons }
        // Batch RFD PDBs and JSONS for CPU tasks
        Utils
            .rebatchTuples(rfd_pdbs_jsons, 200)
            .set { filt_rfd_pdbs_jsons }
    }
    else {
        println("Skipping RFDiffusion stage as skip_rfd_seq=true or skip_rfd_seq_pred=true.")
    }
    ///////////////////////////
    // SEQUENCE DESIGN STAGE //
    ///////////////////////////
    // Run Sequence Design if not skipped
    // BoltzGen already performs sequence generation internally.
    if (params.diffusion_method == 'boltzgen') {
        println("Skipping Sequence Design stage for BoltzGen diffusion output.")
    }
    else if (!params.skip_rfd_seq & !params.skip_rfd_seq_pred & !params.run_rfd_only) {
        // Sequence design (either MPNN or FAMPNN)
        if (params.seq_method == "mpnn") {
            // Add FIXED labels to PDBs for target residues so the sequence does not change
            PrepMPNN(filt_rfd_pdbs_jsons)

            // Method specific batching
            if (params.mpnn_relax_max_cycles > 0) {
                // use smaller batches for fast relax (slow)
                PrepMPNN.out.pdbs
                    .collect()
                    .flatten()
                    .buffer(size: 2, remainder: true)
                    .set { seq_input_pdbs }
            }
            else {
                // use larger batches without fast relax
                PrepMPNN.out.pdbs
                    .collect()
                    .flatten()
                    .buffer(size: 10, remainder: true)
                    .set { seq_input_pdbs }
            }

            // Launch ProteinMPNN
            RunMPNN(seq_input_pdbs)

            // Compress output files
            CompressMPNN("mpnn", RunMPNN.out.pdbs_jsons.flatten().collect())

            // Rebatch sequence assignment files for CPU Filtering Step
            Utils
                .rebatchTuples(RunMPNN.out.pdbs_jsons, 200)
                .set { seq_tuple }

            // Filter designs by sequence score
            FilterMPNN(seq_tuple)
            FilterMPNN.out.pdbs
                .flatten()
                .collect()
                .set { filt_seq_pdbs }
        }
        else if (params.seq_method == "fampnn") {
            // FAMPNN path
            // Rebatch files for Prep Step
            Utils
                .rebatchTuples(filt_rfd_pdbs_jsons, 10)
                .set { fampnn_prep_input_tuple }

            // Restore side-chains to RFD output and prepare CSV file with fixed residues
            PrepFAMPNN(fampnn_prep_input_tuple)
            PrepFAMPNN.out.csv
                .collectFile(name: 'merged_results.csv', keepHeader: true)
                .set { mega_csv }

            // GPU-aware batching for RunFAMPNN
            // rebatchGPU returns [batch_id, files] tuples
            Utils
                .rebatchGPU(PrepFAMPNN.out.pdbs, params.gpus)
                .set { fampnn_pdbs }

            // Add CSV path and GPU ID to PDB channel
            // Use default GPU 0 for legacy mode, or parse from pinned_gpus
            def default_gpu = params.pinned_gpus ? params.pinned_gpus.toString().split(',')[0].trim().toInteger() : (params.gpu_id ?: 0)
            fampnn_pdbs
                .combine(mega_csv)
                .map { batch_id, pdbs, csv -> [batch_id, pdbs, csv, default_gpu] }
                .set { fampnn_input }

            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
                // Perform design and scoring on binder (chain A)
                RunFAMPNN(fampnn_input, 'A')
            }
            else {
                // Perform design and scoring on all chains
                RunFAMPNN(fampnn_input, 'all_chains')
            }

            // Compress output files
            CompressFAMPNN("fampnn", RunFAMPNN.out.pdbs_jsons.flatten().collect())

            // Rebatch sequence assignment files for CPU Filtering Step
            Utils
                .rebatchTuples(RunFAMPNN.out.pdbs_jsons, 200)
                .set { seq_tuple }

            // Filter designs by sequence score
            FilterFAMPNN(seq_tuple)
            FilterFAMPNN.out.pdbs
                .flatten()
                .collect()
                .set { filt_seq_pdbs }
        }
        else {
            error("Not a valid sequence assignment method")
        }
    }
    else if (!params.skip_rfd_seq_pred & !params.run_rfd_only) {
        // Skip sequence design and run prediction using existing PDBs from specified path
        println("Skipping Sequence Design stage as skip_rfd_seq=true.")
        println("Running Prediction and Analysis stages only.")
        println("Looking for PDBs in: ${params.skip_input_dir}")

        // Check if input exists
        def inputPath = file(params.skip_input_dir)
        if (!inputPath.exists()) {
            throw new FileNotFoundException("Skip input path not found at: ${params.skip_input_dir}. Please ensure the path is correct.")
        }

        // Handle both single file and directory inputs
        def pdbs_for_pred = []
        if (inputPath.isFile()) {
            // Single file input
            if (inputPath.name.endsWith('.pdb')) {
                pdbs_for_pred = [inputPath]
                println("Using single PDB file: ${inputPath.name}")
            }
            else {
                throw new FileNotFoundException("Input file must be a .pdb file: ${params.skip_input_dir}")
            }
        }
        else {
            // Directory input
            pdbs_for_pred = inputPath.listFiles().findAll { it.name.endsWith('.pdb') }
            if (pdbs_for_pred.isEmpty()) {
                throw new FileNotFoundException("No PDB files found in directory: ${params.skip_input_dir}. Please provide PDB files to proceed with the workflow.")
            }
            println("Found ${pdbs_for_pred.size()} PDB files in directory")
        }

        // Copy PDB files from the input to inputs directory
        pdbs_for_pred.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }

        // Create channel with PDBs from specified path
        Channel
            .of(pdbs_for_pred)
            .set { filt_seq_pdbs }
    }
    else if (params.skip_rfd_seq_pred) {
        println("Skipping Sequence Design stage as skip_rfd_seq_pred=true.")
    }
    else {
        println("Skipping Sequence Design stage as run_rfd_only=true.")
    }
    ////////////////////////////////
    // STRUCTURE PREDICTION STAGE //
    ////////////////////////////////
    // Run Structure Prediction if not skipped
    // BoltzGen includes internal structure prediction; keep its outputs as analysis inputs.
    if (!params.skip_rfd_seq_pred && !params.run_rfd_only && !params.skip_pred && params.diffusion_method != 'boltzgen') {
        // Optional uncropped target PDB merge for binder design
        if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
            // if uncropped target PDB file is provided, merge with designs
            if (params.uncropped_target_pdb) {
                def uncroppedPDBfile = file(params.uncropped_target_pdb)
                if (!uncroppedPDBfile.exists()) {
                    throw new FileNotFoundException("Uncropped target PDB file not found at path: ${params.uncropped_target_pdb}. Please ensure the file exists and the path is correct.")
                }
                MergeUncroppedTarget(filt_seq_pdbs, uncroppedPDBfile).set { pred_input_pdbs }
            }
            else {
                filt_seq_pdbs.set { pred_input_pdbs }
            }
        }
        else {
            filt_seq_pdbs.set { pred_input_pdbs }
        }
        // Structure Prediction (either AlphaFold2 Initial-Guess or Boltz-2)
        if (params.pred_method == "af2") {

            // reallocate batching for GPU
            Utils
                .rebatchGPUByNumRes(pred_input_pdbs, params.gpus)
                .set { pred_input_tuple }

            // AlphaFold2-Initial Guess
            RunAF2(pred_input_tuple)

            // Compress output files
            CompressAF2("af2", RunAF2.out.pdbs_jsons.flatten().collect())

            // Batch files for CPUs
            Utils
                .rebatchTuples(RunAF2.out.pdbs_jsons, 200)
                .set { af2_tuple }

            // Filtering of AF2 results
            FilterAF2(af2_tuple)

            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
                // Alignment of PDBs to target chain(s). Only need one reference file
                AlignAF2(FilterAF2.out.pdbs.flatten().collect(), pred_input_pdbs.flatten().last())
                AlignAF2.out.pdbs
                    .flatten()
                    .collect()
                    .set { analysis_input_pdbs }
            }
            else {
                FilterAF2.out.pdbs
                    .flatten()
                    .collect()
                    .set { analysis_input_pdbs }
            }
        }
        else if (params.pred_method == "boltz") {
            // Prep yaml files for Boltz-2
            PrepBoltz(pred_input_pdbs)

            // reallocate batching for GPU
            Utils
                .rebatchGPU(PrepBoltz.out.yamls, params.gpus)
                .set { pred_input_tuple }

            // Perform prediction of designs using Boltz-2
            RunBoltz(pred_input_tuple)

            // Batch files for CPUs
            Utils
                .rebatchTuples(RunBoltz.out.pdbs_jsons, 200)
                .set { boltz_tuple }

            // Align Boltz Predictions to FAMPNN output and calculate RMSD
            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
                AlignBoltz(boltz_tuple, filt_seq_pdbs, 'binder')
            }
            else {
                AlignBoltz(boltz_tuple, filt_seq_pdbs, 'monomer')
            }
            // Compress output files
            CompressBoltz("boltz", AlignBoltz.out.pdbs_jsons.flatten().collect())

            // Filtering of Boltz-2 results
            FilterBoltz(AlignBoltz.out.pdbs_jsons)
            FilterBoltz.out.pdbs
                .flatten()
                .collect()
                .set { analysis_input_pdbs }
        }
        else if (params.pred_method == "rf3") {
            // RosettaFold3 prediction via Foundry container
            println("Using RosettaFold3 (Foundry) for structure prediction")

            // reallocate batching for GPU
            Utils
                .rebatchGPU(pred_input_pdbs, params.gpus)
                .set { pred_input_tuple }

            // Run RF3 prediction
            RunRF3(pred_input_tuple)

            // Batch files for CPUs
            Utils
                .rebatchTuples(RunRF3.out.structures_metadata, 200)
                .set { rf3_tuple }

            // Filter RF3 predictions
            FilterRF3(rf3_tuple)
            FilterRF3.out.structures
                .flatten()
                .collect()
                .set { analysis_input_pdbs }
        }
        else {
            error("Not a valid structure prediction method. Choose from: af2, boltz, rf3, protenix")
        }
    }
    else if (!params.run_rfd_only) {
        // Skip prediction and run analysis only using existing PDBs from specified path
        println("Skipping Structure Prediction stage as skip_rfd_seq_pred=true.")
        println("Running Analysis Stage only")
        println("Looking for PDBs in: ${params.skip_input_dir}")
        def inputPath = file(params.skip_input_dir)
        if (!inputPath.exists()) {
            throw new FileNotFoundException("Skip input file path not found at: ${params.skip_input_dir}. Please ensure the path is correct.")
        }

        // Handle both single file and directory inputs
        def pdbs_for_analysis = []
        if (inputPath.isFile()) {
            if (inputPath.name.endsWith('.pdb')) {
                pdbs_for_analysis = [inputPath]
                println("Using single PDB file: ${inputPath.name}")
            }
            else {
                throw new IllegalArgumentException("Input file must be a .pdb file: ${params.skip_input_dir}")
            }
        }
        else {
            pdbs_for_analysis = inputPath.listFiles().findAll { it.name.endsWith('.pdb') }
            if (pdbs_for_analysis.isEmpty()) {
                throw new FileNotFoundException("No PDB files found in directory: ${params.skip_input_dir}. Please provide PDB files to proceed with the workflow.")
            }
            println("Found ${pdbs_for_analysis.size()} PDB files")
        }

        // Copy PDB files to inputs directory
        pdbs_for_analysis.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }

        // Create channel with PDBs from specified path
        Channel
            .of(pdbs_for_analysis)
            .set { analysis_input_pdbs }
    }
    else if (params.diffusion_method == 'boltzgen') {
        println("Skipping Structure Prediction stage for BoltzGen diffusion output.")
    }
    else {
        println("Skipping Structure Prediction stage as run_rfd_only=true or run_boltzgen_only=true.")
    }

    //////////////////////////////
    // DOCKING STAGE (Stage 4)  //
    //////////////////////////////
    if (params.run_docking && !params.run_rfd_only && !params.run_boltzgen_only) {
        println("Running Docking Stage (DiffDock)...")

        // Determine input source: BoltzGen outputs go directly to docking, others go through prediction
        def docking_input_pdbs
        if (params.diffusion_method == 'boltzgen') {
            // BoltzGen skips prediction, use filtered designs directly
            docking_input_pdbs = filt_seq_pdbs
        }
        else {
            // Standard flow uses prediction outputs
            docking_input_pdbs = analysis_input_pdbs
        }

        // Ligand SMILES: prefer diffdock-specific, fall back to boltzgen params
        def ligand_smiles = params.diffdock_ligand_smiles ?: params.boltzgen_ligand_smiles ?: ''
        def ntp_type = params.diffdock_ntp_type ?: params.boltzgen_ntp_type ?: ''

        PrepDiffDock(docking_input_pdbs, ligand_smiles, ntp_type)

        // Batch for GPU
        PrepDiffDock.out.csv
            .combine(PrepDiffDock.out.pdbs.collect().map { pdbs -> [pdbs] })
            .map { csv, pdbs -> tuple("batch_0", csv, pdbs) }
            .set { docking_input }

        RunDiffDock(docking_input)

        // Note: FilterDiffDock expects different output format after module update
        // Skipping filter for now as SDF files are already ranked by confidence

        // Publish docking results
        println("DiffDock results will be published to: ${params.out_dir}/run/diffdock")
    }
    ////////////////////
    // ANALYSIS STAGE //
    ////////////////////
    if (!params.run_rfd_only) {
        // Analysis of PDBs to generate additional metrics 
        AnalyseBestDesigns(analysis_input_pdbs)
        // Use placeholder PDB file if no designs survive filtering
        analysis_input_pdbs
            .flatten()
            .collect()
            .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
            .set { final_pdbs }
    }
    else {
        println("Skipping Analysis stage as run_rfd_only=true or run_boltzgen_only=true.")
    }

    // Open topic channels to collect metadata for all designs. 
    // Channel for metadata with only fold_id and not seq_id
    channel.topic('metadata_ch_fold')
        .flatten()
        .collectFile(name: "metadata_fold.jsonl", newLine: true)
        .ifEmpty { file("${params.code_root}/lib/empty-meta-fold.jsonl") }
        .set { metadata_fold }
    // Channel for metadata with both fold_id and seq_id
    channel.topic('metadata_ch_fold_seq')
        .flatten()
        .collectFile(name: "metadata_fold_seq.jsonl", newLine: true)
        .ifEmpty { file("${params.code_root}/lib/empty-meta-seq.jsonl") }
        .set { metadata_fold_seq }

    // Combine Metadata into CSV
    CombineMetadata(metadata_fold, metadata_fold_seq).csv.collectFile(name: "all_designs.csv").set { all_designs_metadata }

    // Count outputs
    if (params.run_rfd_only) {
        Utils.countPdbFiles(rfd_tuples).set { rfd_count }
        Utils.countPdbFiles(final_pdbs).set { filter_rfd_count }
        seq_count = 0
        filter_seq_count = 0
        filter_pred_count = 0
    }
    else if (params.skip_rfd_seq_pred) {
        rfd_count = 0
        filter_rfd_count = 0
        seq_count = 0
        filter_seq_count = 0
        filter_pred_count = 0
    }
    else if (params.skip_rfd_seq) {
        rfd_count = 0
        filter_rfd_count = 0
        seq_count = 0
        filter_seq_count = 0
        Utils.countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }
    else if (params.skip_rfd) {
        rfd_count = 0
        filter_rfd_count = 0
        Utils.countPdbFiles(seq_tuple).set { seq_count }
        Utils.countPdbFiles(filt_seq_pdbs).set { filter_seq_count }
        Utils.countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }
    else {
        Utils.countPdbFiles(rfd_tuples).set { rfd_count }
        Utils.countPdbFiles(filt_rfd_pdbs_jsons).set { filter_rfd_count }
        Utils.countPdbFiles(seq_tuple).set { seq_count }
        Utils.countPdbFiles(filt_seq_pdbs).set { filter_seq_count }
        Utils.countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }

    // Generate report and statistics of run
    PublishResults(
        final_pdbs,
        all_designs_metadata,
        rfd_count,
        filter_rfd_count,
        seq_count,
        filter_seq_count,
        filter_pred_count,
    )

    // Save log file on completion
    workflow.onComplete {
        def logFile = file('.nextflow.log')
        def outputDir = file(outputDirectory)
        if (logFile.exists()) {
            logFile.copyTo(outputDir.resolve('nextflow.log'))
        }
    }
}

// Collect required input files for RFdiffusion
def collectInputFiles(params) {
    def inputs = []

    // Add required input files
    if (params.rfd_mode in [
        'binder_denovo',
        'binder_foldcond',
        'binder_motifscaff',
        'binder_partialdiff',
        'monomer_motifscaff',
        'monomer_partialdiff',
    ]) {
        if (params.rfd_input_pdb) {
            inputs << file(params.rfd_input_pdb)
        }
    }
    if (params.rfd_mode in ['monomer_denovo', 'monomer_foldcond']) {
        // Add 'placeholder' PDB file, since RFdiffusion requires xyz coordinates
        inputs << file("${params.code_root}/lib/placeholder.pdb")
    }
    if (params.rfd_mode in ['binder_foldcond', 'monomer_foldcond']) {
        if (params.rfd_scaffold_dir) {
            // Add scaffolds_dir and contents
            inputs << file(params.rfd_scaffold_dir)
        }
    }
    if (params.rfd_mode == 'binder_foldcond') {
        if (params.rfd_target_ss) {
            inputs << file(params.rfd_target_ss)
        }
        if (params.rfd_target_adj) {
            inputs << file(params.rfd_target_adj)
        }
    }

    return inputs
}
