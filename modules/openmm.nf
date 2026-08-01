/**
 * OpenMM Physics Refinement Module
 * 
 * Provides physics-based structure relaxation and binding affinity scoring
 * for AI-generated protein designs.
 * 
 * Processes:
 *   - OpenMMRelaxation: Energy minimization with domain-aware restraints
 *   - OpenMMScore: MM-GBSA binding free energy calculation
 * 
 * Features:
 *   - CDR-only mode for antibody/nanobody workflows
 *   - Framework restraints to preserve validated geometry
 *   - Compute tiers: Fast (minimize), Standard (+equilibration), Full (+MM-GBSA)
 *   - Force field priority: AMBER14SB → MACE-OFF → ANI-2x
 */

/**
 * Energy minimization with optional CDR-only mode and framework restraints.
 * 
 * Inputs:
 *   - pdbs: Collection of PDB files to relax
 *   - compute_tier: 'fast', 'standard', or 'full'
 *   - cdr_only: Boolean for antibody CDR-only mode
 *   - restraint_mode: 'none', 'framework', or 'backbone'
 *   - antibody_chain: Chain ID for antibody (default: 'H')
 *   - force_field: 'amber14sb' or 'charmm36m'
 * 
 * Outputs:
 *   - relaxed_pdbs: Energy-minimized PDB files
 *   - metrics_json: JSON files with energy metrics
 *   - logs: Process logs
 */
process OpenMMRelaxation {
    tag "${batch_id}"
    label 'OpenMM'
    label 'gpu'
    
    publishDir "${params.out_dir}/run/openmm/relaxation", mode: 'copy', pattern: '*.pdb'
    publishDir "${params.out_dir}/run/openmm/relaxation", mode: 'copy', pattern: '*.json'
    publishDir "${params.out_dir}/run/openmm/relaxation", mode: 'copy', pattern: '*.log'
    
    input:
    tuple val(batch_id), path(pdbs)
    val compute_tier
    val cdr_only
    val restraint_mode
    val antibody_chain
    val force_field
    
    output:
    path "relaxed/*.pdb", emit: relaxed_pdbs, optional: true
    tuple val(batch_id), path("relaxed/*.pdb"), emit: relaxed_with_batch, optional: true
    path "relaxed/*.json", emit: metrics_json, optional: true
    path "*.log", emit: logs
    path "openmm_metadata_${batch_id}.jsonl", emit: metadata, optional: true, topic: metadata_ch_openmm
    
    script:
    // Compute tier settings
    def maxIterations = compute_tier == 'fast' ? 100 : (compute_tier == 'standard' ? 500 : 1000)
    def energyTolerance = compute_tier == 'fast' ? 50.0 : (compute_tier == 'standard' ? 10.0 : 1.0)
    
    // CDR-only and restraint flags
    def cdrFlag = cdr_only ? '--cdr_only' : ''
    def restraintFlag = restraint_mode != 'none' ? "--restraint_mode ${restraint_mode}" : ''
    
    """
    set -euo pipefail
    
    echo "=== OpenMM Energy Minimization ===" | tee openmm_relax_${batch_id}.log
    echo "Batch ID: ${batch_id}" | tee -a openmm_relax_${batch_id}.log
    echo "Compute tier: ${compute_tier}" | tee -a openmm_relax_${batch_id}.log
    echo "Max iterations: ${maxIterations}" | tee -a openmm_relax_${batch_id}.log
    echo "CDR-only mode: ${cdr_only}" | tee -a openmm_relax_${batch_id}.log
    echo "Restraint mode: ${restraint_mode}" | tee -a openmm_relax_${batch_id}.log
    echo "Force field: ${force_field}" | tee -a openmm_relax_${batch_id}.log
    
    mkdir -p relaxed
    
    # Process each PDB
    for pdb in ${pdbs}; do
        basename=\$(basename "\$pdb" .pdb)
        echo "Processing: \$basename" | tee -a openmm_relax_${batch_id}.log
        
        python3 /scripts/relax_openmm.py \\
            --input "\$pdb" \\
            --output "relaxed/\${basename}_relaxed.pdb" \\
            --output_json "relaxed/\${basename}_openmm.json" \\
            --force_field ${force_field} \\
            --max_iterations ${maxIterations} \\
            --energy_tolerance ${energyTolerance} \\
            ${cdrFlag} \\
            ${restraintFlag} \\
            --antibody_chain ${antibody_chain} \\
            --fix_structure \\
            2>&1 | tee -a openmm_relax_${batch_id}.log || {
                echo "Warning: Failed to relax \$basename" | tee -a openmm_relax_${batch_id}.log
            }
    done
    
    # Combine metrics into JSONL for metadata ingestion
    echo "Generating metadata JSONL..." | tee -a openmm_relax_${batch_id}.log
    python3 << 'PYEOF'
import json
from pathlib import Path

output_lines = []
for json_file in Path('relaxed').glob('*.json'):
    with open(json_file) as f:
        data = json.load(f)
    # Add design name from filename
    data['design_name'] = json_file.stem.replace('_openmm', '')
    output_lines.append(json.dumps(data))

with open('openmm_metadata_${batch_id}.jsonl', 'w') as f:
    f.write('\\n'.join(output_lines))
    
print(f"Generated metadata for {len(output_lines)} designs")
PYEOF
    
    echo "Relaxation complete" | tee -a openmm_relax_${batch_id}.log
    ls -la relaxed/ | tee -a openmm_relax_${batch_id}.log
    """
}


