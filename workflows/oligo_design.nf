/*
 * Oligo Designer Workflow
 * De novo design of DNA, RNA, proteins, and mixed nucleoprotein assemblies
 * 
 * Pipeline: RFDpoly → Boltz-2 Validation → Filter → Results
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
    input_pdb

    main:
    // Stage 1: Run OLIGO_DESIGN sub-workflow (RFDpoly)
    OLIGO_DESIGN(
        design_id,
        contigs,
        polymer_chains,
        input_pdb,
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

/*
 * Standalone entry for direct workflow invocation
 */
workflow {
    // Check if this is an oligo design run
    if (params.mode == 'oligo_design' || params.rfdpoly_enabled) {

        // Prepare input PDB channel
        input_pdb = params.rfdpoly_input_pdb
            ? channel.fromPath(params.rfdpoly_input_pdb)
            : channel.of(file('NO_INPUT'))

        // Run the workflow
        OLIGO_DESIGNER(
            channel.of(params.design_id ?: 'oligo_design'),
            channel.of(params.rfdpoly_contigs),
            channel.of(params.rfdpoly_polymer_chains),
            input_pdb,
        )

        // Collect outputs
        OLIGO_DESIGNER.out.pdbs
            .collect()
            .subscribe { pdbs ->
                println("Oligo Designer completed: ${pdbs.size()} designs generated")
            }
    }
}
