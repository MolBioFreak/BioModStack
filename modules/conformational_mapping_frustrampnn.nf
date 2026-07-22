nextflow.enable.dsl = 2

process CanonicalConformationalAnalysisPlane {
    tag "cm-analysis:${request_id}"
    // Contract processing runs in the API environment; only FrustraMPNN itself
    // is isolated in the registered scientific container.
    label 'cm_gpu'
    stageInMode 'copy'

    publishDir "${params.out_dir}/final/conformational_mapping/${backend_dir}", mode: 'copy'

    input:
    tuple val(request_id), val(backend_dir), path(request_root), path(canonical_dir), val(checkpoint)

    output:
    tuple val(request_id), path('canonical_result'), emit: canonical
    path 'canonical_result/cm_native_artifacts_v1.json', emit: native_manifest
    path 'canonical_result/cm_ensemble_v1.json', emit: ensemble_manifest
    path 'canonical_result/cm_derived_index_v1.json', emit: derived_index

    def assigned_gpu = params.gpu_id?.toString() ?: System.getenv('NXF_DEFAULT_GPU') ?: '0'
    if (!(assigned_gpu ==~ /(?:0|[1-9][0-9]*)/)) {
        throw new IllegalArgumentException('conformational-mapping GPU must be a canonical non-negative integer')
    }

    script:
    """
    set -euo pipefail
    export CUDA_VISIBLE_DEVICES='${assigned_gpu}'
    ${params.api_python} ${params.code_root}/scripts/run_conformational_mapping_analysis_plane.py \
      --request ${request_root}/cm_request_v1.json \
      --runtime-registry ${request_root}/cm_runtime_registry_v1.json \
      --snapshots ${request_root}/cm_complex_snapshots_v1.json \
      --canonical ${canonical_dir} \
      --checkpoint ${checkpoint} \
      --checkpoint-id megascale.ckpt \
      --frustrampnn-container ${params.container_dir}/frustrampnn.sif \
      --gpu-id '${assigned_gpu}' \
      --out canonical_result
    """
}

workflow CONFORMATIONAL_MAPPING_ANALYSIS_PLANE {
    take:
    analysis_tuples

    main:
    CanonicalConformationalAnalysisPlane(analysis_tuples)

    emit:
    canonical = CanonicalConformationalAnalysisPlane.out.canonical
    native_manifest = CanonicalConformationalAnalysisPlane.out.native_manifest
    ensemble_manifest = CanonicalConformationalAnalysisPlane.out.ensemble_manifest
    derived_index = CanonicalConformationalAnalysisPlane.out.derived_index
}
