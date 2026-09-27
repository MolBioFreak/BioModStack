// Model-owned leaf: the native CLI owns all campaign stages and adaptive settings.
process RunBindCraft2 {
    label 'gpu'
    container { System.getenv('BMS_SELECTED_IMAGE_BINDCRAFT2_SIF') ?: "${params.container_dir}/bindcraft2.sif" }
    // GPU placement is scheduler-owned. BC2 sees only the selected card(s), not the host fleet.
    // The workstation gpu label appends task.ext.containerOptions after its own
    // scheduler GPU prefix; the later visibility env owns BC2's selected set.
    ext { containerOptions = "--env CUDA_VISIBLE_DEVICES=${params.get('bc2_gpu_ids') ?: params.gpu_id} --env BINDCRAFT_AF2_PARAMS=${params.weights_root}/alphafold/params --env JAX_COMPILATION_CACHE_DIR=/cache/bindcraft2/compile/${params.get('bc2_gpu_ids') ?: params.gpu_id} --bind ${params.cache_root}/bindcraft2/compile:/cache/bindcraft2/compile --bind ${params.weights_root}/alphafold/params:${params.weights_root}/alphafold/params:ro" }

    input:
    path compilation
    val campaign_dir

    output:
    path 'bc2_complete.json', emit: completion

    script:
    """
    python3 '${params.code_root}/scripts/run_bindcraft2_campaign.py' '${compilation}' '${campaign_dir}' --native-source /opt/bindcraft --execute
    python3 -c 'import json; from pathlib import Path; Path("bc2_complete.json").write_text(json.dumps({"campaign_root": "${campaign_dir}/campaign", "compilation": "${campaign_dir}/compilation.json"}) + "\\n")'
    """
}

// Postprocessing uses existing structures/tables, not AF2 or a reserved GPU.
process PostprocessBindCraft2 {
    label 'cpu'
    container { System.getenv('BMS_SELECTED_IMAGE_BINDCRAFT2_SIF') ?: "${params.container_dir}/bindcraft2.sif" }
    containerOptions '--env JAX_PLATFORMS=cpu'

    input:
    path compilation
    val campaign_dir

    output:
    path 'bc2_complete.json', emit: completion

    script:
    """
    python3 '${params.code_root}/scripts/run_bindcraft2_campaign.py' '${compilation}' '${campaign_dir}' --native-source /opt/bindcraft --execute
    python3 -c 'import json; from pathlib import Path; Path("bc2_complete.json").write_text(json.dumps({"campaign_root": "${campaign_dir}/campaign", "compilation": "${campaign_dir}/compilation.json"}) + "\\n")'
    """
}
