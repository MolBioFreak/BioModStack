#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

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
include { PrepBoltzGenInput ; RunBoltzGen ; FilterBoltzGen } from './modules/boltzgen.nf'
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
include { BoltzFromComplex } from './modules/structure_prediction.nf'
include { RF3FromSequence } from './modules/structure_prediction.nf'
include { structure_prediction_wf } from './modules/structure_prediction.nf'

// Antibody Design Subworkflow
include { ANTIBODY_DESIGN } from './workflows/antibody_design.nf'

// De Novo Antibody Pipeline (RFantibody -> FAMPNN/AntiFold -> Boltz2 -> AntiBERTy -> ThermoMPNN -> IgGM)
include { ANTIBODY_DENOVO } from './workflows/antibody_denovo.nf'

workflow {
    // Permit use of topic channels in Nextflow v24 by enabling preview features
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

    // Calculate batch size based on maximum GPUs
    def num_batches = Math.min(params.gpus, params.rfd_num_designs).intValue()
    def batch_size = Math.ceil(params.rfd_num_designs / num_batches).intValue()
    def num_designs = num_batches * batch_size

    println("Pipeline Mode: ${params.rfd_mode}")
    println("Number of RFdiffusion designs: ${num_designs}")
    println("Number of sequences for each design: ${params.seqs_per_design}")
    println("Output Directory: ${outputDirectory}")


    // Create output directory for copy of config files used in run
    def configDir = file("${outputDirectory}/configs")
    configDir.mkdirs()
    workflow.configFiles.each { configFile ->
        configFile.copyTo("${configDir}/${configFile.getName()}")
    }

    // Create output directory for copy of input files used in run
    def inputsDir = file("${outputDirectory}/inputs")
    inputsDir.mkdirs()

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
            // RFantibody → FAMPNN/AntiFold → Boltz2 → AntiBERTy → IgGM
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
        println("* Predictor: boltz")
        println("* Number of simulations: ${numParallelJobs}")
        // Complex mode only supports Boltz for now

        def complex_name = params.sequence_name ?: 'complex_pred'
        def complex_json = file(params.complex_json_path)

        // Create parallel job channels (like structure_prediction workflow)
        def job_indices = Channel.from(0..<numParallelJobs)
        def complex_ch = job_indices.map { idx ->
            def jobName = numParallelJobs > 1 ? "${complex_name}_job${idx}" : complex_name
            tuple(jobName, complex_json, file("${projectDir}/NO_MSA"))
        }
        BoltzFromComplex(complex_ch)

        BoltzFromComplex.out.pdbs
            .flatten()
            .collect()
            .ifEmpty(file("${projectDir}/lib/placeholder.pdb"))
            .set { final_pdbs }

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

        if (params.pred_method == 'rf3' || params.pred_method == 'both' || params.pred_method == 'boltz') {
            // Use the unified workflow which handles 'both', MSA generation, and tuple inputs
            structure_prediction_wf(parallel_jobs_ch)

            structure_prediction_wf.out.structures
                .flatten()
                .collect()
                .set { final_pdbs }
        }
        else {
            // Fallback for unknown method, default to Boltz inside workflow anyway
            structure_prediction_wf(parallel_jobs_ch)
            structure_prediction_wf.out.structures.flatten().collect().set { final_pdbs }
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
                    params.rfd_input_pdb ? file(params.rfd_input_pdb) : file("${projectDir}/lib/NO_FILE"),
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
                    .ifEmpty(file("${projectDir}/lib/placeholder.pdb"))
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
                    .ifEmpty(file("${projectDir}/lib/placeholder.pdb"))
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
            params.boltzgen_input_pdb ? file(params.boltzgen_input_pdb) : file("${projectDir}/lib/NO_INPUT_PDB"),
            params.boltzgen_ligand_pdb ? file(params.boltzgen_ligand_pdb) : file("${projectDir}/lib/NO_LIGAND_PDB"),
            params.boltzgen_dna_structure ? file(params.boltzgen_dna_structure) : file("${projectDir}/lib/NO_DNA_STRUCT"),
        )

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
                .ifEmpty(file("${projectDir}/lib/placeholder.pdb"))
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
    if (!params.skip_rfd_seq & !params.skip_rfd_seq_pred & !params.run_rfd_only) {
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
            Utils
                .rebatchGPU(PrepFAMPNN.out.pdbs, params.gpus)
                .set { fampnn_pdbs }

            // Add CSV path to PDB channel
            fampnn_pdbs
                .combine(mega_csv)
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
    if (!params.skip_rfd_seq_pred && !params.run_rfd_only && !params.skip_pred) {
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
            error("Not a valid structure prediction method. Choose from: af2, boltz, rf3")
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
            .ifEmpty(file("${projectDir}/lib/placeholder.pdb"))
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
        .ifEmpty { file("${projectDir}/lib/empty-meta-fold.jsonl") }
        .set { metadata_fold }
    // Channel for metadata with both fold_id and seq_id
    channel.topic('metadata_ch_fold_seq')
        .flatten()
        .collectFile(name: "metadata_fold_seq.jsonl", newLine: true)
        .ifEmpty { file("${projectDir}/lib/empty-meta-seq.jsonl") }
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
        inputs << file("${projectDir}/lib/placeholder.pdb")
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
