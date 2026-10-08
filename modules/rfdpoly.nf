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
        inference.update_seq_t=True \\
        diffuser.aa_decode_steps=40 \\
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
    
    # Fix PDB files for proper visualization in PDBe Mol*
    # RFDpoly outputs lack TER/END records which breaks RNA bond rendering
    for pdb in *.pdb; do
        if [ -f "\$pdb" ]; then
            # Add TER before ENDMDL and END at the end
            sed -i 's/^ENDMDL/TER\\nENDMDL/' "\$pdb"
            echo "END" >> "\$pdb"
        fi
    done
    
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
 * Complete workflow: RFDpoly → NA-MPNN → Boltz-2 → Results
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

    // Stage 1: RFDpoly Backbone Generation
    RFDPolyDesign(
        design_id,
        contigs,
        polymer_chains,
        use_scaffold,
        scaffold_file,
        use_target,
        target_file,
    )

    // Stage 2: NA-MPNN Sequence Design
    // Per RFDpoly paper: "we design base sequences on the generated backbones using NA-MPNN"
    NAMPNNDesign(RFDPolyDesign.out.pdbs, design_id)

    // Stage 3: Prepare for Boltz-2 validation
    if (params.oligo_validate_boltz) {
        prep_script = file("${projectDir}/scripts/prep_boltz_oligo.py")
        PrepBoltzOligo(NAMPNNDesign.out.pdbs, prep_script)
        boltz_yamls = PrepBoltzOligo.out.yamls
    }
    else {
        boltz_yamls = channel.empty()
    }

    emit:
    pdbs = NAMPNNDesign.out.pdbs
    sequences = NAMPNNDesign.out.fastas
    metrics = RFDPolyDesign.out.metrics
    boltz_yamls = boltz_yamls
}

/*
 * Process: NAMPNNDesign
 * Runs NA-MPNN to design optimized sequences for nucleic acid backbones
 * Per paper: generalizes ProteinMPNN to nucleic acids
 */
process NAMPNNDesign {
    label 'nampnn'
    label 'gpu'

    publishDir "${params.out_dir}/run/nampnn", mode: 'copy'

    input:
    path pdbs
    val design_id

    output:
    path "designed/*.pdb", emit: pdbs
    path "designed/*.fa", emit: fastas
    path "nampnn_metrics.json", emit: metrics

    script:
    """
    mkdir -p designed input_converted nampnn_out
    
    # Convert RFDpoly residue names to NA-MPNN format
    # RFDpoly: RG, RC, RU, RA -> NA-MPNN: G, C, U, A
    for pdb in *.pdb; do
        if [ -f "\$pdb" ]; then
            sed 's/ RG / G  /g; s/ RC / C  /g; s/ RU / U  /g; s/ RA / A  /g' "\$pdb" > "input_converted/\$pdb"
        fi
    done
    
    # Run NA-MPNN from its working directory (required for data_utils import)
    cd /app/NA-MPNN
    
    for pdb in \${PWD}/../input_converted/*.pdb; do
        if [ -f "\$pdb" ]; then
            echo "Running NA-MPNN on \$(basename \$pdb)..."
            python inference/run.py \\
                --model_type "na_mpnn" \\
                --mode "design" \\
                --pdb_path "\$pdb" \\
                --out_folder "\${PWD}/../nampnn_out" \\
                --number_of_batches ${params.nampnn_num_seqs ?: 1}
        fi
    done
    
    cd -
    
    # Copy designed outputs to designed/ folder
    # NA-MPNN outputs: backbones/<name>_1.pdb, seqs/<name>.fa
    if [ -d "./nampnn_out/backbones" ]; then
        cp ./nampnn_out/backbones/*.pdb designed/ 2>/dev/null || true
    fi
    if [ -d "./nampnn_out/seqs" ]; then
        cp ./nampnn_out/seqs/*.fa designed/ 2>/dev/null || true
    fi
    
    # Generate metrics JSON
    python3 -c "
import json, glob
pdbs = glob.glob('designed/*.pdb')
fastas = glob.glob('designed/*.fa')
metrics = {
    'design_id': '${design_id}',
    'num_designs': len(pdbs),
    'num_sequences': len(fastas),
    'model': 'na_mpnn',
    'num_batches': ${params.nampnn_num_seqs ?: 1}
}
json.dump(metrics, open('nampnn_metrics.json', 'w'), indent=2)
print(f'NA-MPNN designed sequences for {len(pdbs)} structures')
"
    """
}
