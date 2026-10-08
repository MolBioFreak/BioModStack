nextflow.enable.dsl = 2

// This is the legacy collector's job-index filename coordinate, now carried by
// the canonical runner's output tuple rather than a host database query.
process CollectNativeAntibodyFAMPNN {
    label 'process_low'
    publishDir "${params.out_dir}/collected/fampnn", mode: 'copy', pattern: 'job*'
    publishDir "${params.out_dir}/components/fampnn", mode: 'copy', pattern: '*receipt.json'
    input:
    tuple val(batch_id), path(pdbs), path(jsons)
    output:
    tuple path('job*.pdb'), path('job*.json'), emit: outputs
    path "batch_${batch_id}_receipt.json", emit: receipt
    script:
    def manifest = groovy.json.JsonOutput.toJson([
        schema_name: 'bms.antibody-component-result.v1', schema_version: 1,
        parent_job_id: params.job_id, stage: 'fampnn', group_ordinal: batch_id,
        component_id: "antibody_denovo:${params.job_id}:fampnn:${batch_id}",
        requiredness: 'required', status: 'complete',
        pdbs: (pdbs instanceof Collection ? pdbs : [pdbs]).collect { it.toString() },
        annotations: (jsons instanceof Collection ? jsons : [jsons]).collect { it.toString() }])
    def encoded = manifest.getBytes('UTF-8').encodeBase64().toString()
    """
    printf '%s' '${encoded}' | base64 --decode > inputs.json
    python3 - <<'PY'
import hashlib
import json
import shutil
from pathlib import Path
receipt = json.loads(Path('inputs.json').read_text())
artifacts = []
for role in ('pdbs', 'annotations'):
    for source in receipt.pop(role):
        path = Path(source)
        name = 'job${batch_id}_' + path.name
        data = path.read_bytes()
        shutil.copyfile(path, name)
        artifacts.append(dict(role=role, relative_path=name, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data)))
receipt['artifacts'] = artifacts
Path('batch_${batch_id}_receipt.json').write_text(json.dumps(receipt, sort_keys=True))
PY
    """
}


process RecordAntibodyValidationPlan {
    label 'process_low'
    publishDir "${params.out_dir}/components/structure_validation", mode: 'copy'
    input:
    val plan
    output:
    path 'validation_component_plan.json', emit: plan
    script:
    def encoded = groovy.json.JsonOutput.toJson(plan).getBytes('UTF-8').encodeBase64().toString()
    """
    printf '%s' '${encoded}' | base64 --decode > validation_component_plan.json
    """
}
