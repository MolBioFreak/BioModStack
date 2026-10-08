#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

params.sequence_batch_json_path = params.sequence_batch_json_path ?: null
params.complex_batch_dir = params.complex_batch_dir ?: null
params.target_geometry_mode = params.target_geometry_mode ?: null
params.boltz_target_geometry_mode = params.boltz_target_geometry_mode ?: null
params.protenix_target_geometry_mode = params.protenix_target_geometry_mode ?: null
params.target_template_threshold_angstrom = params.target_template_threshold_angstrom ?: 2.0
params.strict_target_rmsd = params.strict_target_rmsd ?: null

include { RFDiffusionWorkflow } from './workflows/rfdiffusion.nf'
include { FilterRFD ; RunRFDiffusion } from './modules/rfdiffusion.nf'
include { PrepRFD3Input ; RunRFD3 ; FilterRFD3 } from './modules/rfd3.nf'
include { RunRF3 ; FilterRF3 } from './modules/rf3.nf'
include { PrepFAMPNN ; FilterFAMPNN ; RunFAMPNN } from './modules/fampnn.nf'
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
include { FrustrampnnQC ; AggregateFrustrationReports } from './modules/frustrampnn.nf'

include { ANTIBODY_DESIGN } from './workflows/antibody_design.nf'

include { ANTIBODY_DENOVO ; NormalizeTargetPDB as NormalizeAntibodyTargetPDB } from './workflows/antibody_denovo.nf'

