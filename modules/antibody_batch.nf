process BatchBoltzValidation {
    label 'Boltz'
    label 'gpu'
    container 'apptainer/boltz.sif'
    
    input:
    path pdbs   // List of PDB files
    path msa    // Shared MSA file
    
    output:
    path "predictions/*.pdb", emit: pdbs
    path "predictions/*.json", emit: scores
    path "boltz_batch.log"
    
    script:
    """
    mkdir -p yamls predictions
    
    # Generate YAML config for each PDB sequence
    python3 ${projectDir}/scripts/prep_boltz_batch.py \\
        --pdb_files ${pdbs} \\
        --msa_path ${msa} \\
        --out_dir yamls
        
    # Run Boltz on the directory of YAMLs (Batch Mode)
    # This loads the model ONCE and processes all sequences
    # Using specific cache directory to avoid conflicts
    export BOLTZ_CACHE_DIR="\$(pwd)/.boltz_cache"
    export NUMBA_CACHE_DIR="\$(pwd)/.numba_cache"
    export XDG_CACHE_HOME="\$(pwd)/.cache_home"
    export HOME="\$(pwd)/.fake_home"
    mkdir -p \$BOLTZ_CACHE_DIR \$NUMBA_CACHE_DIR \$XDG_CACHE_HOME \$HOME
    
    boltz predict yamls/ \\
        --output_format pdb \\
        --diffusion_samples 1 \\
        --out_dir . \\
        2>&1 | tee boltz_batch.log
        
    # Move outputs
    # Boltz outputs structure: boltz_results_yamls/predictions/{name}/{name}_model_0.pdb
    mv boltz_results_yamls/predictions/*/* predictions/ 2>/dev/null || true
    """
}

process BatchImmunogenicity {
    label 'Antiberty'
    container 'apptainer/antibody_tools.sif'
    
    input:
    path pdbs
    
    output:
    path "immunogenicity_scores.csv", emit: scores
    
    script:
    """
    # Run AntiBERTy on all PDBs at once
    python3 ${projectDir}/scripts/batch_antiberty.py \\
        --pdb_files ${pdbs} \\
        --out_csv immunogenicity_scores.csv
    """
}

process BatchStability {
    label 'ThermoMPNN'
    container 'apptainer/stability_tools.sif'
    
    input:
    path pdbs
    
    output:
    path "stability_scores.csv", emit: scores
    
    script:
    """
    # Loop over PDBs (ThermoMPNN is fast, loop is fine)
    echo "file,score" > stability_scores.csv
    
    for pdb in ${pdbs}; do
        python3 /opt/ThermoMPNN/analysis/custom_inference.py \\
            --pdb "\$pdb" \\
            --model_path /opt/ThermoMPNN/models/thermoMPNN_default.pt \\
            >> stability_scores.csv
    done
    """
}
