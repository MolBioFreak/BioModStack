/*
 * RFDpoly Multi-Polymer Design Module
 * Generates DNA, RNA, protein, and mixed assemblies
 * 
 * Based on: https://github.com/RosettaCommons/RFDpoly
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
    path input_pdb        // Optional motif scaffold (or 'NO_INPUT')
    
    output:
    path "*.pdb", emit: pdbs
    path "rfdpoly_metrics.json", emit: metrics
    
    script:
    // Format polymer chains as RFDpoly expects: ['dna','protein']
    def chains_formatted = polymer_chains.split(',').collect{"'${it.trim()}'"}.join(',')
    
    // Handle optional input PDB (addendum fix #3)
    def input_arg = input_pdb.name != 'NO_INPUT' ? 
        "inference.input_pdb=${input_pdb}" : 
        "inference.input_pdb=${params.rfdpoly_dir}/rf_diffusion/test_data/DBP035.pdb"
    
    // Map BioModStack params to RFDpoly Hydra keys (addendum fix #4)
    """
    python3 ${params.rfdpoly_dir}/rf_diffusion/run_inference.py \\
        --config-name=multi_polymer \\
        diffuser.T=${params.rfdpoly_diffusion_steps} \\
        inference.ckpt_path=${params.rfdpoly_weights} \\
        inference.num_designs=${params.rfdpoly_num_designs} \\
        contigmap.contigs="['${contigs}']" \\
        contigmap.polymer_chains="[${chains_formatted}]" \\
        ${input_arg} \\
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
    label 'cpu'
    
    publishDir "${params.out_dir}/run/rfdpoly/boltz_prep", mode: 'copy'
    
    input:
    path pdbs
    
    output:
    path "boltz_inputs/*.yaml", emit: yamls
    
    script:
    """
    mkdir -p boltz_inputs
    python3 ${projectDir}/scripts/prep_boltz_oligo.py \\
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
    // Stage 1: RFDpoly Generation
    RFDPolyDesign(
        design_id,
        contigs,
        polymer_chains,
        input_pdb
    )
    
    // Stage 2: Prepare for Boltz-2 validation
    if (params.oligo_validate_boltz) {
        PrepBoltzOligo(RFDPolyDesign.out.pdbs)
        boltz_yamls = PrepBoltzOligo.out.yamls
    } else {
        boltz_yamls = channel.empty()
    }
    
    emit:
    pdbs = RFDPolyDesign.out.pdbs
    metrics = RFDPolyDesign.out.metrics
    boltz_yamls = boltz_yamls
}
