/*
 * RFDpoly Multi-Polymer Design Module
 * Generates DNA, RNA, protein, and mixed assemblies
 * 
 * Based on: https://github.com/RosettaCommons/RFDpoly
 * Container paths: /RFDpoly (repo), /models (weights)
 * 
 * Phase 5 Enhancement: Full parameter support for target binding,
 * noise schedule, custom sequences, and scaffold redesign
 */

/*
 * Process: RFDPolyDesign
 * Runs RFDpoly diffusion to generate multi-polymer structures
 */
process RFDPolyDesign {
    label 'RFDpoly'
    label 'gpu'

    publishDir "${params.out_dir}/run/rfdpoly", mode: 'copy'

    input:
    val design_id
    val contigs
    // e.g., "33 33 75" (space-separated lengths per chain)
    val polymer_chains
    // e.g., "dna,rna,protein"
    val use_input_pdb
    // Boolean: whether to use input_pdb
    path input_pdb
    // Optional motif scaffold
    val use_target_pdb
    // Boolean: whether to use target_pdb
    path target_pdb

    output:
    path "*.pdb", emit: pdbs
    path "rfdpoly_metrics.json", emit: metrics

    script:
    // Format polymer chains as RFDpoly expects: ['dna','protein']
    def chains_formatted = polymer_chains.split(',').collect { chain -> "'${chain.trim()}'" }.join(',')

    // Select checkpoint based on param
    def ckpt_file = params.rfdpoly_checkpoint == 'rna_optimized'
        ? 'train_session2024-06-27_1719522052_BFF_7.00.pt'
        : 'train_session2024-07-08_1720455712_BFF_3.00.pt'

    // Handle optional input PDB (scaffold)
    def input_arg = use_input_pdb && !input_pdb.name.startsWith('NO_')
        ? "inference.input_pdb=${input_pdb}"
        : ""

    // Handle target PDB for binding design
    def target_arg = use_target_pdb && !target_pdb.name.startsWith('NO_')
        ? "ppi.target_pdb=${target_pdb}"
        : ""

    // Hotspot residues for binding guidance
    def hotspot_arg = params.hotspot_residues && use_target_pdb
        ? "ppi.hotspot_residues=${params.hotspot_residues}"
        : ""

    // Binding guidance (guided diffusion toward target)
    def guidance_arg = params.binding_guidance && use_target_pdb
        ? "diffuser.guidance_scale=2.0"
        : ""

    // Noise schedule (linear or cosine)
    def noise_arg = params.rfdpoly_noise_schedule
        ? "diffuser.noise_schedule=${params.rfdpoly_noise_schedule}"
        : ""

    // Advanced params: temperature, seed
    def temp_arg = params.rfdpoly_temperature ? "diffuser.partial_T=${params.rfdpoly_temperature}" : ""
    def seed_arg = params.rfdpoly_seed ? "inference.seed=${params.rfdpoly_seed}" : ""

    // Container paths: /RFDpoly (repo), /models (weights)
    """
    python3 /RFDpoly/rf_diffusion/run_inference.py \\
        --config-name=multi_polymer \\
        diffuser.T=${params.rfdpoly_diffusion_steps} \\
        inference.ckpt_path=/models/${ckpt_file} \\
        inference.num_designs=${params.rfdpoly_num_designs} \\
        contigmap.contigs="['${contigs}']" \\
        contigmap.polymer_chains="[${chains_formatted}]" \\
        ${input_arg} \\
        ${target_arg} \\
        ${hotspot_arg} \\
        ${guidance_arg} \\
        ${noise_arg} \\
        ${temp_arg} \\
        ${seed_arg} \\
        inference.output_prefix=./${design_id}
    
    # Generate metrics JSON
    python3 -c "
import json, glob
pdbs = glob.glob('*.pdb')
metrics = {
    'design_id': '${design_id}',
    'num_designs': len(pdbs),
    'contigs': '${contigs}',
    'polymer_chains': '${polymer_chains}'.split(','),
    'checkpoint': '${ckpt_file}',
    'noise_schedule': '${params.rfdpoly_noise_schedule ?: "linear"}',
    'binding_guidance': ${params.binding_guidance ? 'True' : 'False'},
    'has_target': ${use_target_pdb ? 'True' : 'False'},
    'pdbs': pdbs
}
json.dump(metrics, open('rfdpoly_metrics.json', 'w'), indent=2)
print(f'Generated {len(pdbs)} designs')
"
    """
}

/*
 * Process: PrepBoltzOligo
 * Converts RFDpoly PDB output to Boltz-2 YAML format
 * Detects polymer type per chain from residue names
 */
process PrepBoltzOligo {
    label 'pyrosetta_tools'

    publishDir "${params.out_dir}/run/rfdpoly/boltz_prep", mode: 'copy'

    input:
    path pdbs
    path prep_script

    output:
    path "boltz_inputs/*.yaml", emit: yamls

    script:
    """
    mkdir -p boltz_inputs
    python3 ${prep_script} \\
        --input_pdbs ${pdbs} \\
        --output_dir boltz_inputs
    """
}

/*
 * Workflow: OLIGO_DESIGN
 * Complete workflow: RFDpoly → Boltz-2 → Filter → Results
 */
workflow OLIGO_DESIGN {
    take:
    design_id
    contigs
    polymer_chains
    input_pdb // Scaffold PDB (optional)
    target_pdb // Target protein PDB (optional, for protein-binding aptamer mode)

    main:
    // Determine if scaffold input_pdb is provided
    // Safely check if input_pdb is a valid file (not null and not placeholder)
    // Use unique placeholder names to avoid Nextflow input file collision
    def use_scaffold = input_pdb instanceof Path && !input_pdb.name.startsWith('NO_')
    def scaffold_file = use_scaffold ? input_pdb : file("${projectDir}/NO_SCAFFOLD")

    // Determine if target_pdb is provided (for binding design)
    def use_target = target_pdb instanceof Path && !target_pdb.name.startsWith('NO_')
    def target_file = use_target ? target_pdb : file("${projectDir}/NO_TARGET")

    // Stage 1: RFDpoly Generation
    RFDPolyDesign(
        design_id,
        contigs,
        polymer_chains,
        use_scaffold,
        scaffold_file,
        use_target,
        target_file,
    )

    // Stage 2: Prepare for Boltz-2 validation
    if (params.oligo_validate_boltz) {
        prep_script = file("${projectDir}/scripts/prep_boltz_oligo.py")
        PrepBoltzOligo(RFDPolyDesign.out.pdbs, prep_script)
        boltz_yamls = PrepBoltzOligo.out.yamls
    }
    else {
        boltz_yamls = channel.empty()
    }

    emit:
    pdbs = RFDPolyDesign.out.pdbs
    metrics = RFDPolyDesign.out.metrics
    boltz_yamls = boltz_yamls
}