/**
 * MM-GBSA binding free energy scoring.
 * 
 * Calculates ΔG_bind for protein-protein complexes using
 * Molecular Mechanics with Generalized Born Surface Area.
 * 
 * Inputs:
 *   - pdbs: Collection of complex PDB files
 *   - mode: 'interface', 'stability', or 'both'
 *   - binder_chains: Binder chain IDs (comma-separated)
 *   - target_chains: Target chain IDs (comma-separated)
 *   - force_field: Force field name
 * 
 * Outputs:
 *   - scores_json: JSON files with MM-GBSA scores
 *   - logs: Process logs
 */
process OpenMMScore {
    tag "${batch_id}"
    label 'OpenMM'
    label 'gpu'
    
    publishDir "${params.out_dir}/run/openmm/mmgbsa", mode: 'copy', pattern: '*.json'
    publishDir "${params.out_dir}/run/openmm/mmgbsa", mode: 'copy', pattern: '*.log'
    
    input:
    tuple val(batch_id), path(pdbs)
    val scoring_mode
    val binder_chains
    val target_chains
    val force_field
    
    output:
    path "scores/*.json", emit: scores_json, optional: true
    path "*.log", emit: logs
    path "mmgbsa_metadata_${batch_id}.jsonl", emit: metadata, optional: true, topic: metadata_ch_mmgbsa
    
    script:
    """
    set -euo pipefail
    
    echo "=== OpenMM MM-GBSA Scoring ===" | tee openmm_mmgbsa_${batch_id}.log
    echo "Batch ID: ${batch_id}" | tee -a openmm_mmgbsa_${batch_id}.log
    echo "Scoring mode: ${scoring_mode}" | tee -a openmm_mmgbsa_${batch_id}.log
    echo "Binder chains: ${binder_chains}" | tee -a openmm_mmgbsa_${batch_id}.log
    echo "Target chains: ${target_chains}" | tee -a openmm_mmgbsa_${batch_id}.log
    echo "Force field: ${force_field}" | tee -a openmm_mmgbsa_${batch_id}.log
    
    mkdir -p scores work
    
    # Process each PDB
    for pdb in ${pdbs}; do
        basename=\$(basename "\$pdb" .pdb)
        echo "Scoring: \$basename" | tee -a openmm_mmgbsa_${batch_id}.log
        
        python3 /scripts/score_mmgbsa.py \\
            --mode ${scoring_mode} \\
            --complex "\$pdb" \\
            --output "scores/\${basename}_mmgbsa.json" \\
            --work_dir "work/\$basename" \\
            --binder_chains ${binder_chains} \\
            --target_chains ${target_chains} \\
            --force_field ${force_field} \\
            2>&1 | tee -a openmm_mmgbsa_${batch_id}.log || {
                echo "Warning: Failed to score \$basename" | tee -a openmm_mmgbsa_${batch_id}.log
            }
    done
    
    # Combine scores into JSONL for metadata ingestion
    echo "Generating metadata JSONL..." | tee -a openmm_mmgbsa_${batch_id}.log
    python3 << 'PYEOF'
import json
from pathlib import Path

output_lines = []
for json_file in Path('scores').glob('*.json'):
    with open(json_file) as f:
        data = json.load(f)
    data['design_name'] = json_file.stem.replace('_mmgbsa', '')
    output_lines.append(json.dumps(data))

with open('mmgbsa_metadata_${batch_id}.jsonl', 'w') as f:
    f.write('\\n'.join(output_lines))
    
print(f"Generated metadata for {len(output_lines)} designs")
PYEOF
    
    echo "MM-GBSA scoring complete" | tee -a openmm_mmgbsa_${batch_id}.log
    ls -la scores/ | tee -a openmm_mmgbsa_${batch_id}.log
    """
}


