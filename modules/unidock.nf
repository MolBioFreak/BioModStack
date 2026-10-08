/**
 * Uni-Dock GPU-Accelerated Molecular Docking Module
 * 
 * Provides physics-based docking using AutoDock Vina scoring functions
 * with GPU acceleration (2000x speedup over CPU Vina).
 * 
 * Processes:
 *   - PrepUniDock: Convert PDB to PDBQT, generate ligands, compute box
 *   - RunUniDock: Execute GPU docking
 *   - FilterUniDock: Filter by affinity, convert to PDB
 */

process PrepUniDock {
    tag "${pdbs}"
    label 'pyrosetta_tools'

    publishDir "${params.out_dir}/run/unidock/prep", mode: 'copy', pattern: '*.log'

    input:
    path pdbs
    val ligand_smiles
    val ntp_type
    val box_size
    val box_center
    val flexible_residues

    output:
    path "receptor.pdbqt", emit: receptor
    path "receptor_flex.pdbqt", emit: flex_receptor, optional: true
    path "ligands", emit: ligand_dir
    path "box_params.json", emit: box
    path "*.log", emit: logs

    script:
    def smilesArg = ligand_smiles ? "--ligand_smiles '${ligand_smiles}'" : ''
    def ntpArg = ntp_type ? "--ntp_type '${ntp_type}'" : ''
    def boxCenterArg = box_center ? "--box_center '${box_center}'" : ''
    def flexArg = flexible_residues ? "--flexible_residues '${flexible_residues}'" : ''
    def boxSizeVal = box_size ?: 25
    """
    set -euo pipefail
    
    echo "=== Uni-Dock Preparation ===" | tee prep_unidock.log
    echo "Input PDB: ${pdbs}" | tee -a prep_unidock.log
    echo "Ligand SMILES: ${ligand_smiles ?: 'None'}" | tee -a prep_unidock.log
    echo "NTP Type: ${ntp_type ?: 'None'}" | tee -a prep_unidock.log
    echo "Box Size: ${boxSizeVal}" | tee -a prep_unidock.log
    echo "Flexible Residues: ${flexible_residues ?: 'None'}" | tee -a prep_unidock.log
    
    python3 /scripts/prep_unidock.py \\
        --input_pdb ${pdbs} \\
        ${smilesArg} \\
        ${ntpArg} \\
        --box_size ${boxSizeVal} \\
        ${boxCenterArg} \\
        ${flexArg} \\
        --out_dir . \\
        2>&1 | tee -a prep_unidock.log
    
    echo "Preparation complete" | tee -a prep_unidock.log
    ls -la | tee -a prep_unidock.log
    """
}


