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
    val covalent_bonds
    val nanobody_framework
    val cdr_h1_length
    val cdr_h2_length
    val cdr_h3_length
    path input_pdb
    path ligand_pdb
    path dna_structure
    path target_pdb

    output:
    path "boltzgen_prepared", emit: yaml

    script:
    def nanobodyScaffoldSpecs = params.get('boltzgen_nanobody_scaffold_specs')
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
        ${input_pdb.name != 'NO_INPUT_PDB' ? "--input_pdb '${input_pdb}'" : ''} \\
        ${ligand_pdb.name != 'NO_LIGAND_PDB' ? "--ligand_pdb '${ligand_pdb}'" : ''} \\
        ${dna_structure.name != 'NO_DNA_STRUCT' ? "--dna_structure '${dna_structure}'" : ''} \\
        ${target_pdb.name != 'NO_TARGET_PDB' ? "--target_pdb '${target_pdb}'" : ''} \\
        --output_yaml boltzgen_input.yaml

    # Note: boltzgen YAML validation skipped here (boltzgen CLI only in boltzgen.sif)
    # The prep_boltzgen.py script validates structure internally
    echo "BoltzGen YAML prepared: boltzgen_input.yaml"
    cat boltzgen_input.yaml
    python3 ${params.code_root}/scripts/lib/boltzgen_inputs.py \
        --config boltzgen_input.yaml --output boltzgen_prepared
    """
}

process RunBoltzGen {
    label 'BoltzGen'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltzgen", mode: 'copy', pattern: "*.log"
    // Wrapper outputs converted PDBs + JSONs to output/designs/
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "output/designs/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "output/designs/*.{json,npz,csv}", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/collected/boltzgen_raw", mode: 'copy', pattern: "output/designs/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/collected/boltzgen_raw", mode: 'copy', pattern: "output/designs/*.{json,npz,csv}", saveAs: { filename -> filename.split('/')[-1] }
    // Also capture batch metadata if available
    publishDir "${params.out_dir}/run/boltzgen/metadata", mode: 'copy', pattern: "output/**/all_designs_metrics.csv", saveAs: { filename -> filename.split('/')[-1] }

    input:
    path yaml_configs

    output:
    path "output/designs/*.pdb", emit: pdbs, optional: true
    path "output/designs/*.{json,npz,csv}", emit: jsons, optional: true
    path "*.log"

    script:
    def numDesigns = params.get('boltzgen_num_designs') ?: 10
    def diffusionBatchSize = params.get('boltzgen_diffusion_batch_size') ?: params.get('boltzgen_batch_size') ?: 1
    def protocol = params.get('boltzgen_protocol') ?: 'auto'
    def stepScale = params.get('boltzgen_step_scale')
    def noiseScale = params.get('boltzgen_noise_scale')
    def inverseFoldAvoid = params.get('boltzgen_inverse_fold_avoid') ?: ''
    def inverseFoldNumSeqs = params.get('boltzgen_inverse_fold_num_sequences') ?: ''
    def checkpointMode = params.get('boltzgen_checkpoint_mode') ?: 'both'
    // Runtime placement only: keep the installed CLI's diverse/adherence order
    // and fractions, but consume the exact shared-cache materialized members.
    def checkpointVariants = checkpointMode == 'both' ? ['diverse', 'adherence'] : [checkpointMode]
    def checkpointPaths = checkpointVariants.collect { "/weights/boltzgen/boltzgen1_${it}.ckpt" }.join(' ')
    def skipInverseFolding = params.get('boltzgen_skip_inverse_folding') ?: false
    def reuseExisting = params.get('boltzgen_reuse') ?: false
    // Handle both single config and batch of configs
    def preparedDirectory = !(yaml_configs instanceof List) && yaml_configs.isDirectory()
    def configArg = yaml_configs instanceof List ? "--configs ${yaml_configs.join(' ')}" :
        (preparedDirectory ? '--config boltzgen_input.yaml' : "--config ${yaml_configs}")
    """
    # Work beside the original YAML so its unchanged relative inputs resolve.
    task_dir=\$(pwd)
    ${preparedDirectory ? "cd '${yaml_configs}'" : ''}
    ${params.get('boltzgen_prepared_sha256') ? "python3 ${params.code_root}/scripts/lib/boltzgen_inputs.py --config " + (preparedDirectory ? 'boltzgen_input.yaml' : yaml_configs) + " --expected-sha256 ${params.boltzgen_prepared_sha256}" : ''}
    python3 /scripts/run_boltzgen_wrapper.py \\
        ${params.get('core_protein_scientific_contract') == 1 ? "--core-protein-scientific-contract 1" : ''} \\
        ${configArg} \\
        --out_dir "\$task_dir/output" \\
        --num_designs ${numDesigns} \\
        ${diffusionBatchSize > 1 ? "--diffusion_batch_size ${diffusionBatchSize}" : ""} \\
        --protocol ${protocol} \\
        ${stepScale != null ? "--step_scale ${stepScale}" : ''} \\
        ${noiseScale != null ? "--noise_scale ${noiseScale}" : ''} \\
        ${inverseFoldAvoid ? "--inverse_fold_avoid '${inverseFoldAvoid}'" : ''} \\
        ${inverseFoldNumSeqs ? "--inverse_fold_num_sequences ${inverseFoldNumSeqs}" : ''} \\
        --design_checkpoints ${checkpointPaths} \\
        --inverse_fold_checkpoint /weights/boltzgen/boltzgen1_ifold.ckpt \\
        --folding_checkpoint /weights/boltzgen/boltz2_conf_final.ckpt \\
        --affinity_checkpoint /weights/boltzgen/boltz2_aff.ckpt \\
        --moldir /weights/boltzgen/mols.zip \\
        ${skipInverseFolding ? "--skip_inverse_folding" : ''} \\
        ${reuseExisting ? "--reuse" : ''} \\
        ${params.get('boltzgen_extra_config') ?: ''} \\
        2>&1 | tee "\$task_dir/boltzgen.log"
    """
}

process FilterBoltzGen {
    // Native byte evidence must be regular task-local snapshots, not symlinks.
    stageInMode { params.get('core_protein_scientific_contract') == 1 ? 'copy' : 'symlink' }
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/filter_boltzgen", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/filter_boltzgen", mode: 'copy', pattern: "filtered/*.json"
    publishDir "${params.out_dir}/collected/boltzgen_filtered", mode: 'copy', pattern: "filtered/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/collected/boltzgen_filtered", mode: 'copy', pattern: "filtered/*.{json,npz,csv}", saveAs: { filename -> filename.split('/')[-1] }

    input:
    path pdbs
    path jsons

    output:
    path "filtered/*.pdb", emit: pdbs, optional: params.get('core_protein_scientific_contract') == 1
    path "filtered/*.json", emit: jsons, optional: true
    path "filtered/filter_summary.json", emit: summary, optional: true
    path "filtered/*.{npz,csv}", emit: native_artifacts, optional: true
    path "*.log"

    script:
    // Zero is an explicit scientific setting, not a missing value.
    def minPlddt = params.get('boltzgen_min_plddt')
    def minConfScore = params.get('boltzgen_min_conf_score')
    def maxRmsd = params.get('boltzgen_refolding_rmsd_threshold') != null ? params.get('boltzgen_refolding_rmsd_threshold') : params.get('boltzgen_max_rmsd')
    def budget = params.get('boltzgen_budget')
    def alpha = params.get('boltzgen_alpha') != null ? params.get('boltzgen_alpha') : '0.01'
    def filterBiased = params.containsKey('boltzgen_filter_biased') ? (params.get('boltzgen_filter_biased') != false) : true
    def metricsOverride = params.get('boltzgen_metrics_override') ?: ''
    def additionalFilters = params.get('boltzgen_additional_filters') ?: ''
    def sizeBuckets = params.get('boltzgen_size_buckets') ?: ''

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

    python3 ${params.code_root}/scripts/filter_boltzgen.py \\
        ${params.get('core_protein_scientific_contract') == 1 ? "--core-protein-scientific-contract 1" : ''} \\
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
    """
}

