/*
 * RFDpoly Multi-Polymer Design Module
 * Generates DNA, RNA, protein, and mixed assemblies
 * 
 * Based on: https://github.com/RosettaCommons/RFDpoly
 * Container paths: /RFDpoly (repo), /models (weights)
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
    val contigs           // e.g., "33 33 75" (space-separated lengths per chain)
    val polymer_chains    // e.g., "dna,rna,protein"
    val use_input_pdb     // Boolean: whether to use input_pdb
    path input_pdb        // Optional motif scaffold
    
    output:
    path "*.pdb", emit: pdbs
    path "rfdpoly_metrics.json", emit: metrics
    
    script:
    // Format polymer chains as RFDpoly expects: ['dna','protein']
    def chains_formatted = polymer_chains.split(',').collect { chain -> "'${chain.trim()}'" }.join(',')
    
    // Select checkpoint based on param (Critical fix #6: checkpoint selection)
    def ckpt_file = params.rfdpoly_checkpoint == 'rna_optimized' 
        ? 'train_session2024-06-27_1719522052_BFF_7.00.pt'
        : 'train_session2024-07-08_1720455712_BFF_3.00.pt'
    
    // Handle optional input PDB (Critical fix #4)
    def input_arg = use_input_pdb && input_pdb.name != 'NO_FILE'
        ? "inference.input_pdb=${input_pdb}"
        : "inference.input_pdb=/RFDpoly/rf_diffusion/test_data/DBP035.pdb"
    
    // Advanced params (High fix: temperature, seed)
    def temp_arg = params.rfdpoly_temperature ? "diffuser.partial_T=${params.rfdpoly_temperature}" : ""
    def seed_arg = params.rfdpoly_seed ? "inference.seed=${params.rfdpoly_seed}" : ""
    
    // Container paths: /RFDpoly (repo), /models (weights)
    """
    python3 /RFDpoly/rf_diffusion/run_inference.py \\
        --config-name=multi_polymer \\
        diffuser.T=${params.rfdpoly_diffusion_steps} \\
        inference.ckpt_path=/models/${ckpt_file} \\
        inference.num_designs=${params.rfdpoly_num_designs} \\
        contigmap.contigs="['\${contigs}']" \\
        contigmap.polymer_chains="[${chains_formatted}]" \\
        ${input_arg} \\
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
    label 'pyrosetta_tools'  // Use existing python container
    
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
    input_pdb
    
    main:
    // Determine if input_pdb is provided (Critical fix #4)
    def use_input = input_pdb != null && input_pdb.name != 'NO_FILE'
    
    // Stage 1: RFDpoly Generation
    RFDPolyDesign(
        design_id,
        contigs,
        polymer_chains,
        use_input,
        input_pdb ?: file("${projectDir}/NO_FILE")
    )
    
    // Stage 2: Prepare for Boltz-2 validation
    if (params.oligo_validate_boltz) {
        prep_script = file("${projectDir}/scripts/prep_boltz_oligo.py")
        PrepBoltzOligo(RFDPolyDesign.out.pdbs, prep_script)
        boltz_yamls = PrepBoltzOligo.out.yamls
    } else {
        boltz_yamls = channel.empty()
    }
    
    emit:
    pdbs = RFDPolyDesign.out.pdbs
    metrics = RFDPolyDesign.out.metrics
    boltz_yamls = boltz_yamls
}
