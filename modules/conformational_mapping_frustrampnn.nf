nextflow.enable.dsl = 2

process PrepareConformationalMappingFrustraMPNNV2 {
    tag "cm-frustrampnn-prepare:${request_id}"
    label 'CPU'
    errorStrategy 'terminate'
    maxRetries 0
    stageInMode 'copy'

    input:
    tuple val(request_id), val(backend_dir), path(request_root), path(canonical_dir)

    output:
    tuple val(request_id), val(backend_dir), path('frustrampnn_prepared'), \
        path('cm_frustrampnn_preparation_manifest_v1.json'), emit: prepared

    script:
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/prepare_conformational_mapping_frustrampnn_v2.py' \
      --parent-job-id '${params.job_id}' \
      --request '${request_root}/cm_request_v1.json' \
      --snapshots '${canonical_dir}/cm_complex_snapshots_v1.json' \
      --canonical '${canonical_dir}' \
      --output-dir frustrampnn_prepared \
      --manifest cm_frustrampnn_preparation_manifest_v1.json
    """
}

process CanonicalConformationalAnalysisPlaneV2 {
    tag "cm-analysis-v2:${request_id}"
    label 'CPU'
    errorStrategy 'terminate'
    maxRetries 0
    stageInMode 'copy'

    publishDir "${params.out_dir}/final/conformational_mapping/${backend_dir}", \
        mode: 'copy', overwrite: true

    input:
    tuple val(request_id), val(backend_dir), path(request_root), path(canonical_dir)
    path(preparation_manifest)
    path(result_bundles)

    output:
    tuple val(request_id), path('canonical_result'), emit: canonical

    script:
    def bundleArgs = result_bundles.collect { bundle -> "--bundle '${bundle}'" }.join(' \\\n      ')
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/postprocess_conformational_mapping_frustrampnn_v2.py' \
      --request '${request_root}/cm_request_v1.json' \
      --canonical '${canonical_dir}' \
      --preparation-manifest '${preparation_manifest}' \
      ${bundleArgs} \
      --out canonical_result
    """
}

process StageConformationalMappingFrustraMPNNResult {
    tag "cm-frustrampnn-stage:${component_result.candidate_id}"
    label 'CPU'
    errorStrategy 'terminate'
    maxRetries 0
    stageInMode 'copy'

    input:
    tuple val(component_result), path(candidate_bundle), path(result_manifest)

    output:
    tuple val(component_result), path("${component_result.candidate_id}"), emit: staged

    script:
    """
    set -euo pipefail
    cp -a '${candidate_bundle}' '${component_result.candidate_id}'
    """
}
