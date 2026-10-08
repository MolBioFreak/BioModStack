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
        ${covalent_bonds ? "--covalent_bonds '${covalent_bonds}'" : ''} \\
        ${nanobody_framework ? "--nanobody_framework '${nanobody_framework}'" : ''} \\
        ${cdr_h1_length ? "--cdr_h1_length '${cdr_h1_length}'" : ''} \\
        ${cdr_h2_length ? "--cdr_h2_length '${cdr_h2_length}'" : ''} \\
        ${cdr_h3_length ? "--cdr_h3_length '${cdr_h3_length}'" : ''} \\
        ${input_pdb.name != 'NO_INPUT_PDB' ? "--input_pdb '${input_pdb}'" : ''} \\
        ${ligand_pdb.name != 'NO_LIGAND_PDB' ? "--ligand_pdb '${ligand_pdb}'" : ''} \\
        ${dna_structure.name != 'NO_DNA_STRUCT' ? "--dna_structure '${dna_structure}'" : ''} \\
        ${target_pdb.name != 'NO_TARGET_PDB' ? "--target_pdb '${target_pdb}'" : ''} \\
        --output_yaml boltzgen_input.yaml

    # Validate generated YAML using boltzgen check (fail-fast on bad config)
    echo "Validating BoltzGen YAML..."
    boltzgen check boltzgen_input.yaml || {
        echo "ERROR: BoltzGen YAML validation failed"
        cat boltzgen_input.yaml
        exit 1
    }
    echo "YAML validation passed"
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
    def paramsJson = params.boltzgen_extra_params ? "'${params.boltzgen_extra_params}'" : "'{}'"
    """
    python3 ${projectDir}/scripts/spawn_boltzgen_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --total_designs ${total_designs} \\
        --designs_per_job ${designs_per_job} \\
        --yaml_config "\$(readlink -f ${yaml_config})" \\
        --target_pdb "\$(readlink -f ${target_pdb})" \\
        --mode "${mode}" \\
        --batch_name "${batch_name}" \\
        --params_json ${paramsJson} \\
        --api_url "http://localhost:8000" \\
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

    output:
    path "boltzgen_child_outputs.json", emit: result
    path "wait_boltzgen.log"

    script:
    """
    python3 ${projectDir}/scripts/wait_for_children.py \\
        --parent_job_id "${parent_job_id}" \\
        --stage "boltzgen" \\
        --poll_interval 30 \\
        --api_url "http://localhost:8000" \\
        --output boltzgen_child_outputs.json \\
        2>&1 | tee wait_boltzgen.log
    """
}

process CollectBoltzGenOutputs {
    label 'process_low'

    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "collected/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "collected/*.json"
    publishDir "${params.out_dir}/run/boltzgen_parallel", mode: 'copy', pattern: "collection_manifest.json"

    input:
    path child_outputs_json

    output:
    path "collected/*.pdb", emit: pdbs, optional: true
    path "collected/*.json", emit: jsons, optional: true
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
                    print(f"Collected: {pdb} -> {dest}")
            
            for js in search_path.glob("confidence_*.json"):
                dest = Path("collected") / f"job{job_idx}_{js.name}"
                if not dest.exists():
                    shutil.copy(js, dest)
                    collected_jsons.append(str(dest))
    
    # Write manifest
    manifest = {
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
    
    # Trigger result ingestion
    if [ \$PDB_COUNT -gt 0 ]; then
        echo "Triggering result ingestion..."
        python3 ${projectDir}/scripts/result_ingester.py \\
            --job_id "${parent_job_id}" \\
            --results_dir "${params.out_dir}" \\
            --api_url "http://localhost:8000" \\
            2>&1 | tee ingest.log || echo "Warning: Ingestion had issues (non-fatal)"
    fi
    
    # Create aggregation report
    cat > aggregation_report.json <<EOF
{
    "parent_job_id": "${parent_job_id}",
    "total_pdbs": \$PDB_COUNT,
    "total_jsons": \$JSON_COUNT,
    "status": "complete",
    "ingestion_triggered": true
}
EOF

    echo "Aggregation complete"
    """
}
