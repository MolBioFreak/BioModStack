nextflow.enable.dsl = 2

// Ordinary redesign only; interface_context keeps its separate diagnostic leaf.
process RunLigandMPNNDesign {
    tag 'ligandmpnn-design'
    label 'gpu'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0
    publishDir "${params.out_dir}", mode: 'copy', pattern: 'ligandmpnn_design'

    input:
    path request, stageAs: 'prepared_request.json'
    path source, stageAs: 'source/*'

    output:
    path 'ligandmpnn_design', emit: native_outputs

    script:
    def image = "${params.container_dir}/foundry.sif"
    def runner = "${params.code_root}/scripts/run_ligandmpnn_design.py"
    """
    set -euo pipefail
    apptainer exec --nv --no-home --env CUDA_VISIBLE_DEVICES=${params.gpu_id} \
      --bind "\$PWD:\$PWD" \
      --bind '${runner}:/probe/run_ligandmpnn_design.py' \
      '${image}' python /probe/run_ligandmpnn_design.py \
      --request '${request}' --input '${source}' --output ligandmpnn_design
    """
}
