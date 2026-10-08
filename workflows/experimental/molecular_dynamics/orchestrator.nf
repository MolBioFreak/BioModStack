nextflow.enable.dsl=2

include { MD_PREPARE_CONFIG } from '../../../modules/experimental/molecular_dynamics/prepare'
include { MD_ANALYZE_REPLICA } from '../../../modules/experimental/molecular_dynamics/analyze'

def sha256File(path) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    path.toFile().withInputStream { stream ->
        byte[] buffer = new byte[1024 * 1024]
        int read
        while ((read = stream.read(buffer)) > 0) digest.update(buffer, 0, read)
    }
    digest.digest().encodeHex().toString()
}

params.md_job_config = null
params.md_input_root = null
params.api_url = System.getenv('API_BASE_URL') ?: 'http://127.0.0.1:8000'
params.job_id = null
params.job_name = 'Molecular Dynamics'
params.md_child_poll_seconds = 5
params.md_analysis_enabled = (System.getenv('BMS_MD_ANALYSIS_ENABLED') ?: '0') == '1'

process MD_SPAWN_REPLICAS {
    tag "md-spawn:${parent_job_id}"
    label 'MolecularDynamicsCoordinator'
    publishDir "${params.out_dir}/orchestration", mode: 'copy', overwrite: true

    input:
    path normalized_config
    path metadata
    val parent_job_id
    val parent_name
    val api_url

    output:
    path 'spawn_md_replicas.json', emit: spawn_result

    script:
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.spawn_replicas \
      --parent-job-id "${parent_job_id}" \
      --parent-name "${parent_name}" \
      --normalized-config ${normalized_config} \
      --metadata ${metadata} \
      --api-url "${api_url}" \
      --output spawn_md_replicas.json
    """
}

process MD_WAIT_FOR_REPLICAS {
    tag "md-wait:${parent_job_id}"
    label 'MolecularDynamicsCoordinator'
    publishDir "${params.out_dir}/orchestration", mode: 'copy', overwrite: true

    input:
    path spawn_result
    val parent_job_id
    val parent_name
    val api_url
    val poll_seconds

    output:
    path 'child_outputs.json', emit: child_status

    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \
      --parent_job_id "${parent_job_id}" \
      --stage md_replica \
      --poll_interval ${poll_seconds} \
      --batch_name "${parent_name}" \
      --api_url "${api_url}" \
      --output child_outputs.json
    """
}

process MD_COLLECT_REPLICAS {
    tag "md-collect:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCoordinator'
    publishDir "${params.out_dir}", mode: 'copy', overwrite: true

    input:
    path child_status

    output:
    path 'manifest.json', emit: manifest
    path 'replicas', emit: replicas

    script:
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.aggregate_children \
      --child-status ${child_status} \
      --output-dir .
    """
}

process MD_ASSERT_REPLICA_OUTCOME {
    tag "md-outcome:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCoordinator'

    input:
    path aggregate_manifest

    output:
    path 'md_outcome_verified.txt'

    script:
    """
    python3 - <<'PY'
import json
from pathlib import Path
manifest = json.loads(Path('${aggregate_manifest}').read_text())
if manifest.get('status') != 'completed':
    raise SystemExit('one or more MD replica children failed or were cancelled')
Path('md_outcome_verified.txt').write_text('completed' + chr(10))
PY
    """
}

workflow {
    if (!params.md_job_config) error "--md_job_config is required"
    if (!params.job_id) error "--job_id is required for durable MD orchestration"

    config_ch = Channel.fromPath(params.md_job_config, checkIfExists: true)
    base_dir = params.md_input_root ?: file(params.md_job_config).parent.toString()
    MD_PREPARE_CONFIG(config_ch, base_dir)

    MD_SPAWN_REPLICAS(
        MD_PREPARE_CONFIG.out.normalized_config,
        MD_PREPARE_CONFIG.out.metadata,
        params.job_id.toString(),
        params.job_name.toString(),
        params.api_url.toString(),
    )
    MD_WAIT_FOR_REPLICAS(
        MD_SPAWN_REPLICAS.out.spawn_result,
        params.job_id.toString(),
        params.job_name.toString(),
        params.api_url.toString(),
        params.md_child_poll_seconds as int,
    )
    MD_COLLECT_REPLICAS(MD_WAIT_FOR_REPLICAS.out.child_status)
    MD_ASSERT_REPLICA_OUTCOME(MD_COLLECT_REPLICAS.out.manifest)
    if (params.md_analysis_enabled) {
        analysis_items = MD_COLLECT_REPLICAS.out.replicas.flatMap { replicas_dir ->
            replicas_dir.toFile().listFiles()
                .findAll { entry -> entry.isDirectory() && entry.name ==~ /replica_[0-9]+/ }
                .sort { left, right -> left.name <=> right.name }
                .collect { entry ->
                    def replicaIndex = (entry.name - 'replica_') as int
                    def manifestPath = entry.toPath().resolve('manifest.json')
                    tuple(replicaIndex, file(entry), sha256File(manifestPath))
                }
        }
        MD_ANALYZE_REPLICA(analysis_items)
    }
}
