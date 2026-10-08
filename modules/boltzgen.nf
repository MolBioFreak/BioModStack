process PrepBoltzGenInput {
    label 'pyrosetta_tools'
    stageInMode 'copy'

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
    val covalent_bonds
    val nanobody_framework
    val cdr_h1_length
    val cdr_h2_length
    val cdr_h3_length
    path input_pdb, stageAs: 'source/*'
    path ligand_pdb, stageAs: 'ligand/*'
    path dna_structure, stageAs: 'dna/*'
    path target_pdb, stageAs: 'target/*'
    path scaffold_structures, stageAs: 'scaffold??/*'

    output:
    path "boltzgen_input.yaml", emit: yaml
    path "boltzgen_inputs", emit: bundle

    script:
    def nanobodyScaffoldSpecs = params.get('boltzgen_nanobody_scaffold_specs')
    def scaffoldFiles = scaffold_structures instanceof List ? scaffold_structures : [scaffold_structures]
    if (nanobodyScaffoldSpecs) {
        def specs = new groovy.json.JsonSlurper().parseText(nanobodyScaffoldSpecs.toString())
        if (!(specs instanceof List) || specs.size() != scaffoldFiles.size()) error('BoltzGen scaffold closure cardinality mismatch')
        specs.eachWithIndex { spec, index ->
            if (!(spec.spec instanceof Map) || !(spec.spec.path instanceof String)) error('BoltzGen scaffold requires a typed native spec.path')
            // Only the typed path binding changes. Native chain/residue/design
            // constraints and source bytes retain their original authority.
            spec.spec.path = scaffoldFiles[index].toString()
            if (spec.containsKey('path')) spec.path = scaffoldFiles[index].toString()
        }
        nanobodyScaffoldSpecs = groovy.json.JsonOutput.toJson(specs)
    }
    """
    export MAMBA_ROOT_PREFIX=/opt/conda/
    if [ -x /opt/conda/envs/pyrosetta/bin/python3 ]; then
        export PATH=/opt/conda/envs/pyrosetta/bin:\$PATH
    elif command -v micromamba >/dev/null 2>&1; then
        eval "\$(micromamba shell hook --shell bash)"
        if micromamba env list 2>/dev/null | awk '{print \$1}' | grep -qx 'pyrosetta'; then
            micromamba activate pyrosetta
        fi
    fi

    # Prepare input YAML for BoltzGen
    python3 ${params.code_root}/scripts/prep_boltzgen.py \\
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
        ${covalent_bonds ? "--covalent_bonds '${covalent_bonds}'" : ''} \\
        ${nanobody_framework ? "--nanobody_framework '${nanobody_framework}'" : ''} \\
        ${nanobodyScaffoldSpecs ? "--nanobody_scaffold_specs '${nanobodyScaffoldSpecs}'" : ''} \\
        ${cdr_h1_length ? "--cdr_h1_length '${cdr_h1_length}'" : ''} \\
        ${cdr_h2_length ? "--cdr_h2_length '${cdr_h2_length}'" : ''} \\
        ${cdr_h3_length ? "--cdr_h3_length '${cdr_h3_length}'" : ''} \\
        ${input_pdb && input_pdb.name != 'NO_INPUT_PDB' ? "--input_pdb '${input_pdb}'" : ''} \\
        ${ligand_pdb && ligand_pdb.name != 'NO_LIGAND_PDB' ? "--ligand_pdb '${ligand_pdb}'" : ''} \\
        ${dna_structure && dna_structure.name != 'NO_DNA_STRUCT' ? "--dna_structure '${dna_structure}'" : ''} \\
        ${target_pdb && target_pdb.name != 'NO_TARGET_PDB' ? "--target_pdb '${target_pdb}'" : ''} \\
        --output_yaml boltzgen_input.yaml

    # Note: boltzgen YAML validation skipped here (boltzgen CLI only in boltzgen.sif)
    # The prep_boltzgen.py script validates structure internally
    echo "BoltzGen YAML prepared: boltzgen_input.yaml"
    cat boltzgen_input.yaml
    python3 '${params.code_root}/scripts/native_boltzgen_campaign.py' bundle --yaml boltzgen_input.yaml --output boltzgen_inputs
    """
}

