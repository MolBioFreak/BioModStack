nextflow.enable.dsl = 2

process CanonicalConformationalImport {
    tag "cm-import:${request_id}"
    label 'local_cpu'
    stageInMode 'copy'

    input:
    tuple val(request_id), path(request_root)

    output:
    tuple val(request_id), path('canonical_import'), emit: canonical
    path 'canonical_import/cm_native_artifacts_v1.json', emit: native_manifest
    path 'canonical_import/cm_ensemble_v1.json', emit: ensemble_manifest

    script:
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/finalize_import_conformational_mapping.py \
      --request ${request_root}/cm_request_v1.json \
      --snapshot ${request_root}/cm_complex_snapshots_v1.json \
      --staged-root ${request_root}/registered_import \
      --out canonical_import
    """
}

workflow CONFORMATIONAL_MAPPING_IMPORT {
    take:
    request_tuples
    main:
    CanonicalConformationalImport(request_tuples)
    emit:
    canonical = CanonicalConformationalImport.out.canonical
    native_manifest = CanonicalConformationalImport.out.native_manifest
    ensemble_manifest = CanonicalConformationalImport.out.ensemble_manifest
}
