nextflow.enable.dsl = 2

process PrepCalibyRequest {
    label 'process_low'

    publishDir "${params.out_dir}/inputs/caliby", mode: 'copy', pattern: 'caliby_request.json'
    publishDir "${params.out_dir}/inputs/caliby", mode: 'copy', pattern: 'caliby_inputs/*', saveAs: { filename -> filename.replace('caliby_inputs/', '') }

    output:
    path 'caliby_request.json', emit: request
    path 'caliby_inputs', emit: input_dir

    script:
    """
    mkdir -p caliby_inputs

    python3 ${params.code_root}/scripts/prep_caliby_request.py \\
        --job-id "${params.job_id ?: 'unknown'}" \\
        --job-name "${params.name ?: params.batch_name ?: 'caliby_experimental'}" \\
        --task "${params.caliby_task ?: 'sequence_design'}" \\
        --input-pdb-dir "${params.caliby_input_pdb_dir ?: ''}" \\
        --conformer-dir "${params.caliby_conformer_dir ?: ''}" \\
        --pdb-name-list "${params.caliby_pdb_name_list ?: ''}" \\
        --pos-constraint-csv "${params.caliby_pos_constraint_csv ?: ''}" \\
        --model-name "${params.caliby_model_name ?: 'soluble_caliby_v1'}" \\
        --packer-model-name "${params.caliby_packer_model_name ?: 'caliby_packer_010'}" \\
        --num-seqs-per-pdb ${params.caliby_num_seqs_per_pdb ?: 4} \\
        --batch-size ${params.caliby_batch_size ?: 4} \\
        --num-workers ${params.caliby_num_workers ?: 8} \\
        --clean-num-workers ${params.caliby_clean_num_workers ?: 2} \\
        --temperature ${params.caliby_temperature ?: 0.1} \\
        --omit-aas "${params.caliby_omit_aas ?: 'C'}" \\
        --run-self-consistency-eval "${params.caliby_run_self_consistency_eval ?: false}" \\
        --self-consistency-num-models ${params.caliby_self_consistency_num_models ?: 5} \\
        --self-consistency-num-recycles ${params.caliby_self_consistency_num_recycles ?: 3} \\
        --self-consistency-use-multimer "${params.caliby_self_consistency_use_multimer ?: false}" \\
        --sampling-overrides-json '${params.caliby_sampling_overrides_json ?: ''}' \\
        --output caliby_request.json \\
        --input-dir caliby_inputs
    """
}

process RunCalibyExperimental {
    label 'Caliby'
    label 'gpu'

    publishDir "${params.out_dir}/collected/caliby_raw", mode: 'copy', pattern: 'raw/pdbs/*.pdb', saveAs: { filename -> filename.replace('raw/pdbs/', '') }
    publishDir "${params.out_dir}/collected/caliby_raw", mode: 'copy', pattern: 'raw/metadata/*.json', saveAs: { filename -> filename.replace('raw/metadata/', '') }
    publishDir "${params.out_dir}/metadata", mode: 'copy', pattern: 'design_manifest.json'
    publishDir "${params.out_dir}/run/caliby", mode: 'copy', pattern: '*.log'

    input:
    path request_json
    path input_dir

    output:
    path 'raw/pdbs/*.pdb', emit: pdbs
    path 'raw/metadata/*.json', emit: jsons
    path 'design_manifest.json', emit: manifest
    path '*.log'

    script:
    """
    python3 ${params.code_root}/scripts/run_caliby_experimental.py \\
        --request ${request_json} \\
        --input-dir ${input_dir} \\
        --output-dir . \\
        2>&1 | tee caliby.log
    """
}

process FinalizeCalibyExperimentalOutputs {
    label 'process_low'

    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: 'published/*.pdb', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: 'published/confidence_*.json', saveAs: { filename -> filename.replace('published/', '') }
    publishDir "${params.out_dir}/metadata", mode: 'copy', pattern: 'published/design_manifest.json', saveAs: { filename -> filename.replace('published/', '') }

    input:
    path pdb_files
    path metadata_jsons
    path design_manifest

    output:
    path 'published/*.pdb', emit: pdbs
    path 'published/confidence_*.json', emit: jsons
    path 'published/design_manifest.json', emit: manifest

    script:
    """
    mkdir -p published
    cp ${pdb_files} published/
    cp ${design_manifest} published/design_manifest.json
    for meta in ${metadata_jsons}; do
        base=\$(basename "\$meta")
        target="\${base#generator_}"
        cp "\$meta" "published/confidence_\${target}"
    done
    """
}
