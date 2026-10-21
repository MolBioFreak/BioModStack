nextflow.enable.dsl = 2

process RunRFD4Proteina {
    tag 'rfd4-proteina'
    label 'RFD4Proteina'
    stageInMode 'copy'
    publishDir "${params.out_dir}/collected", mode: 'copy'

    input:
    path closure, stageAs: 'rfd4_closure'

    output:
    path 'rfd4_proteina', emit: native_outputs

    script:
    def image = System.getenv('BMS_SELECTED_IMAGE_RFD4_PROTEINA_SIF') ?: params.rfd4_proteina_container_path ?: "${params.container_dir}/rfd4_proteina.sif"
    def bundle = params.rfd4_proteina_bundle
    """
    set -euo pipefail
    mkdir -p .tmp .cache/matplotlib
    apptainer exec --no-home --nv \
      --env TMPDIR="\$PWD/.tmp" --env MPLCONFIGDIR="\$PWD/.cache/matplotlib" \
      --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${params.gpu_id} \
      --env PYTHONPATH=/bms/scripts \
      --bind "\$PWD:\$PWD" \
      --bind '${params.code_root}/scripts:/bms/scripts:ro' \
      --bind '${params.weights_root}:${params.weights_root}:ro' \
      '${image}' python -m bms_rfd4.run \
      --request rfd4_closure/request.json --bindings rfd4_closure/bindings.json \
      --bundle '${bundle}' --output rfd4_proteina
    """
}
