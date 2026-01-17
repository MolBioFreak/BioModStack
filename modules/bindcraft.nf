#!/usr/bin/env nextflow

/*
 * BindCraft - De Novo Minibinder Design Module
 * 
 * Uses AlphaFold2 backpropagation, ProteinMPNN, and PyRosetta
 * for automated binder design with high experimental success rates.
 * 
 * Reference: https://github.com/martinpacesa/BindCraft
 */

// =============================================================================
// PROCESS: Prepare BindCraft Input Configuration
// =============================================================================
process PrepBindCraftInput {
    label 'pyrosetta_tools'
    
    publishDir "${params.out_dir}/run/bindcraft/config", mode: 'copy', pattern: "*.json"
    
    input:
    path target_pdb
    val hotspot_residues
    val binder_lengths
    val num_final_designs
    val design_algorithm
    val chains
    val binder_name
    // Advanced settings
    val use_multimer_design
    val num_recycles_design
    val num_recycles_validation
    val mpnn_weights
    val num_mpnn_sequences
    // Filter settings
    val min_iptm
    val max_hotspot_rmsd
    // Storage optimization
    val zip_animations
    val zip_plots
    val remove_unrelaxed_trajectory
    val remove_unrelaxed_complex
    val remove_binder_monomer
    val save_trajectory_pickle
    
    output:
    path "settings_target.json", emit: target_settings
    path "settings_advanced.json", emit: advanced_settings
    path "settings_filters.json", emit: filter_settings
    path "${target_pdb}", emit: target_pdb_out
    
    script:
    """
    #!/usr/bin/env python3
    import json
    import shutil
    from pathlib import Path
    
    # Generate settings_target.json
    target_settings = {
        "design_path": "./output",
        "binder_name": "${binder_name}" or "binder",
        "starting_pdb": "${target_pdb}",
        "chains": "${chains}" or "A",
        "target_hotspot_residues": ${hotspot_residues ? "\"${hotspot_residues}\"" : "null"},
        "lengths": "${binder_lengths}" or "80-120",
        "number_of_final_designs": ${num_final_designs}
    }
    
    with open("settings_target.json", "w") as f:
        json.dump(target_settings, f, indent=2)
    
    # Generate settings_advanced.json
    advanced_settings = {
        "design_algorithm": "${design_algorithm}",
        "use_multimer_design": ${use_multimer_design},
        "num_recycles_design": ${num_recycles_design},
        "num_recycles_validation": ${num_recycles_validation},
        "sample_models": True,
        # MPNN settings
        "mpnn_weights": "${mpnn_weights}",
        "num_seqs": ${num_mpnn_sequences},
        "max_mpnn_sequences": 4,
        "sampling_temp": 0.1,
        "mpnn_fix_interface": False,
        # Template settings (default flexibility)
        "rm_template_seq_design": False,
        "rm_template_seq_predict": False,
        "rm_template_sc_design": False,
        "rm_template_sc_predict": False,
        # Prediction settings
        "predict_initial_guess": False,
        "predict_bigbang": False,
        # Iteration counts (4stage defaults)
        "soft_iterations": 100,
        "temporary_iterations": 25,
        "hard_iterations": 25,
        "greedy_iterations": 25,
        "greedy_percentage": 0.1,
        # Design weights (BindCraft defaults)
        "weights_plddt": 0.1,
        "weights_pae_intra": 0.1,
        "weights_pae_inter": 0.5,
        "weights_con_intra": 0.5,
        "weights_con_inter": 1.0,
        "intra_contact_distance": 8,
        "inter_contact_distance": 8,
        "intra_contact_number": 2,
        "inter_contact_number": 3,
        "weights_helicity": 0,
        "random_helicity": False,
        # Additional losses
        "use_i_ptm_loss": True,
        "weights_iptm": 0.1,
        "use_rg_loss": True,
        "weights_rg": 0.1,
        "use_termini_distance_loss": False,
        "weights_termini_loss": 0,
        # Storage optimization (from UI)
        "zip_animations": ${zip_animations},
        "zip_plots": ${zip_plots},
        "remove_unrelaxed_trajectory": ${remove_unrelaxed_trajectory},
        "remove_unrelaxed_complex": ${remove_unrelaxed_complex},
        "remove_binder_monomer": ${remove_binder_monomer},
        "save_trajectory_pickle": ${save_trajectory_pickle},
        # Runtime limits
        "max_trajectories": 10000,
        "acceptance_rate": 0.01,
        "start_monitoring": 100,
        # Debug settings
        "enable_mpnn": True,
        "enable_rejection_check": True
    }
    
    with open("settings_advanced.json", "w") as f:
        json.dump(advanced_settings, f, indent=2)
    
    # Generate settings_filters.json (BindCraft default filters)
    filter_settings = {
        # AlphaFold2 confidence metrics
        "pLDDT": {"threshold": 0.8, "higher": True},
        "pTM": {"threshold": 0.6, "higher": True},
        "i_pTM": {"threshold": ${min_iptm}, "higher": True},
        "pAE": {"threshold": 0.4, "higher": False},
        "i_pAE": {"threshold": 0.3, "higher": False},
        "i_pLDDT": {"threshold": 0.8, "higher": True},
        "ss_pLDDT": {"threshold": 0.7, "higher": True},
        # Rosetta metrics
        "Unrelaxed_Clashes": {"threshold": 5, "higher": False},
        "Relaxed_Clashes": {"threshold": 0, "higher": False},
        "Binder_Energy_Score": {"threshold": -10, "higher": False},
        "Surface_Hydrophobicity": {"threshold": 0.4, "higher": False},
        "ShapeComplementarity": {"threshold": 0.55, "higher": True},
        "PackStat": {"threshold": 0.55, "higher": True},
        "dG": {"threshold": -15, "higher": False},
        "dSASA": {"threshold": 600, "higher": True},
        "dG/dSASA": {"threshold": -0.02, "higher": False},
        "Interface_SASA_%": {"threshold": 10, "higher": True},
        "Interface_Hydrophobicity": {"threshold": 0.45, "higher": False},
        "n_InterfaceResidues": {"threshold": 10, "higher": True},
        "n_InterfaceHbonds": {"threshold": 3, "higher": True},
        "InterfaceHbondsPercentage": {"threshold": 0.2, "higher": True},
        "n_InterfaceUnsatHbonds": {"threshold": 5, "higher": False},
        "InterfaceUnsatHbondsPercentage": {"threshold": 0.3, "higher": False},
        # Structural validation
        "HotspotRMSD": {"threshold": ${max_hotspot_rmsd}, "higher": False},
        "Target_RMSD": {"threshold": 2.5, "higher": False},
        "Binder_pLDDT": {"threshold": 0.75, "higher": True},
        "Binder_pTM": {"threshold": 0.6, "higher": True},
        "Binder_pAE": {"threshold": 0.35, "higher": False},
        "Binder_RMSD": {"threshold": 2.5, "higher": False}
    }
    
    with open("settings_filters.json", "w") as f:
        json.dump(filter_settings, f, indent=2)
    
    print(f"Generated BindCraft configuration files")
    print(f"  Target: ${target_pdb}")
    print(f"  Hotspots: ${hotspot_residues ?: 'auto-detect'}")
    print(f"  Binder lengths: ${binder_lengths}")
    print(f"  Algorithm: ${design_algorithm}")
    """
}

