nextflow.enable.dsl = 2
process EvaluateLaProteina {
    label 'LaProteina'
    label 'gpu'
    stageInMode 'copy'
    publishDir "${params.out_dir}/collected", mode: 'copy'
    input:
    path closure, stageAs: 'la_evaluation_closure'
    output:
    path 'laproteina_evaluation', emit: native_outputs
    script:
    def quote = { value -> "'" + value.toString().replace("'", "'\"'\"'") + "'" }
    def destination = quote("${params.out_dir}/collected/laproteina_evaluation")
    // Reuse the existing LaProteina container/cache/GPU owner; no nested runtime.
    """
    set -uo pipefail
    native_status=0
    DATA_PATH=${quote("${params.weights_root}/laproteina")} python3 ${quote("${params.code_root}/scripts/run_laproteina_inference.py")} \\
      --evaluate --request la_evaluation_closure/request.json \\
      --input-dir la_evaluation_closure --output-dir laproteina_evaluation || native_status=\$?
    if [ "\$native_status" -ne 0 ]; then
      mkdir -p ${destination} || true
      [ ! -d laproteina_evaluation ] || cp -a laproteina_evaluation/. ${destination}/ || true
    fi
    exit "\$native_status"
    """
}
