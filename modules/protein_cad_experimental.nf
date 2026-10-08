nextflow.enable.dsl = 2

process PrepProteinCadRequest {
    label 'process_low'

    publishDir "${params.out_dir}/inputs/protein_cad", mode: 'copy', pattern: 'protein_cad_request.json'
    publishDir "${params.out_dir}/inputs/protein_cad", mode: 'copy', pattern: 'protein_cad_inputs/*', saveAs: { filename -> filename.replace('protein_cad_inputs/', '') }

    output:
    path 'protein_cad_request.json', emit: request
    path 'protein_cad_inputs', emit: input_dir

    script:
    def checkpointDir = params.pcad_laproteina_checkpoint_dir ?: params.laproteina_checkpoint_dir ?: "${params.weights_root}/laproteina"
    def dataPath = params.pcad_laproteina_data_path ?: params.laproteina_data_path ?: "${params.weights_root}/laproteina"
    def discoCheckpoint = params.pcad_disco_checkpoint_path ?: params.disco_checkpoint_path ?: "${params.weights_root}/disco/DISCO.pt"
    """
    mkdir -p protein_cad_inputs

    python3 ${params.code_root}/scripts/prep_protein_cad_request.py \\
        --job-id "${params.job_id ?: 'unknown'}" \\
        --job-name "${params.name ?: params.batch_name ?: 'protein_cad_experimental'}" \\
        --backend "${params.pcad_backend ?: 'disco'}" \\
        --task "${params.pcad_task ?: 'unconditional'}" \\
        --num-designs ${params.pcad_num_designs ?: 8} \\
        --target-lengths "${params.pcad_target_lengths ?: '100,200'}" \\
        --laproteina-preset "${params.pcad_laproteina_preset ?: params.laproteina_preset ?: 'ucond_tri'}" \\
        --laproteina-samples-per-length ${params.pcad_laproteina_samples_per_length ?: params.laproteina_samples_per_length ?: 8} \\
        --laproteina-num-steps ${params.pcad_laproteina_num_steps ?: params.laproteina_num_steps ?: 400} \\
        --laproteina-motif-task-name "${params.pcad_laproteina_motif_task_name ?: params.laproteina_motif_task_name ?: ''}" \\
        --laproteina-motif-pdb "${params.pcad_laproteina_motif_pdb ?: params.laproteina_motif_pdb ?: ''}" \\
        --laproteina-contig-string "${params.pcad_laproteina_contig_string ?: params.laproteina_contig_string ?: ''}" \\
        --laproteina-segment-order "${params.pcad_laproteina_segment_order ?: params.laproteina_segment_order ?: ''}" \\
        --laproteina-atom-selection-mode "${params.pcad_laproteina_atom_selection_mode ?: params.laproteina_atom_selection_mode ?: 'all_atom'}" \\
        --laproteina-motif-min-length "${params.pcad_laproteina_motif_min_length ?: params.laproteina_motif_min_length ?: ''}" \\
        --laproteina-motif-max-length "${params.pcad_laproteina_motif_max_length ?: params.laproteina_motif_max_length ?: ''}" \\
        --laproteina-checkpoint-dir "${checkpointDir}" \\
        --laproteina-data-path "${dataPath}" \\
        --disco-experiment "${params.pcad_disco_experiment ?: params.disco_experiment ?: 'designable'}" \\
        --disco-effort "${params.pcad_disco_effort ?: params.disco_effort ?: 'fast'}" \\
        --disco-num-inference-seeds ${params.pcad_disco_num_inference_seeds ?: params.disco_num_inference_seeds ?: params.pcad_num_designs ?: 8} \\
        --disco-seeds "${params.pcad_disco_seeds ?: params.disco_seeds ?: ''}" \\
        --disco-input-json-path "${params.pcad_disco_input_json_path ?: params.disco_input_json_path ?: ''}" \\
        --disco-ligand-sdf "${params.pcad_disco_ligand_sdf ?: params.disco_ligand_sdf ?: ''}" \\
        --disco-ligand-name "${params.pcad_disco_ligand_name ?: params.disco_ligand_name ?: ''}" \\
        --disco-na-sequence "${params.pcad_disco_na_sequence ?: params.disco_na_sequence ?: ''}" \\
        --disco-checkpoint-path "${discoCheckpoint}" \\
        --disco-use-deepspeed-evo-attention "${params.pcad_disco_use_deepspeed_evo_attention ?: params.disco_use_deepspeed_evo_attention ?: false}" \\
        --disco-cutlass-path "${params.pcad_disco_cutlass_path ?: params.disco_cutlass_path ?: ''}" \\
        --output protein_cad_request.json \\
        --input-dir protein_cad_inputs
    """
}

process RunLaProteina {
    label 'LaProteina'
    label 'gpu'

    publishDir "${params.out_dir}/collected/protein_cad_raw", mode: 'copy', pattern: 'raw/pdbs/*.pdb', saveAs: { filename -> filename.replace('raw/pdbs/', '') }
    publishDir "${params.out_dir}/collected/protein_cad_raw", mode: 'copy', pattern: 'raw/metadata/*.json', saveAs: { filename -> filename.replace('raw/metadata/', '') }
    publishDir "${params.out_dir}/metadata", mode: 'copy', pattern: 'design_manifest.json'
    publishDir "${params.out_dir}/run/protein_cad", mode: 'copy', pattern: '*.log'

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
    python3 ${params.code_root}/scripts/run_laproteina_inference.py \\
        --request ${request_json} \\
        --input-dir ${input_dir} \\
        --output-dir .
    """
}

process RunDISCO {
    label 'DISCO'
    label 'gpu'

    publishDir "${params.out_dir}/collected/protein_cad_raw", mode: 'copy', pattern: 'raw/pdbs/*.pdb', saveAs: { filename -> filename.replace('raw/pdbs/', '') }
    publishDir "${params.out_dir}/collected/protein_cad_raw", mode: 'copy', pattern: 'raw/metadata/*.json', saveAs: { filename -> filename.replace('raw/metadata/', '') }
    publishDir "${params.out_dir}/metadata", mode: 'copy', pattern: 'design_manifest.json'
    publishDir "${params.out_dir}/run/protein_cad", mode: 'copy', pattern: '*.log'

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
    python3 ${params.code_root}/scripts/run_disco_inference.py \\
        --request ${request_json} \\
        --input-dir ${input_dir} \\
        --output-dir .
    """
}

process FinalizeProteinCadOutputs {
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
