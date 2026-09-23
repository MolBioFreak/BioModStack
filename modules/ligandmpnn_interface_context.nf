nextflow.enable.dsl = 2

// Selected, read-only candidate diagnostic. No verdict or candidate mutation.
process RunLigandMPNNInterfaceContext {
    tag "ligandmpnn-context:${invocation_id}"
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0
    publishDir "${params.out_dir}/ligandmpnn_interface_context/${invocation_id}", mode: 'copy', pattern: 'context_result'

    input:
    tuple val(invocation_id), path(request_snapshot), path(source_snapshot)

    output:
    tuple val(invocation_id), path('context_result'), emit: evidence

    script:
    def image = "${params.container_dir}/foundry.sif"
    def stageScript = "${params.code_root}/scripts/stage_ligandmpnn_interface_context.py"
    def nativeScript = "${params.code_root}/scripts/run_ligandmpnn_interface_context.py"
    def apiRoot = "${params.code_root}/platform/api"
    """
    set -euo pipefail
    python3 '${stageScript}' '${request_snapshot}' '${source_snapshot}' effective_request.json
    apptainer exec --no-home \\
      --bind "\$PWD:\$PWD" \\
      --bind '${nativeScript}:/probe/ligandmpnn_context.py' \\
      '${image}' python /probe/ligandmpnn_context.py \\
      "\$PWD/effective_request.json" "\$PWD/context_result"
    PYTHONPATH='${apiRoot}' python3 - <<'PY'
import json
from pathlib import Path
from services.ligandmpnn_interface_context import read_context_result
request = json.loads(Path('effective_request.json').read_text())
read_context_result(Path('context_result/result.json'),
                    candidate_id=request['candidate_id'], round_id=request['round_id'],
                    source_sha256=request['source_sha256'])
PY
    """
}
