process PrepBoltzGenInput {
    label 'pyrosetta_tools' // Use basic tool container for prep, doesn't need boltzgen

    input:
    val ligand_smiles
    val ntp_type
    val scaffold_length
    val num_designs
    
    output:
    path "boltzgen_input.yaml", emit: yaml

    script:
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    # Prepare input YAML for BoltzGen
    python /scripts/prep_boltzgen.py \\
        ${ligand_smiles ? "--ligand_smiles '${ligand_smiles}'" : ''} \\
        ${ntp_type ? "--ntp_type '${ntp_type}'" : ''} \\
        --scaffold_length '${scaffold_length}' \\
        --num_designs ${num_designs} \\
        --output_yaml boltzgen_input.yaml
    """
}

process RunBoltzGen {
    label 'BoltzGen'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltzgen", mode: 'copy', pattern: "*.log"
    // BoltzGen outputs to final_ranked_designs/ and intermediate_designs_inverse_folded/
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "output/final_ranked_designs/*_predicted*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "output/final_ranked_designs/*_designed*.cif", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "output/designs/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/run/boltzgen/metadata", mode: 'copy', pattern: "output/final_ranked_designs/*.csv", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/run/boltzgen/metadata", mode: 'copy', pattern: "output/final_ranked_designs/*.pdf", saveAs: { filename -> filename.split('/')[-1] }

    input:
    path yaml_configs  // Accepts single config or collection for batch processing

    output:
    path "output/final_ranked_designs/*_predicted*.cif", emit: pdbs, optional: true
    path "output/final_ranked_designs/*_designed*.cif", emit: designed, optional: true
    path "output/designs/*.pdb", emit: converted_pdbs, optional: true
    path "*.log"

    script:
    def numDesigns = params.boltzgen_num_designs ?: 10
    def protocol = params.boltzgen_protocol ?: 'protein-small_molecule'
    // Handle both single config and batch of configs
    def configArg = yaml_configs instanceof List ? "--configs ${yaml_configs.join(' ')}" : "--config ${yaml_configs}"
    """
    # Run BoltzGen with wrapper that handles CIF->PDB conversion and batch processing
    
    python3 /scripts/run_boltzgen_wrapper.py \\
        ${configArg} \\
        --out_dir output \\
        --num_designs ${numDesigns} \\
        --protocol ${protocol} \\
        ${params.boltzgen_extra_config ? params.boltzgen_extra_config : ''} \\
        2>&1 | tee boltzgen.log
    """
}


process FilterBoltzGen {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/filter_boltzgen", mode: 'copy', pattern: "*.log"

    input:
    path pdbs
    path jsons

    output:
    path "filtered/*.pdb", emit: pdbs
    path "*.log"

    script:
    // Use generic filter params or specific ones if added later
    def paramString = Utils.formatFilterParams(
        params,
        "boltzgen",
        ["min_plddt", "min_conf_score"]
    )

    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    python /scripts/filter_boltzgen.py \\
        --pdbs ${pdbs} \\
        --jsons ${jsons} \\
        ${paramString} \\
        --out_dir filtered \\
        2>&1 | tee filter_boltzgen.log
    """
}
