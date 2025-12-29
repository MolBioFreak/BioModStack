/**
 * Dual Docking Workflow - Run DiffDock and Uni-Dock in Parallel
 * 
 * Orchestrates parallel execution of both docking engines
 * and compares results for orthogonal validation.
 */

include { PrepDiffDock ; RunDiffDock } from './diffdock'
include { PrepUniDock ; RunUniDock ; FilterUniDock } from './unidock'


/**
 * Compare poses between DiffDock and Uni-Dock
 */
process CompareDockingPoses {
    tag "compare"
    label 'pyrosetta_tools'

    publishDir "${params.out_dir}/run/docking_comparison", mode: 'copy'

    input:
    path diffdock_results
    path unidock_results
    val rmsd_threshold

    output:
    path "comparison.json", emit: comparison
    path "consensus_poses", emit: consensus_dir, optional: true
    path "*.log", emit: logs

    script:
    def threshold = rmsd_threshold ?: 2.0
    """
    set -euo pipefail
    
    echo "=== Comparing Docking Poses ===" | tee compare_docking.log
    echo "DiffDock dir: ${diffdock_results}" | tee -a compare_docking.log
    echo "Uni-Dock dir: ${unidock_results}" | tee -a compare_docking.log
    echo "RMSD threshold: ${threshold}" | tee -a compare_docking.log
    
    mkdir -p consensus_poses
    
    python3 /scripts/compare_docking_poses.py \\
        --diffdock_dir ${diffdock_results} \\
        --unidock_dir ${unidock_results} \\
        --rmsd_threshold ${threshold} \\
        --output comparison.json \\
        --consensus_dir consensus_poses \\
        2>&1 | tee -a compare_docking.log
    
    echo "Comparison complete" | tee -a compare_docking.log
    cat comparison.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Agreements: {d[\"summary\"][\"total_agreements\"]} ({d[\"summary\"][\"agreement_rate\"]:.1%})')" | tee -a compare_docking.log
    """
}


/**
 * Main Dual Docking Workflow
 * 
 * Runs both DiffDock and Uni-Dock in parallel on the same inputs,
 * then compares the results.
 */
workflow DualDocking {
    take:
    pdbs // Input PDB files
    ligand_smiles // Ligand SMILES string
    ntp_type // NTP template type (optional)

    main:
    println("=== Running Dual Docking (DiffDock + Uni-Dock) ===")

    // Prepare DiffDock inputs
    PrepDiffDock(pdbs, ligand_smiles, ntp_type)

    // Prepare Uni-Dock inputs
    PrepUniDock(
        pdbs,
        ligand_smiles,
        ntp_type,
        params.unidock_box_size ?: 25,
        params.unidock_box_center ?: '',
        params.unidock_flexible_residues ?: '',
    )

    // Run DiffDock
    diffdock_input = PrepDiffDock.out.csv
        .combine(PrepDiffDock.out.pdbs.collect().map { files -> [files] })
        .map { csv, pdbs_list -> tuple("diffdock_0", csv, pdbs_list) }

    RunDiffDock(diffdock_input)

    // Run Uni-Dock
    flex_receptor = PrepUniDock.out.flex_receptor.ifEmpty(file('NO_FLEX'))

    unidock_input = PrepUniDock.out.receptor
        .combine(flex_receptor)
        .combine(PrepUniDock.out.ligand_dir)
        .combine(PrepUniDock.out.box)
        .map { receptor, flex, ligands, box ->
            tuple("unidock_0", receptor, flex, ligands, box)
        }

    RunUniDock(unidock_input)

    // Filter Uni-Dock results
    FilterUniDock(RunUniDock.out.poses.collect(), RunUniDock.out.scores)

    // Compare results
    CompareDockingPoses(
        RunDiffDock.out.sdfs.collect().map { it -> it[0].parent },
        FilterUniDock.out.pdbs.collect().map { it -> it[0].parent },
        params.rmsd_agreement_threshold ?: 2.0,
    )

    emit:
    diffdock_poses = RunDiffDock.out.sdfs
    unidock_poses = FilterUniDock.out.pdbs
    comparison = CompareDockingPoses.out.comparison
    consensus = CompareDockingPoses.out.consensus_dir
}
