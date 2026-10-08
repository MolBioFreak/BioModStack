nextflow.enable.dsl = 2

process RunProtonPottsMPNNDesign {
    tag 'protonpottsmpnn-design'
    label 'cpu'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0
    publishDir "${params.out_dir}", mode: 'copy', pattern: 'protonpottsmpnn_design'

    input:
    path request, stageAs: 'prepared_request.json'
    path source, stageAs: 'source/*'

    output:
    path 'protonpottsmpnn_design', emit: native_outputs

    script:
    def image = System.getenv('BMS_SELECTED_IMAGE_PROTONPOTTSMPNN_SIF') ?: params.protonpottsmpnn_container_path ?: System.getenv('BMS_PROTONPOTTSMPNN_CONTAINER_PATH') ?: "${params.container_dir}/protonpottsmpnn.sif"
    def device = params.protonpottsmpnn_device ?: 'cpu'
    def cuda = device.toString().startsWith('cuda')
    def acceleration = cuda ? "--nv --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${params.gpu_id}" : '--env CUDA_VISIBLE_DEVICES='
    // Physical IDs belong to the scheduler; the native engine sees logical 0.
    def nativeDevice = cuda ? 'cuda:0' : device
    def runner = "${params.code_root}/scripts/run_protonpottsmpnn_design.py"
    def library = "${params.code_root}/scripts/lib"
    """
    set -euo pipefail
    apptainer exec --no-home ${acceleration} \
      --bind "\$PWD:\$PWD" \
      --bind '${runner}:/probe/run_protonpottsmpnn_design.py' \
      --bind '${library}:/probe/lib:ro' \
      '${image}' python /probe/run_protonpottsmpnn_design.py \
      --request ${request} --input ${source} --out . --n-jobs ${task.cpus} --device '${nativeDevice}'
    """
}