// =============================================================================
// SWA (Spawn-Wait-Aggregate) Processes for Parallelized BoltzGen Campaigns
// Used when boltzgen_parallel_mode is enabled for large-scale design campaigns
// =============================================================================

process SpawnBoltzGenJobs {
    label 'process_low'

    publishDir "${params.out_dir}/run/boltzgen_parallel", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/run/boltzgen_parallel", mode: 'copy', pattern: "*.log"

    input:
    val parent_job_id
    val total_designs
    val designs_per_job
    path yaml_config
    path target_pdb
    val mode
    val batch_name

    output:
    path "spawn_boltzgen_result.json", emit: result
    path "spawn_boltzgen.log"

    script:
    // Capture all applicable selected settings, including the parent's filters.
    // Child entrypoint applies generation only; global selection stays here.
    def selected = params.findAll { key, value -> key.startsWith('boltzgen_') &&
        !(key in ['boltzgen_extra_params', 'boltzgen_yaml_config', 'boltzgen_child_settings_json']) }
    if (params.get('boltzgen_child_settings_json')) {
        selected.putAll(new groovy.json.JsonSlurper().parseText(params.boltzgen_child_settings_json))
    }
    def extra = params.get('boltzgen_extra_params')
    if (extra) {
        def legacy = extra instanceof Map ? extra : new groovy.json.JsonSlurper().parseText(extra.toString())
        legacy.each { key, value ->
            if (selected.containsKey(key) && selected[key] != value) {
                error('BoltzGen extra settings conflict with selected parent: ' + key)
            }
            selected[key] = value
        }
    }
    def paramsJson = "'" + groovy.json.JsonOutput.toJson(selected).replace("'", "'\"'\"'") + "'"
    """
    python3 ${params.code_root}/scripts/spawn_boltzgen_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --total_designs ${total_designs} \\
        --designs_per_job ${designs_per_job} \\
        --yaml_config "\$(readlink -f ${yaml_config})" \\
        --target_pdb "\$(readlink -f ${target_pdb})" \\
        --mode "${mode}" \\
        --batch_name "${batch_name}" \\
        --params_json ${paramsJson} \\
        --api_url "${params.api_url}" \\
        --output spawn_boltzgen_result.json \\
        2>&1 | tee spawn_boltzgen.log
    """
}

