nextflow.enable.dsl = 2

include {
    RunConforNets;
    FinalizeConforNetsOutputs;
} from './confornets_experimental.nf'

def shellQuote(value) {
    String text = value == null ? '' : value.toString()
    return "'${text.replace("'", "'\"'\"'")}'"
}

process PrepCanonicalConforNetsRequest {
    label 'local_cpu'
    stageInMode 'copy'

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
    def nativePath = shellQuote(native_results.toString())
    """
    set -euo pipefail
    test -f ${coordinatePlanPath}
    python3 ${params.code_root}/scripts/finalize_confornets_conformational_mapping.py \
        --request ${requestPath} \
        --native-root ${nativePath} \
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

    // Dynamic legacy params cannot be safely populated from cm_request_path. The
    // canonical prep emits an equivalent authenticated native request instead;
    // RunConforNets and FinalizeConforNetsOutputs remain byte-unchanged and reused.
    PrepCanonicalConforNetsRequest(request_roots)
    RunConforNets(
        PrepCanonicalConforNetsRequest.out.request,
        PrepCanonicalConforNetsRequest.out.assets_dir,
    )
    FinalizeConforNetsOutputs(RunConforNets.out.results_dir)
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
