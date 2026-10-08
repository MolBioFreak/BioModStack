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

process BatchProtenixValidation {
    label 'Protenix'
    label 'gpu'
    container "${params.container_dir}/protenix.sif"

    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.cif"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "predictions/*.json"
    publishDir "${params.out_dir}/run/protenix", mode: 'copy', pattern: "*.log"

    input:
    path pdbs
    path msa

    output:
    path "predictions/*.pdb", emit: pdbs
    path "predictions/*.json", emit: scores
    path "predictions/*.cif", emit: cifs, optional: true
    path "protenix_batch.log"

    script:
    def model_name = params.protenix_model_weights ?: 'protenix_base_20250630_v1.0.0'
    def seeds = params.protenix_seeds ?: '42'
    def n_sample = params.protenix_n_sample ?: 5
    def n_step = params.protenix_n_step ?: 200
    def n_cycle = params.protenix_n_cycle ?: 10
    def use_template = (params.protenix_use_template == true || params.protenix_use_template == 'true')
    def enable_cache = (params.protenix_enable_cache == true || params.protenix_enable_cache == 'true' || params.protenix_enable_cache == null)
    def enable_fusion = (params.protenix_enable_fusion == true || params.protenix_enable_fusion == 'true' || params.protenix_enable_fusion == null)
    def use_msa = (params.protenix_use_msa == true || params.protenix_use_msa == 'true' || params.protenix_use_msa == null)
    def msa_backend = params.protenix_msa_backend ?: 'auto'
    def normalizeGpuCsv = { raw ->
        if (raw == null) return ''
        if (raw instanceof Collection) {
            return raw.collect { it?.toString()?.trim() }.findAll { it }.join(',')
        }
        def text = raw.toString().trim()
        if (text.startsWith('[') && text.endsWith(']') && text.length() >= 2) {
            text = text.substring(1, text.length() - 1)
        }
        return text
    }
    def msa_preferred_gpu_csv = normalizeGpuCsv(params.msa_preferred_gpus)
    def msa_excluded_gpu_csv = normalizeGpuCsv(params.msa_excluded_gpus)
    def msa_cpu_only_flag = (params.msa_use_gpu == false || params.msa_use_gpu == 'false') ? '--cpu-only' : ''
    def model_aliases = [
        'protenix_esm_20241211_v0.2.1': 'protenix_mini_esm_v0.5.0',
        'protenix_base_20241211_v0.2.1': 'protenix_base_default_v1.0.0'
    ]
    def effective_model = model_aliases.get(model_name, model_name)
    if (!use_msa && !(effective_model.contains('esm') || effective_model.contains('ism'))) {
        effective_model = 'protenix_mini_esm_v0.5.0'
    }

    """
    #!/bin/bash
    set -euo pipefail
    shopt -s nullglob

    mkdir -p original_designs raw_predictions predictions
    cp ${pdbs} original_designs/

    export PROTENIX_ROOT_DIR="${params.code_root}/.protenix_cache"
    export XDG_CACHE_HOME="\$PROTENIX_ROOT_DIR/common"
    export TRITON_CACHE_DIR="\$PROTENIX_ROOT_DIR/triton"
    export MPLCONFIGDIR="\$PROTENIX_ROOT_DIR/matplotlib"
    export PYTHONNOUSERSITE=1
    export PIP_NO_USER=1
    mkdir -p "\$PROTENIX_ROOT_DIR/common" "\$PROTENIX_ROOT_DIR/checkpoint" "\$PROTENIX_ROOT_DIR/triton" "\$PROTENIX_ROOT_DIR/matplotlib"

    if ! command -v protenix &> /dev/null; then
        echo "[BatchProtenixValidation] ERROR: protenix CLI not found in container image" >&2
        exit 127
    fi

    python3 ${params.code_root}/scripts/prep_protenix_batch.py \\
        --pdb_files ${pdbs} \\
        --out_json input.json \\
        --seeds "${seeds}"

    PROTENIX_INPUT_JSON="input.json"
    if [ "${use_msa}" = "true" ]; then
        python3 ${params.code_root}/scripts/prepare_protenix_msa.py \\
            --input_json input.json \\
            --output_json prepared_input.json \\
            --out_dir msa_prepared \\
            --backend "${msa_backend}" \\
            --colabfold-api-host "${params.colabfold_api_host ?: 'https://api.colabfold.com'}" \\
            --db-path "${params.msa_local_db}" \\
            --cache-dir "${params.msa_cache_dir}" \\
            --threads ${params.msa_threads ?: task.cpus} \\
            --preset "${params.msa_preset ?: 'fast'}" \\
            ${msa_cpu_only_flag} \\
            --gpu-mode "${params.msa_gpu_mode ?: 'auto'}" \\
            --gpu-threshold ${params.msa_gpu_threshold ?: 80} \\
            ${msa_preferred_gpu_csv ? '--preferred-gpus "' + msa_preferred_gpu_csv + '"' : ''} \\
            ${msa_excluded_gpu_csv ? '--excluded-gpus "' + msa_excluded_gpu_csv + '"' : ''} \\
            --gpu-server-mode "${params.msa_gpu_server_mode ?: 'persistent'}" \\
            --gpu-server-wait-timeout ${params.msa_gpu_server_wait_timeout ?: 120} \\
            --gpu-server-db-load-mode ${params.msa_gpu_server_db_load_mode ?: 0} \\
            --gpu-server-startup-wait ${params.msa_gpu_server_startup_wait ?: 1.0} \\
            2>&1 | tee protenix_msa_prep.log
        PROTENIX_INPUT_JSON="prepared_input.json"
    fi

    protenix pred \\
        --input "\$PROTENIX_INPUT_JSON" \\
        --out_dir raw_predictions/ \\
        --model_name ${effective_model} \\
        --seeds "${seeds}" \\
        --sample ${n_sample} \\
        --step ${n_step} \\
        --cycle ${n_cycle} \\
        --use_msa ${use_msa} \\
        --use_template ${use_template} \\
        --enable_cache ${enable_cache} \\
        --enable_fusion ${enable_fusion} \\
        2>&1 | tee protenix_batch.log

    raw_cif_count=\$(find raw_predictions -type f -name '*.cif' | wc -l)
    raw_json_count=\$(find raw_predictions -type f -name '*_summary_confidence_sample_*.json' | wc -l)
    if [ "\$raw_cif_count" -eq 0 ] || [ "\$raw_json_count" -eq 0 ]; then
        echo "[BatchProtenixValidation] ERROR: Protenix produced no CIF/summary JSON outputs" >&2
        exit 1
    fi

    python3 ${params.code_root}/scripts/align_protenix.py \\
        --design_dir ./original_designs \\
        --protenix_dir ./raw_predictions \\
        --output_dir predictions \\
        --design_type binder \\
        --binder_chains "${params.antibody_chains ?: 'H,L'}" \\
        --target_chains "${params.antigen_chains ?: 'T'}" \\
        --ncpus ${task.cpus} \\
        2>&1 | tee alignment_protenix.log

    aligned_pdb_count=\$(find predictions -maxdepth 1 -name '*.pdb' | wc -l)
    aligned_json_count=\$(find predictions -maxdepth 1 -name '*.json' | wc -l)
    if [ "\$aligned_pdb_count" -eq 0 ] || [ "\$aligned_json_count" -eq 0 ]; then
        echo "[BatchProtenixValidation] ERROR: alignment produced no usable PDB/JSON outputs" >&2
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