process RunBoltzGen {
    label 'BoltzGen'
    label 'gpu'
    maxForks 1
    errorStrategy 'terminate'
    stageInMode 'copy'
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/run/boltzgen", mode: 'copy', pattern: "*.log"
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/run/boltzgen/native", mode: 'copy', pattern: 'output'
    // Wrapper outputs converted PDBs + JSONs to output/designs/
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/pdb_files", mode: 'copy', pattern: "output/designs/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/pdb_files", mode: 'copy', pattern: "output/designs/*.json", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/collected/boltzgen_raw", mode: 'copy', pattern: "output/designs/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/collected/boltzgen_raw", mode: 'copy', pattern: "output/designs/*.json", saveAs: { filename -> filename.split('/')[-1] }
    // Also capture batch metadata if available
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/run/boltzgen/metadata", mode: 'copy', pattern: "output/**/all_designs_metrics.csv", saveAs: { filename -> filename.split('/')[-1] }

    input:
    tuple val(component), path(yaml_configs)

    output:
    tuple val(component), path("output/designs"), emit: native_outputs
    path 'output', emit: scientific_output
    path "output/designs/*.pdb", emit: pdbs, optional: true
    path "output/designs/*.{json,npz,csv}", emit: jsons, optional: true
    path "*.log"

    script:
    def effective = new LinkedHashMap(params as Map)
    effective.putAll(component.settings ?: [:])
    def numDesigns = component.designs
    def diffusionBatchSize = effective.get('boltzgen_diffusion_batch_size') ?: effective.get('boltzgen_batch_size') ?: 1
    def protocol = effective.get('boltzgen_protocol') ?: 'auto'
    def stepScale = effective.get('boltzgen_step_scale')
    def noiseScale = effective.get('boltzgen_noise_scale')
    def inverseFoldAvoid = effective.get('boltzgen_inverse_fold_avoid') ?: ''
    def inverseFoldNumSeqs = effective.get('boltzgen_inverse_fold_num_sequences') ?: ''
    def checkpointMode = effective.get('boltzgen_checkpoint_mode') ?: ''
    def skipInverseFolding = effective.get('boltzgen_skip_inverse_folding') ?: false
    def reuseExisting = effective.get('boltzgen_reuse') ?: false
    // Handle both single config and batch of configs
    // Every native reference was materialized by preparation. Execute inside
    // the portable closure so YAML relative paths retain their exact meaning.
    def configArg = '--config boltzgen_input.yaml'
    """
    # Run BoltzGen with wrapper that handles CIF->PDB conversion and batch processing
    cp -a '${yaml_configs}/.' .
    set +e
    set -o pipefail
    
    python3 /scripts/run_boltzgen_wrapper.py \\
        ${effective.get('core_protein_scientific_contract') == 1 ? "--core-protein-scientific-contract 1" : ''} \\
        ${configArg} \\
        --out_dir output \\
        --num_designs ${numDesigns} \\
        ${diffusionBatchSize > 1 ? "--diffusion_batch_size ${diffusionBatchSize}" : ""} \\
        --protocol ${protocol} \\
        ${stepScale != null ? "--step_scale ${stepScale}" : ''} \\
        ${noiseScale != null ? "--noise_scale ${noiseScale}" : ''} \\
        ${inverseFoldAvoid ? "--inverse_fold_avoid '${inverseFoldAvoid}'" : ''} \\
        ${inverseFoldNumSeqs ? "--inverse_fold_num_sequences ${inverseFoldNumSeqs}" : ''} \\
        ${checkpointMode && checkpointMode != 'both' ? "--checkpoint_mode ${checkpointMode}" : ''} \\
        ${skipInverseFolding ? "--skip_inverse_folding" : ''} \\
        ${reuseExisting ? "--reuse" : ''} \\
        ${effective.get('boltzgen_extra_config') ?: ''} \\
        2>&1 | tee boltzgen.log
    native_status=\$?
    mkdir -p output/designs
    printf '{"stage":"inference","exit_code":%s}\n' "\$native_status" > output/designs/component_execution.json
    ${component.index == null ? 'exit "$native_status"' : 'exit 0'}
    """
}

