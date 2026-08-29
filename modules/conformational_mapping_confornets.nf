nextflow.enable.dsl = 2

include {
    FinalizeConforNetsOutputs;
} from './confornets_experimental.nf'

def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

process PrepCanonicalConforNetsRequest {
    label 'ConforNetsCanonical'
    stageInMode 'rellink'

    input:
    path request_root

    output:
    path 'confornets_request.json', emit: request
    path 'confornets_assets', emit: assets_dir

    script:
    def requestJson = shellQuote(request_root.resolve('cm_request_v1.json').toString())
    def coordinatePlanJson = shellQuote(request_root.resolve('cm_coordinate_plan_v1.json').toString())
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/prep_canonical_confornets_request.py \
        --request ${requestJson} \
        --coordinate-plan ${coordinatePlanJson} \
        --assets-dir confornets_assets \
        --output confornets_request.json
    """
}

process RunCanonicalConforNets {
    label 'ConforNetsCanonical'
    label 'gpu'
    stageInMode 'rellink'

    input:
    path request_json
    path assets_dir

    output:
    path 'confornets_results', emit: results_dir
    path 'run_confornets.log', emit: log

    script:
    def requestPath = shellQuote(request_json.toString())
    def assetsPath = shellQuote(assets_dir.toString())
    """
    set -euo pipefail
    python3 /scripts/run_confornets_inference.py \
        --request ${requestPath} \
        --assets-dir ${assetsPath} \
        --output-dir confornets_results \
        2>&1 | tee run_confornets.log
    """
}

process BindCanonicalConforNetsOutputLedger {
    label 'local_cpu'
    stageInMode 'copy'

    input:
    path request_root
    path native_results

    output:
    path 'bound_confornets_results', emit: results_dir

    script:
    def requestJson = shellQuote(request_root.resolve('cm_request_v1.json').toString())
    def coordinatePlanJson = shellQuote(request_root.resolve('cm_coordinate_plan_v1.json').toString())
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/bind_confornets_output_ledger.py \
        --request ${requestJson} \
        --coordinate-plan ${coordinatePlanJson} \
        --native-root ${shellQuote(native_results.toString())} \
        --out bound_confornets_results
    """
}

process FinalizeCanonicalConforNets {
    label 'local_cpu'
    stageInMode 'copy'

    publishDir "${params.out_dir}/final/conformational_mapping", mode: 'copy'

    input:
    path request_root
    path native_results

    output:
    path 'canonical_confornets', emit: canonical_dir
    path 'canonical_confornets/cm_native_artifacts_v1.json', emit: native_manifest
    path 'canonical_confornets/cm_ensemble_v1.json', emit: ensemble_manifest

    script:
    def requestPath = shellQuote(request_root.resolve('cm_request_v1.json').toString())
    def coordinatePlanPath = shellQuote(request_root.resolve('cm_coordinate_plan_v1.json').toString())
    def snapshotPath = shellQuote(request_root.resolve('cm_complex_snapshots_v1.json').toString())
    def nativePath = shellQuote(native_results.toString())
    """
    set -euo pipefail
    test -f ${coordinatePlanPath}
    test -f ${snapshotPath}
    python3 ${params.code_root}/scripts/finalize_confornets_conformational_mapping.py \
        --request ${requestPath} \
        --native-root ${nativePath} \
        --snapshot ${snapshotPath} \
        --out canonical_confornets
    """
}

workflow CONFORMATIONAL_MAPPING_CONFORNETS {
    take:
    target_tuples

    main:
    request_roots = target_tuples.map { _target_meta, request_root ->
        request_root
    }

    // The canonical lane is isolated on the instrumented image. Legacy execution
    // and normalization remain unchanged.
    PrepCanonicalConforNetsRequest(request_roots)
    RunCanonicalConforNets(
        PrepCanonicalConforNetsRequest.out.request,
        PrepCanonicalConforNetsRequest.out.assets_dir,
    )
    FinalizeConforNetsOutputs(RunCanonicalConforNets.out.results_dir)
    BindCanonicalConforNetsOutputLedger(
        request_roots,
        FinalizeConforNetsOutputs.out.results_dir,
    )
    FinalizeCanonicalConforNets(
        request_roots,
        BindCanonicalConforNetsOutputLedger.out.results_dir,
    )

    emit:
    canonical_dir = FinalizeCanonicalConforNets.out.canonical_dir
    native_manifest = FinalizeCanonicalConforNets.out.native_manifest
    ensemble_manifest = FinalizeCanonicalConforNets.out.ensemble_manifest
}
