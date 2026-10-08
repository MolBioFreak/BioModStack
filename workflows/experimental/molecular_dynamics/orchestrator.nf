nextflow.enable.dsl=2

include { MD_PREPARE_CONFIG } from '../../../modules/experimental/molecular_dynamics/prepare'

params.md_job_config = null
params.md_input_root = null
params.api_url = System.getenv('API_BASE_URL') ?: 'http://127.0.0.1:8000'
params.job_id = null
params.job_name = 'Molecular Dynamics'
params.md_child_poll_seconds = 5
params.md_analysis_enabled = (System.getenv('BMS_MD_ANALYSIS_ENABLED') ?: '0') == '1'
params.md_analysis_sif_sha256 = System.getenv('BMS_MD_ANALYSIS_SIF_SHA256') ?: '3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68'
params.md_analysis_implementation_sha256 = System.getenv('BMS_MD_ANALYSIS_IMPLEMENTATION_SHA256')

process MD_SPAWN_REPLICAS {
    tag "md-spawn:${parent_job_id}"
    label 'MolecularDynamicsCoordinator'

    input:
    path normalized_config
    path metadata
    path preparation_bundle
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
      --preparation-bundle ${preparation_bundle} \
      --api-url "${api_url}" \
      --output spawn_md_replicas.json
    """
}

process MD_WAIT_FOR_REPLICAS {
    tag "md-wait:${parent_job_id}"
    label 'MolecularDynamicsCoordinator'

    input:
    path spawn_result
    val parent_job_id
    val parent_name
    val api_url
    val poll_seconds

    output:
    path 'replica_child_outputs.json', emit: child_status

    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \
      --parent_job_id "${parent_job_id}" \
      --stage md_replica \
      --poll_interval ${poll_seconds} \
      --batch_name "${parent_name}" \
      --api_url "${api_url}" \
      --output replica_child_outputs.json
    """
}

process MD_COLLECT_REPLICAS {
    tag "md-collect:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCoordinator'

    input:
    path child_status

    output:
    path 'replica_collection.done', emit: collection_marker

    script:
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.aggregate_children \
      --child-status ${child_status} \
      --output-dir "${params.out_dir}"
    printf 'completed\n' > replica_collection.done
    """
}

process MD_ASSERT_REPLICA_OUTCOME {
    tag "md-outcome:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCoordinator'

    input:
    path collection_marker
    val aggregate_manifest

    output:
    path 'md_replica_outcome_verified.txt', emit: verified

    script:
    """
    python3 - <<'PY'
import json
from pathlib import Path
manifest = json.loads(Path('${aggregate_manifest}').read_text())
if manifest.get('status') != 'completed':
    raise SystemExit('one or more MD replica children failed or were cancelled')
if not manifest.get('replicas'):
    raise SystemExit('no immutable MD replica outputs were collected')
Path('md_replica_outcome_verified.txt').write_text('completed' + chr(10))
PY
    """
}

process MD_SPAWN_ANALYSIS {
    tag "md-analysis-spawn:${parent_job_id}"
    label 'MolecularDynamicsCoordinator'

    input:
    path replica_outcome
    val aggregate_manifest
    val parent_job_id
    val parent_name
    val api_url
    val runtime_sha256

    output:
    path 'spawn_md_analysis.json', emit: spawn_result

    script:
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.spawn_analysis \
      --parent-job-id "${parent_job_id}" \
      --parent-name "${parent_name}" \
      --aggregate-manifest "${aggregate_manifest}" \
      --api-url "${api_url}" \
      --work-item-dir "${params.out_dir}/orchestration/analysis_work_items" \
      --runtime-sha256 "${runtime_sha256}" \
      --output spawn_md_analysis.json
    """
}

process MD_WAIT_FOR_ANALYSIS {
    tag "md-analysis-wait:${parent_job_id}"
    label 'MolecularDynamicsCoordinator'

    input:
    path spawn_result
    val parent_job_id
    val parent_name
    val api_url
    val poll_seconds

    output:
    path 'analysis_child_outputs.json', emit: child_status

    script:
    """
    python3 ${params.code_root}/scripts/wait_for_children.py \
      --parent_job_id "${parent_job_id}" \
      --stage md_analysis \
      --poll_interval ${poll_seconds} \
      --batch_name "${parent_name}" \
      --api_url "${api_url}" \
      --output analysis_child_outputs.json
    """
}