/**
 * ΔΔG calculation for mutagenesis validation.
 * 
 * Compares mutant vs wild-type binding affinity.
 * ΔΔG = ΔG_mutant - ΔG_wildtype
 * Negative values indicate improved binding.
 */
process OpenMMDeltaDeltaG {
    tag "${design_name}"
    label 'OpenMM'
    label 'gpu'
    
    publishDir "${params.out_dir}/run/openmm/ddg", mode: 'copy'
    
    input:
    tuple val(design_name), path(mutant_pdb), path(wildtype_pdb)
    val binder_chains
    val target_chains
    val force_field
    
    output:
    path "${design_name}_ddg.json", emit: ddg_json
    path "*.log", emit: logs
    
    script:
    """
    set -euo pipefail
    
    echo "=== OpenMM ΔΔG Calculation ===" | tee openmm_ddg_${design_name}.log
    echo "Mutant: ${mutant_pdb}" | tee -a openmm_ddg_${design_name}.log
    echo "Wildtype: ${wildtype_pdb}" | tee -a openmm_ddg_${design_name}.log
    
    mkdir -p work
    
    python3 /scripts/score_mmgbsa.py \\
        --mode ddg \\
        --complex ${mutant_pdb} \\
        --wildtype ${wildtype_pdb} \\
        --output ${design_name}_ddg.json \\
        --work_dir work \\
        --binder_chains ${binder_chains} \\
        --target_chains ${target_chains} \\
        --force_field ${force_field} \\
        2>&1 | tee -a openmm_ddg_${design_name}.log
    
    echo "ΔΔG calculation complete" | tee -a openmm_ddg_${design_name}.log
    cat ${design_name}_ddg.json | tee -a openmm_ddg_${design_name}.log
    """
}


/**
 * Convenience workflow for standalone OpenMM refinement.
 * Combines relaxation and optional MM-GBSA scoring.
 */
workflow OpenMMRefinement {
    take:
    pdbs                    // Channel of PDB files
    compute_tier            // 'fast', 'standard', or 'full'
    
    main:
    // Get workflow-specific defaults from params
    def cdr_only = params.openmm_cdr_only ?: false
    def restraint_mode = params.openmm_restraint_mode ?: 'none'
    def antibody_chain = params.openmm_antibody_chain ?: 'H'
    def force_field = params.openmm_force_field ?: 'amber14sb'
    def mmgbsa_mode = params.openmm_mmgbsa_mode ?: 'off'
    def binder_chains = params.openmm_binder_chains ?: 'H'
    def target_chains = params.openmm_target_chains ?: 'A'
    
    // Batch PDBs for GPU processing
    pdbs
        .collect()
        .flatten()
        .buffer(size: 10, remainder: true)
        .map { batch -> tuple("openmm_${batch.hashCode()}", batch) }
        .set { batched_pdbs }
    
    // Run relaxation
    OpenMMRelaxation(
        batched_pdbs,
        compute_tier,
        cdr_only,
        restraint_mode,
        antibody_chain,
        force_field
    )
    
    // Run MM-GBSA if requested (only for 'full' tier or explicit request)
    if (compute_tier == 'full' || mmgbsa_mode != 'off') {
        // Prepare relaxed structures for scoring
        OpenMMRelaxation.out.relaxed_pdbs
            .collect()
            .flatten()
            .buffer(size: 10, remainder: true)
            .map { batch -> tuple("mmgbsa_${batch.hashCode()}", batch) }
            .set { relaxed_for_scoring }
        
        OpenMMScore(
            relaxed_for_scoring,
            mmgbsa_mode == 'off' ? 'interface' : mmgbsa_mode,
            binder_chains,
            target_chains,
            force_field
        )
    }
    
    emit:
    relaxed_pdbs = OpenMMRelaxation.out.relaxed_pdbs
    relaxation_metrics = OpenMMRelaxation.out.metrics_json
    mmgbsa_scores = params.openmm_compute_tier == 'full' || mmgbsa_mode != 'off' 
        ? OpenMMScore.out.scores_json 
        : channel.empty()
    logs = OpenMMRelaxation.out.logs
}
