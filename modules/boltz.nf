process PrepBoltz {
    label 'pyrosetta_tools'

    input:
    path pdb_files

    output:
    path ("yamls/*.yaml"), emit: yamls

    script:
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    # Generate yaml files containing sequences for Boltz-2 prediction 
    python /scripts/prep_boltz_yaml.py \
        --input "./" \
        --output "yamls"
    """
}

// PrepBoltz WITH MSA Generation
// Generates MSAs for each unique chain sequence using GPU MMseqs2, then creates YAMLs with MSA paths
process PrepBoltzWithMSA {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/msa", mode: 'copy', pattern: "msa/*.a3m"

    input:
    path pdb_files

    output:
    path ("yamls/*.yaml"), emit: yamls
    path ("msa/*.a3m"), emit: msas, optional: true

    script:
    def dbPath = params.msa_local_db
    def cacheDir = params.msa_cache_dir
    def threads = params.msa_threads ?: 32
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta
    
    mkdir -p yamls msa
    
    # Call external prep_boltz_with_msa.py script
    python3 ${params.code_root}/scripts/prep_boltz_with_msa.py \\
        --input "./" \\
        --output "yamls" \\
        --msa_output "msa" \\
        --db_path "${dbPath}" \\
        --cache_dir "${cacheDir}" \\
        --threads ${threads} \\
        --msa_script "${params.code_root}/scripts/run_local_msa.py"
    """
}

process RunBoltz {
    label 'Boltz'
    label 'gpu'
    publishDir "${params.out_dir}/run/boltz", mode: 'copy', pattern: "*.log"
    tag "B${batch_id}"

    input:
    tuple val(batch_id), path(yamls)

    output:
    tuple path("predictions/*.pdb"), path("predictions/*.json"), emit: pdbs_jsons
    path ("predictions/*.npz"), emit: pae_npz, optional: true
    path ("*.log"), emit: logs

    script:
    """
        # We specify tmp directories as some python packages try to write to the user home directory outside the container
        mkdir tmp
        export NUMBA_CACHE_DIR=tmp
        export XDG_CONFIG_HOME=tmp
        export TRITON_CACHE_DIR=tmp
        export HOME=tmp
        
        mkdir yamls
        for file in \$(find *.yaml); do
            cp -L "\$file" ./yamls/
        done

        boltz predict \
            ./yamls/ \
            --output_format pdb \
            ${params.boltz_diffusion_samples ? '--diffusion_samples ' + params.boltz_diffusion_samples : ''} \
            ${params.boltz_max_parallel_samples ? '--max_parallel_samples ' + params.boltz_max_parallel_samples : ''} \
            --recycling_steps ${params.boltz_recycling_steps ?: 3} \
            --sampling_steps ${params.boltz_sampling_steps ?: 50} \
            ${params.boltz_use_potentials ? '--use_potentials' : ''} \
            ${params.boltz_step_scale ? '--step_scale ' + params.boltz_step_scale : ''} \
            ${params.boltz_predict_affinity ? '--sampling_steps_affinity ' + (params.boltz_sampling_steps_affinity ?: 200) + ' --diffusion_samples_affinity ' + (params.boltz_diffusion_samples_affinity ?: 5) : ''} \
            ${params.boltz_affinity_mw_correction ? '--affinity_mw_correction' : ''} \
            --cache /boltzcache \
            ${params.boltz_extra_config ? params.boltz_extra_config : ''} \
            2>&1 | tee boltz_${batch_id}.log
 
        # Move output files out of nested directories and rename to {inputname}_boltzpred.pdb|json
        mkdir -p predictions
        for dir in boltz_results_yamls/predictions/*/; do
            # Extract input name from directory path
            inputname=\$(basename "\$dir")
            # Process PDB file
            if [ -f "\${dir}/\${inputname}_model_0.pdb" ]; then
                mv "\${dir}/\${inputname}_model_0.pdb" "predictions/\${inputname}_boltzpred.pdb"
            fi
            # Process JSON file 
            if [ -f "\${dir}/confidence_\${inputname}_model_0.json" ]; then
                mv "\${dir}/confidence_\${inputname}_model_0.json" "predictions/\${inputname}_boltzpred.json"
            fi
            npz_src="\${dir}/pae_\${inputname}_model_0.npz"
            if [ -f "\$npz_src" ]; then
                cp "\$npz_src" "predictions/\${inputname}_boltzpred.pae.npz"
            fi
            # Copy affinity JSONs (generated when --sampling_steps_affinity is set)
            cp "\${dir}"/affinity_*.json predictions/ 2>/dev/null || :
        done

        """
}
process AlignBoltz {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/align", mode: 'copy', pattern: "alignment_*.log"

    input:
    tuple path(pdb_files), path(json_files)
    path designs
    val design_type

    output:
    tuple path("aligned/*.pdb"), path("aligned/*.json"), emit: pdbs_jsons
    path "alignment_*.log"
    path ("boltz_metadata_*.jsonl"), topic: metadata_ch_fold_seq

    script:

    def num_processes = task.cpus - 1

    """
    export MAMBA_ROOT_PREFIX=/opt/conda/
    
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    # Script to align predictions to RFdiffusion designs and calculate RMSD
    # Also, extracts and renames metadata from json files
    python /scripts/align_boltz.py \
        --design_dir ./ \
        --boltz_dir ./ \
        --output_dir aligned \
        --design_type ${design_type} \
        --ncpus ${num_processes} \
        2>&1 | tee alignment_${task.index}.log
    
    # metadata convert script to combine json files into jsonl
    python /scripts/metadata_converter.py \
        --converter boltz \
        --input_dir aligned \
        --input_ext .json \
        --output_file boltz_metadata_${task.index}.jsonl
    """
}
process FilterBoltz {
    label 'pyrosetta_tools'
    publishDir "${params.out_dir}/run/filter_boltz", mode: 'copy', pattern: '*.log'

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path ("output/*.pdb"), emit: pdbs, optional: true
    path ("filter_boltz_${task.index}.log"), emit: log
    path ("filtered.jsonl"), emit: jsonl, optional: true

    script:
    def paramString = Utils.formatFilterParams(
        params,
        "boltz",
        [
            "max_overall_rmsd",
            "max_binder_rmsd",
            "max_target_rmsd",
            "min_conf_score",
            "min_ptm",
            "min_ptm_interface",
            "min_plddt",
            "min_plddt_interface",
            "max_pde",
            "max_pde_interface",
        ],
    )

    """
    python -u /scripts/filter_boltz.py \\
        --json-directory ./ \\
        ${paramString} \\
        --output-directory output \\
        2>&1 | tee filter_boltz_${task.index}.log
    """
}
