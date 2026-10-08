process BatchBoltzValidation {
    label 'Boltz'
    label 'gpu'
    container "${params.container_dir}/boltz2.sif"

    // CRITICAL: Publish validated structures and confidence scores to output directory
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.cif"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.json"
    publishDir "${params.out_dir}/run/boltz", mode: 'copy', pattern: "*.log"

    input:
    path pdbs
    // List of PDB files
    path msa

    output:
    path "predictions/*.pdb", emit: pdbs
    path "predictions/*.json", emit: scores
    path "boltz_batch.log"

    script:
    """
    set -euo pipefail
    shopt -s nullglob

    mkdir -p yamls predictions
    
    # Generate YAML config for each PDB sequence
    python3 ${params.code_root}/scripts/prep_boltz_batch.py \\
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
        ${params.boltz_max_parallel_samples ? '--max_parallel_samples ' + params.boltz_max_parallel_samples : ''} \\
        --out_dir . \\
        2>&1 | tee boltz_batch.log
        
    # Move and rename outputs for align_boltz.py to find them
    # Boltz outputs structure: boltz_results_yamls/predictions/{name}/{name}_model_0.pdb
    # align_boltz.py expects: {name}_boltzpred.pdb and {name}_boltzpred.json
    boltz_dirs=(boltz_results_yamls/predictions/*)
    if [ \${#boltz_dirs[@]} -eq 0 ]; then
        echo "[BatchBoltzValidation] ERROR: No Boltz prediction directories found in boltz_results_yamls/predictions" >&2
        exit 1
    fi

    boltz_pdb_count=0
    boltz_json_count=0
    for dir in "\${boltz_dirs[@]}"; do
        [ -d "\$dir" ] || continue
        name="\$(basename "\$dir")"

        pdb_src="\$dir/\${name}_model_0.pdb"
        if [ -f "\$pdb_src" ]; then
            cp "\$pdb_src" "\${name}_boltzpred.pdb"
            boltz_pdb_count=\$((boltz_pdb_count + 1))
        fi

        json_src=""
        if [ -f "\$dir/confidence_\${name}_model_0.json" ]; then
            json_src="\$dir/confidence_\${name}_model_0.json"
        elif [ -f "\$dir/\${name}_model_0.json" ]; then
            json_src="\$dir/\${name}_model_0.json"
        fi

        if [ -n "\$json_src" ]; then
            cp "\$json_src" "\${name}_boltzpred.json"
            boltz_json_count=\$((boltz_json_count + 1))
        fi
    done

    if [ "\$boltz_pdb_count" -eq 0 ]; then
        echo "[BatchBoltzValidation] ERROR: No Boltz PDB predictions were copied for RMSD alignment" >&2
        exit 1
    fi
    echo "[BatchBoltzValidation] Prepared \$boltz_pdb_count Boltz PDBs and \$boltz_json_count confidence JSONs for alignment"

    # We need access to the original un-repacked templates to calculate RMSD
    # The originals are in 'pdbs' (the input chunk).
    mkdir -p original_designs
    cp \${pdbs} original_designs/
    original_design_count=\$(find original_designs -maxdepth 1 -name '*.pdb' | wc -l)
    if [ "\$original_design_count" -eq 0 ]; then
        echo "[BatchBoltzValidation] ERROR: No original design PDBs were staged for RMSD alignment" >&2
        exit 1
    fi

    export MAMBA_ROOT_PREFIX=/opt/conda/
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    # Run alignment script
    python3 ${params.code_root}/scripts/align_boltz.py \\
        --design_dir ./original_designs \\
        --boltz_dir ./ \\
        --output_dir predictions \\
        --design_type binder \\
        --binder_chains "${params.antibody_chains ?: 'H,L'}" \\
        --target_chains "${params.antigen_chains ?: 'T'}" \\
        --ncpus ${task.cpus} \\
        2>&1 | tee alignment_batch.log

    aligned_pdb_count=\$(find predictions -maxdepth 1 -name '*.pdb' | wc -l)
    aligned_json_count=\$(find predictions -maxdepth 1 -name '*.json' | wc -l)
    echo "[BatchBoltzValidation] Alignment outputs: pdb=\$aligned_pdb_count json=\$aligned_json_count"
    if [ "\$aligned_pdb_count" -eq 0 ] || [ "\$aligned_json_count" -eq 0 ]; then
        echo "[BatchBoltzValidation] ERROR: RMSD alignment produced no usable PDB/JSON outputs" >&2
        exit 1
    fi
    """
}

process BatchImmunogenicity {
    label 'Antiberty'
    container "${params.container_dir}/antibody_tools.sif"

    input:
    path pdbs

    output:
    path "immunogenicity_scores.csv", emit: scores

    script:
    """
    # Run AntiBERTy on all PDBs at once
    python3 ${params.code_root}/scripts/batch_antiberty.py \\
        --pdb_files ${pdbs} \\
        --out_csv immunogenicity_scores.csv
    """
}

process BatchStability {
    label 'ThermoMPNN'
    container "${params.container_dir}/stability_tools.sif"

    input:
    path pdbs

    output:
    path "stability_scores.csv", emit: scores

    script:
    """
    # ThermoMPNN custom_inference.py expects ../local.yaml relative to the analysis/ folder
    # Copy config to make relative path work
    mkdir -p analysis_run
    cp /opt/ThermoMPNN/local.yaml ./local.yaml
    
    # Loop over PDBs (ThermoMPNN is fast, loop is fine)
    echo "file,score" > stability_scores.csv
    
    for pdb in ${pdbs}; do
        cd analysis_run
        python3 /opt/ThermoMPNN/analysis/custom_inference.py \\
            --pdb "../\$pdb" \\
            --model_path /opt/ThermoMPNN/models/thermoMPNN_default.pt \\
            --out_dir ../ \\
            >> ../stability_scores.csv 2>&1 || echo "\$pdb,error" >> ../stability_scores.csv
        cd ..
    done
    """
}
