nextflow.enable.dsl = 2

import groovy.json.JsonOutput

process PrepareAntibodyFrustraMPNNCandidate {
    tag "frustrampnn-antibody:${candidate_meta.candidate_id}"
    label 'CPU'
    stageInMode 'copy'
    publishDir { "${params.out_dir}/${new File(candidate_meta.producer_candidate_key.toString()).parent}" },
        mode: 'copy', pattern: 'canonical_source.pdb', saveAs: { new File(candidate_meta.producer_candidate_key.toString()).name }

    input:
    tuple val(candidate_meta), path(terminal_structure)

    output:
    tuple path('workflow_component_request_v1.json'), path('canonical_source.pdb'), emit: prepared

    script:
    def requestMetadata = candidate_meta.subMap([
        'parent_job_id', 'parent_workflow_id', 'producer_stage', 'producer_candidate_key',
        'requiredness', 'checkpoint_id', 'producer_method', 'producer_sample',
        'producer_rank', 'producer_output_key', 'producer_identity_sha256',
        'producer_artifact_sha256', 'source_format', 'candidate_id'
    ])
    def metadataBase64 = JsonOutput.toJson(requestMetadata).getBytes('UTF-8').encodeBase64().toString()
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/prepare_frustrampnn_candidate.py' \
      --source '${terminal_structure}' \
      --output-pdb canonical_source.pdb \
      --request workflow_component_request_v1.json \
      --metadata-base64 '${metadataBase64}'
    """
}

process PublishAntibodyFrustraMPNNCandidate {
    tag "frustrampnn-antibody-publish:${result_meta.candidate_id}"
    label 'CPU'
    stageInMode 'copy'

    input:
    tuple val(result_meta), path(candidate_bundle), path(result_manifest)

    output:
    tuple val(result_meta), path(result_manifest), path('published_*.json'), emit: published

    script:
    def candidateId = result_meta.candidate_id.toString()
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/publish_frustrampnn_bundle.py' \
      --source-bundle '${candidate_bundle}' \
      --allowed-root '${params.out_dir}' \
      --destination '${params.out_dir}/frustrampnn/results/${candidateId}' \
      --marker 'published_${candidateId}.json'
    """
}

process AggregateAndReportAntibodyFrustraMPNN {
    label 'CPU'
    publishDir "${params.out_dir}/frustrampnn", mode: 'copy', pattern: 'antibody_frustrampnn_terminal_manifest.json'

    input:
    path published_markers

    output:
    path 'antibody_frustrampnn_terminal_manifest.json', emit: terminal_manifest
    path 'frustrampnn_complete.reported', emit: reported

    script:
    """
    set -euo pipefail
    '${params.api_python}' - <<'PY'
import json
from pathlib import Path

launch_root = Path('${workflow.launchDir}')
out_dir = Path('${params.out_dir}')
job_root = (out_dir if out_dir.is_absolute() else launch_root / out_dir).absolute()
if not job_root.is_dir() or job_root.is_symlink():
    raise SystemExit('antibody_denovo:frustrampnn_invalid_job_root')

def resolve_job_output(value):
    raw_value = str(value)
    parts = raw_value.split('/')
    if (
        not raw_value
        or raw_value.startswith('/')
        or '\\\\' in raw_value
        or any(part in {'', '.', '..'} for part in parts)
    ):
        raise SystemExit('antibody_denovo:frustrampnn_unsafe_publication_path')
    relative = Path(*parts)
    candidate = job_root / relative
    candidate.relative_to(job_root)
    cursor = job_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise SystemExit('antibody_denovo:frustrampnn_symlink_publication_path')
    if not candidate.is_file():
        raise SystemExit('antibody_denovo:frustrampnn_missing_publication_path')
    return candidate

markers = sorted(Path('.').glob('published_*.json'))
if not markers:
    raise SystemExit('antibody_denovo:frustrampnn_missing_publication')
candidates = []
outputs = []
seen = set()
for marker_path in markers:
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    if set(marker) != {'manifest', 'result', 'source'}:
        raise SystemExit('antibody_denovo:frustrampnn_ambiguous_publication')
    result_path = resolve_job_output(marker['result'])
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('status') != 'succeeded':
        raise SystemExit('antibody_denovo:frustrampnn_required_candidate_failed')
    candidate_id = result.get('candidate_id')
    if not candidate_id or candidate_id in seen:
        raise SystemExit('antibody_denovo:frustrampnn_ambiguous_candidate')
    seen.add(candidate_id)
    candidates.append({
        'candidate_id': candidate_id,
        'invocation_id': result.get('invocation_id'),
        'source_sha256': (result.get('source_artifact') or {}).get('sha256'),
        'result': marker['result'],
        'manifest': marker['manifest'],
        'source': marker['source'],
        'status': 'succeeded',
    })
    outputs.extend((marker['result'], marker['manifest'], marker['source']))
payload = {
    'schema_name': 'antibody_denovo_frustrampnn_terminal_manifest',
    'schema_version': 1,
    'parent_job_id': '${params.job_id}',
    'parent_workflow_id': 'antibody_denovo',
    'status': 'complete',
    'requiredness': 'required',
    'candidate_count': len(candidates),
    'candidates': sorted(candidates, key=lambda item: item['candidate_id']),
    'reported_outputs': outputs,
}
Path('antibody_frustrampnn_terminal_manifest.json').write_text(
    json.dumps(payload, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8'
)
Path('frustrampnn_report_outputs.txt').write_text('\n'.join(outputs) + '\n', encoding='utf-8')
PY
    mapfile -t outputs < frustrampnn_report_outputs.txt
    test "\${#outputs[@]}" -gt 0
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' --job-root-relative \
      '${params.job_id}' frustrampnn complete "\${outputs[@]}"
    : > frustrampnn_complete.reported
    """
}

process ReportAntibodyFrustraMPNNNotRequested {
    label 'CPU'
    publishDir "${params.out_dir}/frustrampnn", mode: 'copy', pattern: 'antibody_frustrampnn_terminal_manifest.json'

    input:
    val trigger

    output:
    tuple val(parent_status), path('antibody_frustrampnn_terminal_manifest.json'), path('frustrampnn_not_requested.reported'), emit: result

    script:
    parent_status = [
        component_id: 'frustrampnn',
        parent_job_id: params.job_id.toString(),
        parent_workflow_id: 'antibody_denovo',
        status: 'not_requested',
        requiredness: 'not_requested',
    ]
    def payload = JsonOutput.toJson([
        schema_name: 'antibody_denovo_frustrampnn_terminal_manifest',
        schema_version: 1,
        parent_job_id: params.job_id.toString(),
        parent_workflow_id: 'antibody_denovo',
        status: 'not_requested',
        requiredness: 'not_requested',
        candidate_count: 0,
        candidates: [],
        reported_outputs: [],
    ])
    """
    set -euo pipefail
    printf '%s\n' '${payload}' > antibody_frustrampnn_terminal_manifest.json
    '${params.api_python}' '${params.code_root}/scripts/stage_reporter.py' \
      '${params.job_id}' frustrampnn not_requested
    : > frustrampnn_not_requested.reported
    """
}
