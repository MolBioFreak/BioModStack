/*
 * Oligo Designer Workflow
 * De novo design of DNA, RNA, proteins, and mixed nucleoprotein assemblies
 * 
 * Pipeline: RFDpoly → Boltz-2 Validation → Filter → Results
 * 
 * Phase 5: Added target_pdb support for protein-binding aptamer design
 */

include { RFDPolyDesign ; PrepBoltzOligo ; OLIGO_DESIGN } from '../modules/rfdpoly.nf'
include { RunBoltz ; FilterBoltz } from '../modules/boltz.nf'

/*
 * Main workflow entry point
 */
workflow OLIGO_DESIGNER {
    take:
    design_id
    contigs
    polymer_chains
    input_pdb // Scaffold PDB for redesign (optional)
    target_pdb // Target protein for binding design (optional)

    main:
    // Stage 1: Run OLIGO_DESIGN sub-workflow (RFDpoly)
    OLIGO_DESIGN(
        design_id,
        contigs,
        polymer_chains,
        input_pdb,
        target_pdb,
    )

    // Stage 2: Boltz-2 validation (if enabled)
    if (params.oligo_validate_boltz) {
        // PrepBoltzOligo is already called in OLIGO_DESIGN
        // Now run Boltz-2 prediction on the prepared YAMLs
        boltz_yamls = OLIGO_DESIGN.out.boltz_yamls
            .flatten()
            .map { yaml -> tuple(yaml.baseName, yaml) }

        RunBoltz(boltz_yamls)

        // Stage 3: Filter by confidence thresholds
        FilterBoltz(RunBoltz.out.pdbs_jsons)

        validated_pdbs = FilterBoltz.out.pdbs
        boltz_logs = RunBoltz.out.logs
    }
    else {
        validated_pdbs = OLIGO_DESIGN.out.pdbs
        boltz_logs = channel.empty()
    }

    emit:
    pdbs = validated_pdbs
    rfdpoly_metrics = OLIGO_DESIGN.out.metrics
    boltz_logs = boltz_logs
}

workflow {
    def noiseSchedule = params.containsKey('rfdpoly_noise_schedule') ? params.rfdpoly_noise_schedule : 'linear'
    def bindingGuidance = params.containsKey('binding_guidance') ? params.binding_guidance : false
    def scaffoldPdb = params.containsKey('scaffold_pdb') ? params.scaffold_pdb : null
    def rfdpolyInputPdb = params.containsKey('rfdpoly_input_pdb') ? params.rfdpoly_input_pdb : null
    def targetPdb = params.containsKey('target_pdb') ? params.target_pdb : null
    def designId = params.containsKey('design_id') && params.design_id ? params.design_id : 'oligo_design'

    println("Running Oligo Designer (RFDpoly + Boltz-2)")
    println("* Contigs: ${params.rfdpoly_contigs}")
    println("* Polymer chains: ${params.rfdpoly_polymer_chains}")
    println("* Num designs: ${params.rfdpoly_num_designs}")
    println("* Checkpoint: ${params.rfdpoly_checkpoint}")
    println("* Noise schedule: ${noiseSchedule}")
    println("* Binding guidance: ${bindingGuidance}")
    println("* Validate with Boltz: ${params.oligo_validate_boltz}")
    if (targetPdb) {
        println("* Target PDB: ${targetPdb}")
    }

    def input_pdb = scaffoldPdb
        ? channel.fromPath(scaffoldPdb)
        : (rfdpolyInputPdb
            ? channel.fromPath(rfdpolyInputPdb)
            : channel.of(file("${params.code_root}/NO_FILE")))

    def target_pdb = targetPdb
        ? channel.fromPath(targetPdb)
        : channel.of(file("${params.code_root}/NO_FILE"))

    OLIGO_DESIGNER(
        channel.of(designId),
        channel.of(params.rfdpoly_contigs),
        channel.of(params.rfdpoly_polymer_chains),
        input_pdb,
        target_pdb
    )
}