process RunUniDock {
    tag "${batch_id}"
    label 'UniDock'
    label 'gpu'

    publishDir "${params.out_dir}/run/unidock/results", mode: 'copy'

    input:
    tuple val(batch_id), path(receptor), path(flex_receptor), path(ligand_dir), path(box)

    output:
    path "poses/*.pdbqt", emit: poses
    path "scores.csv", emit: scores
    path "*.log", emit: logs

    script:
    def numPoses = params.unidock_num_poses ?: 9
    def scoring = params.unidock_scoring ?: 'vina'
    def exhaustiveness = params.unidock_exhaustiveness ?: 32
    // Handle optional flexible receptor
    def hasFlexReceptor = flex_receptor.name != 'NO_FLEX' && flex_receptor.exists()
    def flexArg = hasFlexReceptor ? "--flex ${flex_receptor}" : ''
    """
    set -euo pipefail
    
    echo "=== Uni-Dock GPU Docking ===" | tee unidock_${batch_id}.log
    echo "Batch ID: ${batch_id}" | tee -a unidock_${batch_id}.log
    echo "Receptor: ${receptor}" | tee -a unidock_${batch_id}.log
    echo "Flexible receptor: ${hasFlexReceptor ? flex_receptor : 'None'}" | tee -a unidock_${batch_id}.log
    echo "Scoring: ${scoring}" | tee -a unidock_${batch_id}.log
    echo "Exhaustiveness: ${exhaustiveness}" | tee -a unidock_${batch_id}.log
    echo "Num poses: ${numPoses}" | tee -a unidock_${batch_id}.log
    
    # Parse box parameters from JSON
    BOX_PARAMS=\$(python3 -c "
import json
with open('${box}') as f:
    b = json.load(f)
print(f'--center_x {b[\"cx\"]} --center_y {b[\"cy\"]} --center_z {b[\"cz\"]} --size_x {b[\"sx\"]} --size_y {b[\"sy\"]} --size_z {b[\"sz\"]}')
")
    echo "Box params: \$BOX_PARAMS" | tee -a unidock_${batch_id}.log
    
    # Create ligand index file
    find ${ligand_dir} -name "*.pdbqt" > ligand_list.txt
    echo "Found \$(wc -l < ligand_list.txt) ligands" | tee -a unidock_${batch_id}.log
    
    mkdir -p poses
    
    # Run Uni-Dock GPU docking
    unidock \\
        --receptor ${receptor} \\
        ${flexArg} \\
        --ligand_index ligand_list.txt \\
        --dir poses \\
        --scoring ${scoring} \\
        --exhaustiveness ${exhaustiveness} \\
        --num_modes ${numPoses} \\
        \$BOX_PARAMS \\
        2>&1 | tee -a unidock_${batch_id}.log
    
    echo "Docking complete, parsing scores..." | tee -a unidock_${batch_id}.log
    
    # Parse scores from output PDBQT files
    python3 /scripts/parse_unidock_scores.py poses/ > scores.csv
    
    echo "Scores extracted:" | tee -a unidock_${batch_id}.log
    head scores.csv | tee -a unidock_${batch_id}.log
    """
}


process FilterUniDock {
    tag "filter"
    label 'pyrosetta_tools'

    publishDir "${params.out_dir}/run/unidock/filtered", mode: 'copy'

    input:
    path poses
    path scores

    output:
    path "*.pdb", emit: pdbs, optional: true
    path "scores.json", emit: json
    path "*.log", emit: logs

    script:
    def threshold = params.unidock_affinity_threshold ?: -7.0
    """
    set -euo pipefail
    
    echo "=== Uni-Dock Filtering ===" | tee filter_unidock.log
    echo "Affinity threshold: ${threshold} kcal/mol" | tee -a filter_unidock.log
    
    python3 /scripts/filter_unidock.py \\
        --poses_dir . \\
        --scores_csv ${scores} \\
        --affinity_threshold ${threshold} \\
        --out_dir . \\
        2>&1 | tee -a filter_unidock.log
    
    echo "Filtering complete" | tee -a filter_unidock.log
    echo "PDB files generated:" | tee -a filter_unidock.log
    ls -la *.pdb 2>/dev/null | tee -a filter_unidock.log || echo "No PDB files passed threshold"
    """
}


/**
 * Convenience workflow for standalone Uni-Dock docking
 */
workflow UniDock {
    take:
    pdbs
    ligand_smiles
    ntp_type

    main:
    // Prepare inputs
    PrepUniDock(
        pdbs,
        ligand_smiles,
        ntp_type,
        params.unidock_box_size ?: 25,
        params.unidock_box_center ?: '',
        params.unidock_flexible_residues ?: '',
    )

    // Handle optional flex receptor - provide placeholder if not present
    flex_receptor = PrepUniDock.out.flex_receptor.ifEmpty(file('NO_FLEX'))

    // Prepare tuple for RunUniDock
    docking_input = PrepUniDock.out.receptor
        .combine(flex_receptor)
        .combine(PrepUniDock.out.ligand_dir)
        .combine(PrepUniDock.out.box)
        .map { receptor, flex, ligands, box ->
            tuple("unidock_0", receptor, flex, ligands, box)
        }

    // Run GPU docking
    RunUniDock(docking_input)

    // Filter results
    FilterUniDock(RunUniDock.out.poses.collect(), RunUniDock.out.scores)

    emit:
    poses = FilterUniDock.out.pdbs
    scores = FilterUniDock.out.json
    raw_poses = RunUniDock.out.poses
    raw_scores = RunUniDock.out.scores
}