process WaitForBoltzGenChildren {
    label 'process_low'

    publishDir "${params.out_dir}/run/boltzgen_parallel", mode: 'copy', pattern: "*.json"
    publishDir "${params.out_dir}/run/boltzgen_parallel", mode: 'copy', pattern: "*.log"

    input:
    val parent_job_id
    path spawn_result
    val batch_name

    output:
    path "boltzgen_child_outputs.json", emit: result
    path "wait_boltzgen.log"

    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "boltzgen" \\
        --poll_interval 30 \\
        --batch_name "${batch_name}" \\
        --api_url "${params.api_url}" \\
        --output boltzgen_child_outputs.json \\
        2>&1 | tee wait_boltzgen.log
    """
}

process CollectBoltzGenOutputs {
    label 'process_low'

    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "collected/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "collected/*.{json,npz,csv}"
    publishDir "${params.out_dir}/collected/boltzgen_raw", mode: 'copy', pattern: "collected/*.pdb", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/collected/boltzgen_raw", mode: 'copy', pattern: "collected/*.{json,npz,csv}", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}/run/boltzgen_parallel", mode: 'copy', pattern: "collection_manifest.json"

    input:
    path child_outputs_json

    output:
    path "collected/*.pdb", emit: pdbs, optional: true
    path "collected/*.{json,npz,csv}", emit: jsons, optional: true
    path "collection_manifest.json", emit: manifest

    script:
    """
    #!/usr/bin/env python3
    import json
    import shutil
    from pathlib import Path
    
    # Read child output directories
    with open("${child_outputs_json}") as f:
        data = json.load(f)
    
    child_dirs = data.get("child_output_dirs", [])
    import sys
    sys.path.insert(0, "${params.code_root}/scripts")
    from child_job_utils import complete_native_collection
    accepted = {directory: [] for directory in child_dirs}

    Path("collected").mkdir(exist_ok=True)
    
    collected_pdbs = []
    collected_jsons = []
    
    for job_idx, child_dir in enumerate(child_dirs):
        dir_path = Path(child_dir)
        if not dir_path.exists():
            print(f"Warning: Child dir {child_dir} does not exist")
            continue
        
        # Search for PDBs and JSONs in standard locations
        for subdir in ["pdb_files", "run/boltzgen/output/designs", "output/designs", ""]:
            search_path = dir_path / subdir if subdir else dir_path
            if not search_path.exists():
                continue
            
            for pdb in search_path.glob("*.pdb"):
                dest = Path("collected") / f"job{job_idx}_{pdb.name}"
                if not dest.exists():
                    shutil.copy(pdb, dest)
                    collected_pdbs.append(str(dest))
                    accepted[child_dir].append(pdb)
                    print(f"Collected: {pdb} -> {dest}")
            
            for js in search_path.glob("confidence_*.json"):
                dest = Path("collected") / f"confidence_job{job_idx}_{js.name[len('confidence_'):]}"
                if not dest.exists():
                    # Derived cohort identity; original child sidecars remain immutable.
                    import hashlib
                    raw = js.read_bytes()
                    payload = json.loads(raw)
                    native_id = js.stem[len('confidence_'):]
                    candidate_id = f"job{job_idx}_{native_id}"
                    if payload.get('design_id', native_id) != native_id:
                        raise ValueError('foreign BoltzGen child metadata identity')
                    payload['design_id'] = candidate_id
                    payload['collection_source'] = {'path': js.relative_to(dir_path).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(),
                                                    'native_candidate_id': native_id}
                    native = payload.get('native_scalar_source')
                    if native is not None:
                        if native['candidate_id'] != native_id:
                            raise ValueError('foreign BoltzGen native scalar candidate')
                        artifact = native['artifact']
                        if Path(artifact['path']).name != artifact['path']:
                            raise ValueError('BoltzGen scalar input escapes child publication')
                        source = js.parent / artifact['path']
                        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != artifact['sha256']:
                            raise ValueError('BoltzGen native scalar bytes changed')
                        native_name = f"job{job_idx}_{source.name}"
                        shutil.copyfile(source, Path('collected') / native_name)
                        native['candidate_id'] = candidate_id
                        artifact['path'] = native_name
                        accepted[child_dir].append(source)
                    dest.write_text(json.dumps(payload, allow_nan=False))
                    collected_jsons.append(str(dest))
                    accepted[child_dir].append(js)

    collection = complete_native_collection(data, accepted, authority='modules/boltzgen.nf:CollectBoltzGenOutputs')
    # Write manifest
    manifest = {
        "component_collection": collection,
        "children_processed": len(child_dirs),
        "pdbs_collected": len(collected_pdbs),
        "jsons_collected": len(collected_jsons),
        "collected_pdbs": collected_pdbs,
        "collected_jsons": collected_jsons
    }
    
    with open("collection_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    
    print(f"Collection complete: {len(collected_pdbs)} PDBs, {len(collected_jsons)} JSONs")
    """
}

process AggregateBoltzGenResults {
    label 'process_low'

    publishDir "${params.out_dir}", mode: 'copy', pattern: "aggregation_report.json"

    input:
    val parent_job_id
    path collected_pdbs
    path collected_jsons
    path manifest

    output:
    path "aggregation_report.json", emit: report

    script:
    """
    #!/bin/bash
    set -euo pipefail
    
    echo "Aggregating BoltzGen results for parent job ${parent_job_id}"
    
    # Count collected files
    PDB_COUNT=\$(ls ${collected_pdbs} 2>/dev/null | wc -l || echo 0)
    JSON_COUNT=\$(ls ${collected_jsons} 2>/dev/null | wc -l || echo 0)
    
    echo "Found \$PDB_COUNT PDBs and \$JSON_COUNT JSONs"
    
    # Native computation emits bytes only. Shared controller publication owns
    # database ingestion after authorized return (also for Local placement).
    
    # Create aggregation report
    cat > aggregation_report.json <<EOF
{
    "parent_job_id": "${parent_job_id}",
    "total_pdbs": \$PDB_COUNT,
    "total_jsons": \$JSON_COUNT,
    "status": "complete",
    "ingestion_triggered": false
}
EOF

    echo "Aggregation complete"
    """
}