process FilterBoltzGen {
    // Native byte evidence must be regular task-local snapshots, not symlinks.
    stageInMode { params.get('core_protein_scientific_contract') == 1 ? 'copy' : 'symlink' }
    label 'pyrosetta_tools'
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/run/filter_boltzgen", mode: 'copy', pattern: "*.log"
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/run/filter_boltzgen", mode: 'copy', pattern: "filtered/*.json"
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/collected/boltzgen_filtered", mode: 'copy', pattern: "filtered/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${component.index == null ? params.out_dir : params.out_dir + '/components/boltzgen/' + component.index}/collected/boltzgen_filtered", mode: 'copy', pattern: "filtered/*.{json,npz,csv}", saveAs: { filename -> filename.split('/')[-1] }

    input:
    tuple val(component), path(pdbs), path(jsons)

    output:
    tuple val(component), path("filtered"), emit: native_outputs
    path "filtered/*.pdb", emit: pdbs, optional: true
    path "filtered/*.json", emit: jsons, optional: true
    path "filtered/filter_summary.json", emit: summary, optional: true
    path "filtered/*.{npz,csv}", emit: native_artifacts, optional: true
    path "*.log"

    script:
    def effective = new LinkedHashMap(params as Map)
    effective.putAll(component.settings ?: [:])
    // Only marked future attempts use strict zero-preserving transport.
    def strictEvidence = effective.get('core_protein_scientific_contract') == 1
    def minPlddt = strictEvidence ? effective.get('boltzgen_min_plddt') : (effective.get('boltzgen_min_plddt') ?: null)
    def minConfScore = strictEvidence ? effective.get('boltzgen_min_conf_score') : (effective.get('boltzgen_min_conf_score') ?: null)
    def maxRmsd = strictEvidence ? (effective.get('boltzgen_refolding_rmsd_threshold') != null ? effective.get('boltzgen_refolding_rmsd_threshold') : effective.get('boltzgen_max_rmsd')) : (effective.get('boltzgen_refolding_rmsd_threshold') ?: effective.get('boltzgen_max_rmsd') ?: null)
    def budget = strictEvidence ? effective.get('boltzgen_budget') : (effective.get('boltzgen_budget') ?: null)
    def alpha = strictEvidence && effective.get('boltzgen_alpha') != null ? effective.get('boltzgen_alpha') : (effective.get('boltzgen_alpha') ?: '0.01')
    def filterBiased = effective.containsKey('boltzgen_filter_biased') ? (effective.get('boltzgen_filter_biased') != false) : true
    def metricsOverride = effective.get('boltzgen_metrics_override') ?: ''
    def additionalFilters = effective.get('boltzgen_additional_filters') ?: ''
    def sizeBuckets = effective.get('boltzgen_size_buckets') ?: ''

    """
    export MAMBA_ROOT_PREFIX=/opt/conda/
    if [ -x /opt/conda/envs/pyrosetta/bin/python3 ]; then
        export PATH=/opt/conda/envs/pyrosetta/bin:\$PATH
    elif command -v micromamba >/dev/null 2>&1; then
        eval "\$(micromamba shell hook --shell bash)"
        if micromamba env list 2>/dev/null | awk '{print \$1}' | grep -qx 'pyrosetta'; then
            micromamba activate pyrosetta
        fi
    fi

    set +e
    set -o pipefail
    python3 ${params.code_root}/scripts/filter_boltzgen.py \\
        ${effective.get('core_protein_scientific_contract') == 1 ? "--core-protein-scientific-contract 1" : ''} \\
        --pdbs ${pdbs} \\
        --jsons ${jsons} \\
        ${minPlddt != null ? "--boltzgen-min-plddt ${minPlddt}" : ''} \\
        ${minConfScore != null ? "--boltzgen-min-conf-score ${minConfScore}" : ''} \\
        ${maxRmsd != null ? "--boltzgen-max-rmsd ${maxRmsd}" : ''} \\
        ${budget != null ? "--budget ${budget}" : ''} \\
        --alpha ${alpha} \\
        --filter-biased ${filterBiased} \\
        ${metricsOverride ? "--metrics-override '${metricsOverride}'" : ''} \\
        ${additionalFilters ? "--additional-filters '${additionalFilters}'" : ''} \\
        ${sizeBuckets ? "--size-buckets '${sizeBuckets}'" : ''} \\
        --out_dir filtered \\
        2>&1 | tee filter_boltzgen.log
    native_status=\$?
    mkdir -p filtered
    printf '{"stage":"filter","exit_code":%s}\n' "\$native_status" > filtered/component_execution.json
    ${component.index == null ? 'exit "$native_status"' : 'exit 0'}
    """
}
