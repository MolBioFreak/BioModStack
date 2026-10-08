process PrepBoltzGenInput {
    label 'pyrosetta_tools'

    input:
    val ligand_smiles
    val ntp_type
    val scaffold_length
    val num_designs
    val binding_site_residues
    val catalytic_site
    val protein_sequence
    val dna_template_seq
    val dna_primer_seq
    val secondary_structure
    val protocol
    path input_pdb
    path ligand_pdb
    path dna_structure

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
        ${binding_site_residues ? "--binding_site_residues '${binding_site_residues}'" : ''} \\
        ${catalytic_site ? "--catalytic_site" : ''} \\
        ${protein_sequence ? "--protein_sequence '${protein_sequence}'" : ''} \\
        ${dna_template_seq ? "--dna_template_seq '${dna_template_seq}'" : ''} \\
        ${dna_primer_seq ? "--dna_primer_seq '${dna_primer_seq}'" : ''} \\
        ${secondary_structure ? "--secondary_structure '${secondary_structure}'" : ''} \\
        ${protocol ? "--protocol '${protocol}'" : '--protocol protein-anything'} \\
        ${input_pdb.name != 'NO_INPUT_PDB' ? "--input_pdb '${input_pdb}'" : ''} \\
        ${ligand_pdb.name != 'NO_LIGAND_PDB' ? "--ligand_pdb '${ligand_pdb}'" : ''} \\
        ${dna_structure.name != 'NO_DNA_STRUCT' ? "--dna_structure '${dna_structure}'" : ''} \\
        --output_yaml boltzgen_input.yaml
    """
}

process RunBoltzGen {
    label 'BoltzGen'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltzgen", mode: 'copy', pattern: "*.log"
    // Wrapper outputs converted PDBs + JSONs to output/designs/
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "output/designs/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "output/designs/*.json", saveAs: { filename -> filename.split('/')[-1] }
    // Also capture batch metadata if available
    publishDir "${params.out_dir}/run/boltzgen/metadata", mode: 'copy', pattern: "output/**/all_designs_metrics.csv", saveAs: { filename -> filename.split('/')[-1] }

    input:
    path yaml_configs

    output:
    path "output/designs/*.pdb", emit: pdbs, optional: true
    path "output/designs/*.json", emit: jsons, optional: true
    path "*.log"

    script:
    def numDesigns = params.boltzgen_num_designs ?: 10
    def batchSize = params.boltzgen_batch_size ?: 1
    def protocol = params.boltzgen_protocol ?: 'auto'
    def stepScale = params.boltzgen_step_scale ?: ''
    def noiseScale = params.boltzgen_noise_scale ?: ''
    def inverseFoldAvoid = params.boltzgen_inverse_fold_avoid ?: ''
    def inverseFoldNumSeqs = params.boltzgen_inverse_fold_num_sequences ?: ''
    def checkpointMode = params.boltzgen_checkpoint_mode ?: ''
    def skipInverseFolding = params.boltzgen_skip_inverse_folding ?: false
    def reuseExisting = params.boltzgen_reuse ?: false
    // Handle both single config and batch of configs
    def configArg = yaml_configs instanceof List ? "--configs ${yaml_configs.join(' ')}" : "--config ${yaml_configs}"
    """
    # Run BoltzGen with wrapper that handles CIF->PDB conversion and batch processing
    
    python3 /scripts/run_boltzgen_wrapper.py \\
        ${configArg} \\
        --out_dir output \\
        --num_designs ${numDesigns} \\
        ${batchSize > 1 ? "--batch_size ${batchSize}" : ""} \\
        --protocol ${protocol} \\
        ${stepScale ? "--step_scale ${stepScale}" : ''} \\
        ${noiseScale ? "--noise_scale ${noiseScale}" : ''} \\
        ${inverseFoldAvoid ? "--inverse_fold_avoid '${inverseFoldAvoid}'" : ''} \\
        ${inverseFoldNumSeqs ? "--inverse_fold_num_sequences ${inverseFoldNumSeqs}" : ''} \\
        ${checkpointMode && checkpointMode != 'both' ? "--checkpoint_mode ${checkpointMode}" : ''} \\
        ${skipInverseFolding ? "--skip_inverse_folding" : ''} \\
        ${reuseExisting ? "--reuse" : ''} \\
        ${params.boltzgen_extra_config ? params.boltzgen_extra_config : ''} \\
        2>&1 | tee boltzgen.log
    """
}

process FilterBoltzGen {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/filter_boltzgen", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/filter_boltzgen", mode: 'copy', pattern: "filtered/*.json"

    input:
    path pdbs
    path jsons

    output:
    path "filtered/*.pdb", emit: pdbs
    path "filtered/*.json", emit: jsons, optional: true
    path "filtered/filter_summary.json", emit: summary, optional: true
    path "*.log"

    script:
    // Build filter parameters
    def minPlddt = params.boltzgen_min_plddt ?: ''
    def minConfScore = params.boltzgen_min_conf_score ?: ''
    def maxRmsd = params.boltzgen_max_rmsd ?: ''
    def budget = params.boltzgen_budget ?: ''
    def alpha = params.boltzgen_alpha ?: '0.01'
    def filterBiased = params.boltzgen_filter_biased != false ? 'true' : 'false'
    def metricsOverride = params.boltzgen_metrics_override ?: ''
    def additionalFilters = params.boltzgen_additional_filters ?: ''
    def sizeBuckets = params.boltzgen_size_buckets ?: ''

    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    python /scripts/filter_boltzgen.py \\
        --pdbs ${pdbs} \\
        --jsons ${jsons} \\
        ${minPlddt ? "--boltzgen-min-plddt ${minPlddt}" : ''} \\
        ${minConfScore ? "--boltzgen-min-conf-score ${minConfScore}" : ''} \\
        ${maxRmsd ? "--boltzgen-max-rmsd ${maxRmsd}" : ''} \\
        ${budget ? "--budget ${budget}" : ''} \\
        --alpha ${alpha} \\
        --filter-biased ${filterBiased} \\
        ${metricsOverride ? "--metrics-override '${metricsOverride}'" : ''} \\
        ${additionalFilters ? "--additional-filters '${additionalFilters}'" : ''} \\
        ${sizeBuckets ? "--size-buckets '${sizeBuckets}'" : ''} \\
        --out_dir filtered \\
        2>&1 | tee filter_boltzgen.log
    """
}
