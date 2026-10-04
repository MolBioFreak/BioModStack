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
    def image = "${params.container_dir}/protonpottsmpnn.sif"
    def runner = "${params.code_root}/scripts/run_protonpottsmpnn_design.py"
    def library = "${params.code_root}/scripts/lib"
    """
    set -euo pipefail
    apptainer exec --no-home --env CUDA_VISIBLE_DEVICES= \
      --bind "\$PWD:\$PWD" \
      --bind '${runner}:/probe/run_protonpottsmpnn_design.py' \
      --bind '${library}:/probe/lib:ro' \
      '${image}' python /probe/run_protonpottsmpnn_design.py \
      --request ${request} --input ${source} --out protonpottsmpnn_design --n-jobs ${task.cpus}
    """
}