include { ANTIBODY_CHILD } from './workflows/antibody_child.nf'
include { RFANTIBODY_BACKBONE } from './workflows/rfantibody_backbone.nf'
include { MATURATION_CHILD_CORE } from './workflows/maturation_child_core.nf'
include { PPIFLOW_GENERATOR_DESIGN } from './workflows/ppiflow_generator_design.nf'

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

    if (params.rfd_mode == 'antibody_child') {
        println("Running Antibody Child Validation Job")

        def pdb_list = []
        if (params.pdb_paths) {
            def clean = params.pdb_paths.toString().replace('[', '').replace(']', '').split(',')
            pdb_list = clean.collect { it.strip().replaceAll(/['"]/, '') }.findAll { it }.collect { file(it) }
            println("* Mode: Batch (${pdb_list.size()} designs)")
        }
        else if (params.pdb_path) {
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

    if (params.rfd_mode == 'bindcraft_child') {
        println("Running BindCraft Child Job (SWA)")
        println("* Child index: ${params.child_index}")
        println("* Trajectories: ${params.bindcraft_num_final_designs}")

        BINDCRAFT_DESIGN()

        return null
    }

    if (params.rfd_mode == 'rfantibody_backbone') {
        println("Running RFantibody Backbone Generation (Child Job)")
        println("* Target PDB: ${params.target_pdb}")
        println("* Epitope: ${params.epitope_residues}")
        println("* Num designs: ${params.rfantibody_num_designs}")
        println("* GPU: ${params.gpu_id}")

        if (!params.target_pdb) {
            error("Target PDB required for rfantibody_backbone mode")
        }

        def hotspots = params.epitope_residues ?: ""
        def rfantibody_num_designs = params.rfantibody_num_designs ?: 10

        def framework_for_rfantibody = params.framework_pdb
            ? file(params.framework_pdb)
            : file("${params.code_root}/lib/NO_FRAMEWORK")

        RFANTIBODY_BACKBONE(
            file(params.target_pdb),
            hotspots,
            rfantibody_num_designs,
            framework_for_rfantibody
        )

        return null
    }

    if (params.rfd_mode == 'fampnn_child') {
        println("Running FAMPNN Sequence Design (Child Job)")
        println("* PDB paths: ${params.pdb_paths}")
        println("* Seqs per design: ${params.seqs_per_design}")
        println("* GPU: ${params.gpu_id}")

        if (!params.pdb_paths) {
            error("PDB paths required for fampnn_child mode")
        }

        def pdb_paths_raw = params.pdb_paths.toString()
        def pdb_list = pdb_paths_raw.split(',').collect { it.strip().replaceAll(/[\[\]'"]/, '') }.findAll { it }.collect { file(it) }

        if (pdb_list.isEmpty()) {
            error("No valid PDB files found in pdb_paths: ${params.pdb_paths}")
        }

        println("* Processing ${pdb_list.size()} PDBs")

        fampnn_prep_input = Channel.of(tuple(pdb_list, file("${params.code_root}/lib/NO_JSON")))

        PrepFAMPNN(fampnn_prep_input)

        def gpu_id_val = params.gpu_id ?: 0

        fampnn_run_input = PrepFAMPNN.out.pdbs
            .collect()
            .combine(PrepFAMPNN.out.csv)
            .map { payload ->
                tuple(0, payload[0..-2], payload[-1], gpu_id_val)
            }

        RunFAMPNN(fampnn_run_input, params.analysis_chain_id ?: 'all_chains')

        def filterEnabled = params.enable_fampnn_filter != false && (params.fampnn_max_psce != null || params.fampnn_max_residue_psce != null)

        if (filterEnabled) {
            println("  Filtering FAMPNN designs...")
            FilterFAMPNN(RunFAMPNN.out.pdbs_jsons)
        }

        println("FAMPNN child workflow configured")
        return null
    }

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
        MATURATION_CHILD_CORE(pdb_list)

        println("PPIFlow maturation child job complete")
        return null
    }

    if (params.rfd_mode == 'ppiflow_generator') {
        def ppiflowSeedPath = params.get('ppiflow_seed_complex_path')
        def ppiflowSeedDir = params.get('ppiflow_seed_input_dir') ?: params.get('selected_input_dir')
        println("Running PPIFlow Seeded Generator")
        println("* Seed complex path: ${ppiflowSeedPath ?: 'not provided'}")
        println("* Seed complex dir: ${ppiflowSeedDir ?: 'not provided'}")
        println("* Samples per target: ${params.ppiflow_samples_per_target ?: 1}")

        if (!ppiflowSeedPath && !ppiflowSeedDir) {
            error("PPIFlow seeded generator requires --ppiflow_seed_complex_path or --ppiflow_seed_input_dir")
        }

        PPIFLOW_GENERATOR_DESIGN()
        return null
    }

    if (params.rfd_mode in ['structure_prediction', 'inverse_folding', 'stability_prediction', 'de_novo', 'antibody_denovo_pipeline', 'antibody_refinement_pipeline']) {
        println("Running Antibody Design Toolkit")
        println("* Mode: ${params.rfd_mode}")

        def jobName = params.sequence_name ?: 'antibody_job'
        def meta = [id: jobName]
        def input_ch

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
        else if (params.rfd_mode in ['antibody_denovo_pipeline', 'antibody_refinement_pipeline']) {
            if (!params.target_pdb) {
                error("Antigen PDB required for ${params.rfd_mode}")
            }
            def selectedInputDir = params.selected_input_dir ?: params.rfantibody_input_pdbs ?: params.fampnn_collected_pdbs
            if (params.rfd_mode == 'antibody_refinement_pipeline' && !selectedInputDir) {
                error("Selected input directory required for antibody_refinement_pipeline")
            }
            def epitope = params.epitope_residues ?: ""
            input_ch = channel.of(tuple(meta, file(params.target_pdb)))

            def framework_ch = params.framework_pdb
                ? channel.of(tuple(meta, file(params.framework_pdb)))
                : channel.empty()

            NormalizeAntibodyTargetPDB(input_ch)
            def normalized_input_ch = NormalizeAntibodyTargetPDB.out.normalized

            ANTIBODY_DENOVO(normalized_input_ch, epitope, framework_ch)

            return null
        }
        else {
            if (!params.target_pdb) {
                error("Target PDB required for ${params.rfd_mode}")
            }
            input_ch = channel.of(tuple(meta, file(params.target_pdb)))
        }

        ANTIBODY_DESIGN(input_ch, params.rfd_mode)

        return null
    }


    if (params.complex_json_path) {
        def numParallelJobs = params.num_parallel_jobs ?: 1
        println("Running complex-based structure prediction (multi-chain + ligands)")
        println("* Complex definition: ${params.complex_json_path}")
        println("* Predictor: ${params.pred_method ?: 'boltz'}")
        println("* Number of simulations: ${numParallelJobs}")

        def complex_name = params.sequence_name ?: 'complex_pred'
        def msa_file = params.msa_path ? file(params.msa_path) : file("${params.code_root}/NO_MSA")
        def complex_ch

        if (params.sequence_batch_json_path && params.complex_batch_dir) {
            def batchEntries = parseJsonFile(params.sequence_batch_json_path) as List
            println("* Batch variants: ${batchEntries.size()}")
            if ((params.pred_method ?: 'boltz') == 'protenix') {
                println("* Protenix complex batch mode: one model bootstrap for ${batchEntries.size()} variants")
                complex_ch = Channel.of(
                    tuple(
                        "${complex_name}_batch",
                        file(params.complex_batch_dir),
                        msa_file,
                    )
                )
            } else {
                complex_ch = Channel
                    .from(batchEntries)
                    .map { entry ->
                        tuple(
                            "${entry.name}",
                            file("${entry.complex_json}"),
                            msa_file,
                        )
                    }
            }
        } else {
            def complex_json = file(params.complex_json_path)

            def job_indices = Channel.from(0..<numParallelJobs)
            complex_ch = job_indices.map { idx ->
                def jobName = numParallelJobs > 1 ? "${complex_name}_job${idx}" : complex_name
                tuple(jobName, complex_json, msa_file)
            }
        }

        complex_prediction_wf(complex_ch)

        complex_prediction_wf.out.structures
            .flatten()
            .collect()
            .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
            .set { final_pdbs }

        if (params.run_frustrampnn == true) {
            println("Running FrustraMPNN post-analysis on complex predictions")
            def frustra_input = final_pdbs
                .flatten()
                .map { pdb -> tuple([id: pdb.baseName], pdb) }
            FrustrampnnQC(frustra_input)
            AggregateFrustrationReports(FrustrampnnQC.out.summary.map { meta, summary -> summary }.collect())
        }

        return null
    }


    if (params.unidock_ligand_smiles || params.unidock_ntp_type) {
        println("Running Uni-Dock standalone docking")
        println("* Receptor: ${params.skip_input_dir}")
        println("* Ligand SMILES: ${params.unidock_ligand_smiles ?: 'N/A'}")
        println("* NTP Type: ${params.unidock_ntp_type ?: 'N/A'}")
        println("* Box Size: ${params.unidock_box_size}Å")
        println("* Exhaustiveness: ${params.unidock_exhaustiveness}")

        def inputPath = file(params.skip_input_dir)
        if (!inputPath.exists()) {
            error("Receptor PDB not found at: ${params.skip_input_dir}")
        }

        def receptor_pdbs = inputPath.isFile() ? [inputPath] : inputPath.listFiles().findAll { it.name.endsWith('.pdb') }
        if (receptor_pdbs.isEmpty()) {
            error("No PDB files found in: ${params.skip_input_dir}")
        }

        def receptor_ch = Channel.from(receptor_pdbs)

        PrepUniDock(
            receptor_ch,
            params.unidock_ligand_smiles ?: '',
            params.unidock_ntp_type ?: '',
            params.unidock_box_size,
            params.unidock_box_center ?: '',
            params.unidock_flexible_residues ?: '',
        )

        def flex_receptor = PrepUniDock.out.flex_receptor.ifEmpty(file('NO_FLEX'))

        def unidock_input = PrepUniDock.out.receptor
            .combine(flex_receptor)
            .combine(PrepUniDock.out.ligand_dir)
            .combine(PrepUniDock.out.box)
            .map { receptor, flex, ligands, box ->
                tuple("unidock_0", receptor, flex, ligands, box)
            }

        RunUniDock(unidock_input)

        FilterUniDock(RunUniDock.out.poses.collect(), RunUniDock.out.scores)

        println("Uni-Dock docking complete. Results in: ${params.out_dir}/run/unidock")
        return null
    }


    if (params.sequence_input || params.sequence_batch_json_path) {
        def numParallelJobs = params.num_parallel_jobs ?: 1
        println("Running sequence-based structure prediction")
        if (params.sequence_batch_json_path) {
            println("* Batch manifest: ${params.sequence_batch_json_path}")
        } else {
            println("* Sequence: ${params.sequence_input.take(50)}...")
        }
        println("* Predictor: ${params.pred_method ?: 'boltz'}")
        println("* Parallel jobs: ${numParallelJobs}")

        def seq_name = params.sequence_name ?: 'predicted'
        def parallel_jobs_ch

        if (params.sequence_batch_json_path) {
            def batchEntries = parseJsonFile(params.sequence_batch_json_path) as List
            println("* Batch sequences: ${batchEntries.size()}")
            parallel_jobs_ch = Channel
                .from(batchEntries)
                .map { entry ->
                    tuple("${entry.sequence}", "${entry.name}")
                }
        } else {
            def job_indices = Channel.from(0..<numParallelJobs)

            parallel_jobs_ch = job_indices.map { idx ->
                def jobName = numParallelJobs > 1 ? "${seq_name}_job${idx}" : seq_name
                tuple(params.sequence_input, jobName)
            }
        }

        if (params.pred_method in ['boltz', 'rf3', 'both', 'protenix', 'all']) {
            structure_prediction_wf(parallel_jobs_ch)

            structure_prediction_wf.out.structures
                .flatten()
                .collect()
                .set { final_pdbs }
        }
        else {
            structure_prediction_wf(parallel_jobs_ch)
            structure_prediction_wf.out.structures.flatten().collect().set { final_pdbs }
        }

        if (params.run_frustrampnn == true) {
            def frustra_input = final_pdbs
                .flatten()
                .map { pdb -> tuple([id: pdb.baseName], pdb) }
            FrustrampnnQC(frustra_input)
            AggregateFrustrationReports(FrustrampnnQC.out.summary.map { meta, summary -> summary }.collect())
        }

        return null
    }


    if (!params.skip_rfd & !params.skip_rfd_seq & !params.skip_rfd_seq_pred & params.diffusion_method != 'boltzgen') {
        if (!params.rfd_num_designs) {
            error("Please provide the number of designs for RFdiffusion to generate")
        }

        if (params.diffusion_method == "rfd3") {
            println("Using RFdiffusion3 (Foundry) for structure generation")

            def inputFiles = collectInputFiles(params)
            inputFiles.each { inputFile ->
                "rsync -r ${inputFile} ${inputsDir}/.".execute()
            }

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

            PrepRFD3Input(rfd3_input_ch)

            RunRFD3(PrepRFD3Input.out.input_json)

            RunRFD3.out.structures_metadata.set { rfd_pdbs_jsons }

            rebatchTuples(rfd_pdbs_jsons, 200)
                .set { rfd_tuples }

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
            error("diffusion_method='rfd' has been retired from tracked BioModStack repo state. Use diffusion_method='rfd3' instead.")
        }
    }
    else if (params.diffusion_method == "boltzgen") {

        println("Using BoltzGen for all-atom binder generation")

        def boltzgenNumDesigns = params.get('boltzgen_num_designs') ?: 10
        def boltzgenDesignsPerJob = params.get('boltzgen_designs_per_job') ?: 100
        def boltzgenParallelMode = params.get('boltzgen_parallel_mode') == true

        PrepBoltzGenInput(
            params.boltzgen_ligand_smiles ?: '',
            params.boltzgen_ntp_type ?: '',
            params.boltzgen_scaffold_length,
            boltzgenNumDesigns,
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

        def parallel_mode_set = params.containsKey('parallel_mode')
        def use_orchestrator = parallel_mode_set
            ? (params.parallel_mode == 'full_orchestrator')
            : boltzgenParallelMode
        if (use_orchestrator) {
            println("BoltzGen PARALLEL MODE: Spawning ${Math.ceil(boltzgenNumDesigns / boltzgenDesignsPerJob)} child jobs")

            def target_pdb = params.boltzgen_target_pdb_path ? file(params.boltzgen_target_pdb_path) : file("${params.code_root}/lib/NO_TARGET_PDB")

            SpawnBoltzGenJobs(
                params.job_id ?: 'unknown',
                boltzgenNumDesigns,
                boltzgenDesignsPerJob,
                PrepBoltzGenInput.out.yaml,
                target_pdb,
                params.boltzgen_mode ?: 'nanobody_binder',
                params.name ?: 'boltzgen_campaign',
            )

            WaitForBoltzGenChildren(
                params.job_id ?: 'unknown',
                SpawnBoltzGenJobs.out.result,
                params.name ?: 'boltzgen_campaign',
            )

            CollectBoltzGenOutputs(WaitForBoltzGenChildren.out.result)

            AggregateBoltzGenResults(
                params.job_id ?: 'unknown',
                CollectBoltzGenOutputs.out.pdbs.collect(),
                CollectBoltzGenOutputs.out.jsons.collect(),
                CollectBoltzGenOutputs.out.manifest,
            )

            CollectBoltzGenOutputs.out.pdbs
                .flatten()
                .collect()
                .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
                .set { final_pdbs }

            rfd_tuples = Channel.empty()
            filt_rfd_pdbs_jsons = Channel.empty()
            filt_seq_pdbs = Channel.empty()
            analysis_input_pdbs = Channel.empty()
        }
        else {
            RunBoltzGen(PrepBoltzGenInput.out.yaml)

            FilterBoltzGen(RunBoltzGen.out.pdbs, RunBoltzGen.out.jsons)

            if (params.run_boltzgen_only) {
                println("BoltzGen standalone mode complete. Exiting.")
                FilterBoltzGen.out.pdbs
                    .flatten()
                    .collect()
                    .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
                    .set { final_pdbs }

                rfd_tuples = Channel.empty()
                filt_rfd_pdbs_jsons = Channel.empty()
                filt_seq_pdbs = Channel.empty()
                analysis_input_pdbs = Channel.empty()
                seq_tuple = Channel.empty()

                params.skip_rfd_seq = true
                params.skip_rfd_seq_pred = true
            }
            else {
                FilterBoltzGen.out.pdbs
                    .flatten()
                    .collect()
                    .set { filt_seq_pdbs }

                FilterBoltzGen.out.pdbs
                    .flatten()
                    .collect()
                    .set { analysis_input_pdbs }

                rfd_tuples = Channel.empty()
                filt_rfd_pdbs_jsons = Channel.empty()
                seq_tuple = Channel.empty()
            }
        }
    }
    else if (params.skip_rfd & !params.skip_rfd_seq & !params.skip_rfd_seq_pred) {
        println("Skipping RFDiffusion stage as skip_rfd=true.")
        println("Running Sequence Design, Prediction, and Analysis stages only.")
        println("Looking for PDBs and JSONs in: ${params.skip_input_dir}")
        if (!file(params.skip_input_dir).exists()) {
            throw new FileNotFoundException("Skip input file directory not found at: ${params.skip_input_dir}. Please ensure the path is correct.")
        }
        def previous_pdbs = file("${params.skip_input_dir}").listFiles().findAll { it.name.endsWith('.pdb') }
        def previous_jsons = file("${params.skip_input_dir}").listFiles().findAll { it.name.endsWith('.json') }
        if (previous_pdbs.isEmpty()) {
            throw new FileNotFoundException("No PDB files found in directory: ${params.skip_input_dir}. Please provide PDB files to proceed with the workflow.")
        }
        if (previous_jsons.isEmpty()) {
            throw new FileNotFoundException("No JSON files found in directory: ${params.skip_input_dir}. Please provide JSON files to proceed with the workflow.")
        }
        println("Found ${previous_pdbs.size()} PDB files")
        println("Found ${previous_jsons.size()} JSON files\n")

        previous_pdbs.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }
        previous_jsons.each { jsonFile ->
            jsonFile.copyTo("${inputsDir}/${jsonFile.getName()}")
        }

        Channel
            .of([previous_pdbs, previous_jsons])
            .set { rfd_pdbs_jsons }
        rebatchTuples(rfd_pdbs_jsons, 200)
            .set { filt_rfd_pdbs_jsons }
    }
    else {
        println("Skipping RFDiffusion stage as skip_rfd_seq=true or skip_rfd_seq_pred=true.")
    }
    if (params.diffusion_method == 'boltzgen') {
        println("Skipping Sequence Design stage for BoltzGen diffusion output.")
    }
    else if (!params.skip_rfd_seq & !params.skip_rfd_seq_pred & !params.run_rfd_only) {
        if (params.seq_method == "mpnn") {
            PrepMPNN(filt_rfd_pdbs_jsons)

            if (params.mpnn_relax_max_cycles > 0) {
                PrepMPNN.out.pdbs
                    .collect()
                    .flatten()
                    .buffer(size: 2, remainder: true)
                    .set { seq_input_pdbs }
            }
            else {
                PrepMPNN.out.pdbs
                    .collect()
                    .flatten()
                    .buffer(size: 10, remainder: true)
                    .set { seq_input_pdbs }
            }

            RunMPNN(seq_input_pdbs)

            CompressMPNN("mpnn", RunMPNN.out.pdbs_jsons.flatten().collect())

            rebatchTuples(RunMPNN.out.pdbs_jsons, 200)
                .set { seq_tuple }

            FilterMPNN(seq_tuple)
            FilterMPNN.out.pdbs
                .flatten()
                .collect()
                .set { filt_seq_pdbs }
        }
        else if (params.seq_method == "fampnn") {
            rebatchTuples(filt_rfd_pdbs_jsons, 10)
                .set { fampnn_prep_input_tuple }

            PrepFAMPNN(fampnn_prep_input_tuple)
            PrepFAMPNN.out.csv
                .collectFile(name: 'merged_results.csv', keepHeader: true)
                .set { mega_csv }

            PrepFAMPNN.out.pdbs
                .collect()
                .map { allPdbs -> partitionGpuBatches(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { fampnn_pdbs }

            def default_gpu = params.pinned_gpus ? params.pinned_gpus.toString().split(',')[0].trim().toInteger() : (params.gpu_id ?: 0)
            fampnn_pdbs
                .combine(mega_csv)
                .map { batch_id, pdbs, csv -> [batch_id, pdbs, csv, default_gpu] }
                .set { fampnn_input }

            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
                RunFAMPNN(fampnn_input, 'A')
            }
            else {
                RunFAMPNN(fampnn_input, 'all_chains')
            }

            CompressFAMPNN("fampnn", RunFAMPNN.out.pdbs_jsons.flatten().collect())

            rebatchTuples(RunFAMPNN.out.pdbs_jsons, 200)
                .set { seq_tuple }

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
        println("Skipping Sequence Design stage as skip_rfd_seq=true.")
        println("Running Prediction and Analysis stages only.")
        println("Looking for PDBs in: ${params.skip_input_dir}")

        def inputPath = file(params.skip_input_dir)
        if (!inputPath.exists()) {
            throw new FileNotFoundException("Skip input path not found at: ${params.skip_input_dir}. Please ensure the path is correct.")
        }

        def pdbs_for_pred = []
        if (inputPath.isFile()) {
            if (inputPath.name.endsWith('.pdb')) {
                pdbs_for_pred = [inputPath]
                println("Using single PDB file: ${inputPath.name}")
            }
            else {
                throw new FileNotFoundException("Input file must be a .pdb file: ${params.skip_input_dir}")
            }
        }
        else {
            pdbs_for_pred = inputPath.listFiles().findAll { it.name.endsWith('.pdb') }
            if (pdbs_for_pred.isEmpty()) {
                throw new FileNotFoundException("No PDB files found in directory: ${params.skip_input_dir}. Please provide PDB files to proceed with the workflow.")
            }
            println("Found ${pdbs_for_pred.size()} PDB files in directory")
        }

        pdbs_for_pred.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }

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
    if (!params.skip_rfd_seq_pred && !params.run_rfd_only && !params.skip_pred && params.diffusion_method != 'boltzgen') {
        if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
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
        if (params.pred_method == "af2") {

            pred_input_pdbs
                .collect()
                .map { allPdbs -> partitionGpuBatchesByNumRes(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { pred_input_tuple }

            RunAF2(pred_input_tuple)

            CompressAF2("af2", RunAF2.out.pdbs_jsons.flatten().collect())

            rebatchTuples(RunAF2.out.pdbs_jsons, 200)
                .set { af2_tuple }

            FilterAF2(af2_tuple)

            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
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
            PrepBoltz(pred_input_pdbs)

            PrepBoltz.out.yamls
                .collect()
                .map { allPdbs -> partitionGpuBatches(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { pred_input_tuple }

            RunBoltz(pred_input_tuple)

            rebatchTuples(RunBoltz.out.pdbs_jsons, 200)
                .set { boltz_tuple }

            if (params.rfd_mode in ['binder_denovo', 'binder_foldconditioning', 'binder_motifscaffolding', 'binder_partialdiffusion']) {
                AlignBoltz(boltz_tuple, filt_seq_pdbs, 'binder')
            }
            else {
                AlignBoltz(boltz_tuple, filt_seq_pdbs, 'monomer')
            }
            CompressBoltz("boltz", AlignBoltz.out.pdbs_jsons.flatten().collect())

            FilterBoltz(AlignBoltz.out.pdbs_jsons)
            FilterBoltz.out.pdbs
                .flatten()
                .collect()
                .set { analysis_input_pdbs }
        }
        else if (params.pred_method == "rf3") {
            println("Using RosettaFold3 (Foundry) for structure prediction")

            pred_input_pdbs
                .collect()
                .map { allPdbs -> partitionGpuBatches(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { pred_input_tuple }

            RunRF3(pred_input_tuple)

            rebatchTuples(RunRF3.out.structures_metadata, 200)
                .set { rf3_tuple }

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
    else if (!params.run_rfd_only && !params.run_boltzgen_only && params.diffusion_method != 'boltzgen') {
        println("Skipping Structure Prediction stage as skip_rfd_seq_pred=true.")
        println("Running Analysis Stage only")
        def skipInputDir = params.get('skip_input_dir')
        if (!skipInputDir) {
            throw new IllegalArgumentException("skip_input_dir is required when skip_rfd_seq_pred=true and analysis-only mode is selected")
        }
        println("Looking for PDBs in: ${skipInputDir}")
        def inputPath = file(skipInputDir)
        if (!inputPath.exists()) {
            throw new FileNotFoundException("Skip input file path not found at: ${skipInputDir}. Please ensure the path is correct.")
        }

        def pdbs_for_analysis = []
        if (inputPath.isFile()) {
            if (inputPath.name.endsWith('.pdb')) {
                pdbs_for_analysis = [inputPath]
                println("Using single PDB file: ${inputPath.name}")
            }
            else {
                throw new IllegalArgumentException("Input file must be a .pdb file: ${skipInputDir}")
            }
        }
        else {
            pdbs_for_analysis = inputPath.listFiles().findAll { it.name.endsWith('.pdb') }
            if (pdbs_for_analysis.isEmpty()) {
                throw new FileNotFoundException("No PDB files found in directory: ${skipInputDir}. Please provide PDB files to proceed with the workflow.")
            }
            println("Found ${pdbs_for_analysis.size()} PDB files")
        }

        pdbs_for_analysis.each { pdbFile ->
            pdbFile.copyTo("${inputsDir}/${pdbFile.getName()}")
        }

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

    if (params.run_docking && !params.run_rfd_only && !params.run_boltzgen_only) {
        println("Running Docking Stage (DiffDock)...")

        def docking_input_pdbs
        if (params.diffusion_method == 'boltzgen') {
            docking_input_pdbs = filt_seq_pdbs
        }
        else {
            docking_input_pdbs = analysis_input_pdbs
        }

        def ligand_smiles = params.diffdock_ligand_smiles ?: params.boltzgen_ligand_smiles ?: ''
        def ntp_type = params.diffdock_ntp_type ?: params.boltzgen_ntp_type ?: ''

        PrepDiffDock(docking_input_pdbs, ligand_smiles, ntp_type)

        PrepDiffDock.out.csv
            .combine(PrepDiffDock.out.pdbs.collect().map { pdbs -> [pdbs] })
            .map { csv, pdbs -> tuple("batch_0", csv, pdbs) }
            .set { docking_input }

        RunDiffDock(docking_input)


        println("DiffDock results will be published to: ${params.out_dir}/run/diffdock")
    }
    if (!params.run_rfd_only) {
        AnalyseBestDesigns(analysis_input_pdbs)
        analysis_input_pdbs
            .flatten()
            .collect()
            .ifEmpty(file("${params.code_root}/lib/placeholder.pdb"))
            .set { final_pdbs }
    }
    else {
        println("Skipping Analysis stage as run_rfd_only=true or run_boltzgen_only=true.")
    }

    channel.topic('metadata_ch_fold')
        .flatten()
        .collectFile(name: "metadata_fold.jsonl", newLine: true)
        .ifEmpty { file("${params.code_root}/lib/empty-meta-fold.jsonl") }
        .set { metadata_fold }
    channel.topic('metadata_ch_fold_seq')
        .flatten()
        .collectFile(name: "metadata_fold_seq.jsonl", newLine: true)
        .ifEmpty { file("${params.code_root}/lib/empty-meta-seq.jsonl") }
        .set { metadata_fold_seq }

    CombineMetadata(metadata_fold, metadata_fold_seq).csv.collectFile(name: "all_designs.csv").set { all_designs_metadata }

    if (params.run_rfd_only) {
        countPdbFiles(rfd_tuples).set { rfd_count }
        countPdbFiles(final_pdbs).set { filter_rfd_count }
        seq_count = 0
        filter_seq_count = 0
        filter_pred_count = 0
    }
    else if (params.run_boltzgen_only) {
        rfd_count = 0
        filter_rfd_count = 0
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
        countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }
    else if (params.skip_rfd) {
        rfd_count = 0
        filter_rfd_count = 0
        countPdbFiles(seq_tuple).set { seq_count }
        countPdbFiles(filt_seq_pdbs).set { filter_seq_count }
        countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }
    else {
        countPdbFiles(rfd_tuples).set { rfd_count }
        countPdbFiles(filt_rfd_pdbs_jsons).set { filter_rfd_count }
        countPdbFiles(seq_tuple).set { seq_count }
        countPdbFiles(filt_seq_pdbs).set { filter_seq_count }
        countPdbFiles(analysis_input_pdbs).set { filter_pred_count }
    }

    PublishResults(
        final_pdbs,
        all_designs_metadata,
        rfd_count,
        filter_rfd_count,
        seq_count,
        filter_seq_count,
        filter_pred_count,
    )

    workflow.onComplete {
        def logFile = file('.nextflow.log')
        def outputDir = file(outputDirectory)
        if (logFile.exists()) {
            logFile.copyTo(outputDir.resolve('nextflow.log'))
        }
    }
}

def collectInputFiles(params) {
    def inputs = []

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
        inputs << file("${params.code_root}/lib/placeholder.pdb")
    }
    if (params.rfd_mode in ['binder_foldcond', 'monomer_foldcond']) {
        if (params.rfd_scaffold_dir) {
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

def parseJsonFile(rawPath) {
    return new groovy.json.JsonSlurper().parse(file(rawPath))
}

def rebatchTuples(inputChannel, batchSize = 50) {
    return inputChannel
        .transpose()
        .buffer(size: batchSize, remainder: true)
        .map { pairs ->
            def firstElements = pairs.collect { pair -> pair[0] }
            def secondElements = pairs.collect { pair -> pair[1] }
            [firstElements, secondElements]
        }
}

def partitionGpuBatches(allPdbs, gpus) {
    def totalSize = allPdbs.size()
    if (totalSize == 0) {
        return []
    }
    def batchCount = Math.max(1, Math.min((gpus ?: 1) as int, totalSize))
    def batchSize = (totalSize / batchCount).doubleValue()
    def index = 0
    def batches = allPdbs.collect { pdb ->
        def position = index
        index = index + 1
        def batchId = (position / batchSize).intValue()
        [batchId, pdb]
    }
    return batches
}

def countResidues(pdbFile) {
    def residueSet = new HashSet()

    pdbFile.eachLine { line ->
        if (line.startsWith("ATOM  ") || line.startsWith("HETATM")) {
            if (line.length() >= 26) {
                def chainId = line.substring(21, 22)
                def residueNumber = line.substring(22, 26).trim()
                def residueName = line.substring(17, 20).trim()
                residueSet.add("${chainId}_${residueNumber}_${residueName}")
            }
        }
    }

    return residueSet.size()
}

def partitionGpuBatchesByNumRes(allPdbs, gpus) {
    def sortedPdbs = allPdbs.sort { pdb -> countResidues(pdb) }
    return partitionGpuBatches(sortedPdbs, gpus)
}

def countPdbFiles(inputChannel) {
    return inputChannel
        .flatten()
        .collect()
        .map { files ->
            files.findAll { fileObj ->
                def name = fileObj.toString()
                name.endsWith('.pdb') || name.endsWith('.cif.gz') || name.endsWith('.cif')
            }.size()
        }
        .ifEmpty(0)
}