process MD_COLLECT_ANALYSIS {
    tag "md-analysis-collect:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCoordinator'

    input:
    path child_status
    val aggregate_manifest

    output:
    path 'analysis_collection.done', emit: collection_marker

    script:
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.collect_analysis \
      --child-status ${child_status} \
      --aggregate-manifest "${aggregate_manifest}" \
      --output-dir "${params.out_dir}"
    printf 'completed\n' > analysis_collection.done
    """
}

process MD_ASSERT_ANALYSIS_OUTCOME {
    tag "md-analysis-outcome:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCoordinator'

    input:
    path collection_marker
    val analysis_manifest

    output:
    path 'md_analysis_outcome_verified.txt', emit: verified

    script:
    """
    python3 - <<'PY'
import json
from pathlib import Path
manifest = json.loads(Path('${analysis_manifest}').read_text())
if manifest.get('status') != 'completed':
    raise SystemExit('one or more durable MD analysis children failed, were cancelled, or remain uncollected')
if manifest.get('completed_analysis_children') != manifest.get('required_analysis_children'):
    raise SystemExit('required durable MD analysis results are not all collected')
Path('md_analysis_outcome_verified.txt').write_text('completed' + chr(10))
PY
    """
}

process MD_COMPLETION_BARRIER {
    tag "md-completion:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCoordinator'
    publishDir "${params.out_dir}", mode: 'copy', overwrite: false

    input:
    path replica_outcome
    path analysis_outcome
    val aggregate_manifest
    val analysis_manifest

    output:
    path 'md_completion_barrier.json', emit: completion

    script:
    """
    python3 - <<'PY'
import hashlib
import json
from pathlib import Path
replica_path = Path('${aggregate_manifest}')
analysis_path = Path('${analysis_manifest}')
replica = json.loads(replica_path.read_text())
analysis = json.loads(analysis_path.read_text())
replica_sha = hashlib.sha256(replica_path.read_bytes()).hexdigest()
if replica.get('status') != 'completed' or analysis.get('status') != 'completed':
    raise SystemExit('MD completion barrier reached before durable collection completed')
if analysis.get('aggregate_manifest_sha256') != replica_sha:
    raise SystemExit('MD analysis was not collected against the immutable replica aggregate')
if len(replica.get('replicas') or []) != analysis.get('completed_analysis_children'):
    raise SystemExit('MD completion barrier requires one collected analysis per replica')
Path('md_completion_barrier.json').write_text(json.dumps({
    'schema': 'bms.md.completion-barrier.v1',
    'status': 'completed',
    'job_id': replica.get('job_id'),
    'aggregate_manifest_sha256': replica_sha,
    'analysis_manifest_sha256': hashlib.sha256(analysis_path.read_bytes()).hexdigest(),
}, sort_keys=True) + chr(10))
PY
    """
}

workflow {
    if (!params.md_job_config) error "--md_job_config is required"
    if (!params.job_id) error "--job_id is required for durable MD orchestration"
    if (!params.md_analysis_enabled) error "durable MD analysis children are disabled"

    aggregate_manifest = "${params.out_dir}/manifest.json"
    analysis_manifest = "${params.out_dir}/analysis/manifest.json"
    config_ch = Channel.fromPath(params.md_job_config, checkIfExists: true)
    base_dir = params.md_input_root ?: file(params.md_job_config).parent.toString()
    MD_PREPARE_CONFIG(config_ch, base_dir)

    MD_SPAWN_REPLICAS(
        MD_PREPARE_CONFIG.out.normalized_config,
        MD_PREPARE_CONFIG.out.metadata,
        MD_PREPARE_CONFIG.out.preparation_bundle,
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
    MD_ASSERT_REPLICA_OUTCOME(MD_COLLECT_REPLICAS.out.collection_marker, aggregate_manifest)
    MD_SPAWN_ANALYSIS(
        MD_ASSERT_REPLICA_OUTCOME.out.verified,
        aggregate_manifest,
        params.job_id.toString(),
        params.job_name.toString(),
        params.api_url.toString(),
        params.md_analysis_sif_sha256.toString(),
    )
    MD_WAIT_FOR_ANALYSIS(
        MD_SPAWN_ANALYSIS.out.spawn_result,
        params.job_id.toString(),
        params.job_name.toString(),
        params.api_url.toString(),
        params.md_child_poll_seconds as int,
    )
    MD_COLLECT_ANALYSIS(MD_WAIT_FOR_ANALYSIS.out.child_status, aggregate_manifest)
    MD_ASSERT_ANALYSIS_OUTCOME(MD_COLLECT_ANALYSIS.out.collection_marker, analysis_manifest)
    MD_COMPLETION_BARRIER(
        MD_ASSERT_REPLICA_OUTCOME.out.verified,
        MD_ASSERT_ANALYSIS_OUTCOME.out.verified,
        aggregate_manifest,
        analysis_manifest,
    )
}