// =============================================================================
// PROCESS: Run BindCraft Design Loop
// =============================================================================
process RunBindCraft {
    label 'BindCraft'
    label 'gpu'
    
    // Publish outputs
    publishDir "${params.out_dir}/run/bindcraft", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/bindcraft/trajectories", mode: 'copy', pattern: "output/Trajectory/*.pdb"
    publishDir "${params.out_dir}/run/bindcraft/mpnn", mode: 'copy', pattern: "output/MPNN/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "output/Accepted/*.pdb"
    publishDir "${params.out_dir}/run/bindcraft/stats", mode: 'copy', pattern: "output/*.csv"
    
    // Container with AF2 weights mounted
    container 'apptainer/bindcraft.sif'
    containerOptions { "--nv --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${task.ext.gpu_id ?: 0} --bind /mnt/BioModStack/weights/alphafold:/app/params --writable-tmpfs" }
    
    input:
    path target_settings
    path advanced_settings
    path filter_settings
    path target_pdb
    val job_id
    
    output:
    path "output/Accepted/*.pdb", emit: accepted_pdbs, optional: true
    path "output/final_design_stats.csv", emit: stats, optional: true
    path "output/trajectory_stats.csv", emit: trajectory_stats, optional: true
    path "output/mpnn_design_stats.csv", emit: mpnn_stats, optional: true
    path "*.log", emit: logs
    
    script:
    """
    set -euo pipefail
    
    echo "=== BindCraft De Novo Binder Design ===" | tee bindcraft_${job_id}.log
    echo "Target: ${target_pdb}" | tee -a bindcraft_${job_id}.log
    echo "Job ID: ${job_id}" | tee -a bindcraft_${job_id}.log
    echo "GPU: \${CUDA_VISIBLE_DEVICES:-0}" | tee -a bindcraft_${job_id}.log
    date | tee -a bindcraft_${job_id}.log
    
    # Copy target PDB to expected location
    mkdir -p input
    cp ${target_pdb} input/
    
    # Update target settings with absolute path
    python3 -c "
import json
with open('${target_settings}') as f:
    settings = json.load(f)
settings['starting_pdb'] = 'input/${target_pdb}'
settings['design_path'] = 'output'
with open('settings_target.json', 'w') as f:
    json.dump(settings, f, indent=2)
"
    
    # Run BindCraft
    cd /app/bindcraft
    
    python3 bindcraft.py \\
        --settings \${OLDPWD}/settings_target.json \\
        --filters \${OLDPWD}/${filter_settings} \\
        --advanced \${OLDPWD}/${advanced_settings} \\
        2>&1 | tee -a \${OLDPWD}/bindcraft_${job_id}.log
    
    cd \${OLDPWD}
    
    # Move outputs to expected location
    if [ -d "/app/bindcraft/output" ]; then
        mv /app/bindcraft/output/* output/ 2>/dev/null || true
    fi
    
    echo "=== BindCraft Complete ===" | tee -a bindcraft_${job_id}.log
    ls -la output/ 2>/dev/null | tee -a bindcraft_${job_id}.log || echo "No output directory"
    ls -la output/Accepted/ 2>/dev/null | tee -a bindcraft_${job_id}.log || echo "No accepted designs"
    """
}

// =============================================================================
// PROCESS: Filter and Rank BindCraft Results
// =============================================================================
process FilterBindCraft {
    label 'pyrosetta_tools'
    
    publishDir "${params.out_dir}/run/filter_bindcraft", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/filter_bindcraft", mode: 'copy', pattern: "filtered/*.json"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "filtered/*.pdb"
    
    input:
    path accepted_pdbs
    path stats_csv
    val budget
    val alpha
    
    output:
    path "filtered/*.pdb", emit: pdbs
    path "filtered/filter_summary.json", emit: summary, optional: true
    path "filtered/ranked_designs.csv", emit: ranked_csv, optional: true
    path "*.log"
    
    script:
    def budgetArg = budget ? "--budget ${budget}" : ""
    def alphaArg = alpha ? "--alpha ${alpha}" : "--alpha 0.01"
    
    """
    #!/usr/bin/env python3
    import json
    import shutil
    import pandas as pd
    from pathlib import Path
    import sys
    
    print("=== BindCraft Results Filter ===")
    
    # Parse input
    stats_file = Path("${stats_csv}")
    pdb_files = list(Path(".").glob("*.pdb"))
    budget = ${budget ?: 'None'}
    alpha = ${alpha ?: 0.01}
    
    print(f"Stats file: {stats_file}")
    print(f"PDB files found: {len(pdb_files)}")
    print(f"Budget: {budget}")
    print(f"Alpha (diversity weight): {alpha}")
    
    # Create output directory
    Path("filtered").mkdir(exist_ok=True)
    
    if not stats_file.exists():
        print("Warning: No stats file found, copying all PDBs")
        for pdb in pdb_files:
            shutil.copy(pdb, f"filtered/{pdb.name}")
        sys.exit(0)
    
    # Load and rank by i_pTM (BindCraft primary ranking metric)
    try:
        df = pd.read_csv(stats_file)
        print(f"Loaded {len(df)} designs from stats")
        
        # Sort by i_pTM descending (higher is better)
        if 'i_pTM' in df.columns:
            df = df.sort_values('i_pTM', ascending=False)
        elif 'Average_i_pTM' in df.columns:
            df = df.sort_values('Average_i_pTM', ascending=False)
        
        # Apply budget if specified
        if budget and budget < len(df):
            df = df.head(budget)
            print(f"Applied budget: keeping top {budget} designs")
        
        # Save ranked CSV
        df.to_csv("filtered/ranked_designs.csv", index=False)
        
        # Copy corresponding PDBs
        copied = 0
        for _, row in df.iterrows():
            # Try to find matching PDB by design name
            design_name = row.get('Design', row.get('design_name', ''))
            for pdb in pdb_files:
                if design_name in pdb.stem or pdb.stem in str(design_name):
                    shutil.copy(pdb, f"filtered/{pdb.name}")
                    copied += 1
                    break
        
        print(f"Copied {copied} ranked PDB files")
        
        # Generate summary
        summary = {
            "total_designs": len(df),
            "budget_applied": budget,
            "top_iptm": float(df.iloc[0].get('i_pTM', df.iloc[0].get('Average_i_pTM', 0))) if len(df) > 0 else 0,
            "avg_iptm": float(df['i_pTM'].mean() if 'i_pTM' in df.columns else df.get('Average_i_pTM', pd.Series([0])).mean()),
            "designs_output": copied
        }
        
        with open("filtered/filter_summary.json", "w") as f:
            json.dump(summary, f, indent=2)
            
    except Exception as e:
        print(f"Error processing stats: {e}")
        # Fall back to copying all PDBs
        for pdb in pdb_files:
            shutil.copy(pdb, f"filtered/{pdb.name}")
    
    print("=== Filter Complete ===")
    """ > filter_bindcraft.log 2>&1
    cat filter_bindcraft.log
    """
}
