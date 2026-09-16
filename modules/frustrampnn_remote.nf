nextflow.enable.dsl = 2

include { CanonicalFrustraMPNNV2 } from './frustrampnn.nf'

// Remote execution stays inside the parent's Nextflow DAG and GPU lease.
// No scheduler child jobs, API callbacks, or alternate model implementation.
process PackRemoteFrustraMPNNRequest {
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0
    input:
    tuple val(identity), path(request), path(source), path(structure_map)
    output:
    path 'candidate_*', emit: candidate
    script:
    if (!(identity ==~ /[A-Za-z0-9_-]+/)) error('Unsafe canonical candidate identity')
    """
    set -euo pipefail
    mkdir 'candidate_${identity}'
    cp -L '${request}' 'candidate_${identity}/workflow_component_request_v3.json'
    cp -L '${source}' 'candidate_${identity}/canonical_source.pdb'
    cp -L '${structure_map}' 'candidate_${identity}/frustrampnn_structure_map_v1.json'
    """
}

process RemoteFrustraMPNNGroupedTask {
    label 'frustrampnn_gpu'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0
    publishDir "${params.out_dir}/frustrampnn/remote_batches", mode: 'copy', pattern: 'batch_receipts/*', saveAs: { name -> new File(name).name }
    input:
    path candidates
    output:
    path 'grouped_results', emit: bundles
    path 'batch_receipts/*', emit: receipts
    script:
    def assignedGpu = params.frustrampnn_physical_gpu_id?.toString()
    if (!(assignedGpu ==~ /(?:0|[1-9][0-9]*)/)) error('Remote canonical batch requires scheduler-assigned GPU')
    def candidateArgs = candidates.collect { "--candidate-dir '${it}'" }.join(' ')
    def apptainerBin = params.get('apptainer_bin') ?: 'apptainer'
    """
    set -euo pipefail
    export CUDA_VISIBLE_DEVICES='${assignedGpu}'
    '${params.api_python}' '${params.code_root}/scripts/remote_frustrampnn_batch.py' \
      ${candidateArgs} \
      --container '${params.container_dir}/frustrampnn.sif' \
      --apptainer '${apptainerBin}' --physical-gpu-id '${assignedGpu}'
    """
}

process PlanRemoteFrustraMPNNGroups {
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    maxRetries 0
    publishDir "${params.out_dir}/frustrampnn/component_groups", mode: 'copy', pattern: 'groups/grouping_plan_v1.json'
    input:
    path candidates
    output:
    path 'groups/group_*', emit: groups
    path 'groups/components.sqlite', emit: ledger
    path 'groups/grouping_plan_v1.json', emit: plan
    script:
    def candidateArgs = candidates.collect { "--candidate-dir '${it}'" }.join(' ')
    """
    set -euo pipefail
    '${params.api_python}' '${params.code_root}/scripts/plan_frustrampnn_groups.py' ${candidateArgs}
    """
}

workflow RemoteCanonicalFrustraMPNN {
    take:
    prepared

    main:
    if (System.getenv('BMS_REMOTE_EXECUTION') != '1') error('Trusted remote execution envelope required')
    // Read the real upstream path, never a process TaskPath before staging.
    identified = prepared.map { request, source, structure_map ->
        def identity = new groovy.json.JsonSlurper().parseText(request.text).candidate_id.toString()
        tuple(identity, request, source, structure_map)
    }
    PackRemoteFrustraMPNNRequest(identified)
    // Collect once to make grouping deterministic despite producer completion order.
    PlanRemoteFrustraMPNNGroups(PackRemoteFrustraMPNNRequest.out.candidate.collect())
    groups = PlanRemoteFrustraMPNNGroups.out.groups.flatten().map { root ->
        root.toFile().listFiles().sort { a, b -> a.name <=> b.name }.collect { file(it.toPath()) }
    }
    routed = groups.branch {
        single: it.size() == 1
        grouped: it.size() > 1
    }
    RemoteFrustraMPNNGroupedTask(routed.grouped)
    // A remainder singleton must not overlap a grouped task on the same GPU.
    grouped_complete = RemoteFrustraMPNNGroupedTask.out.bundles.collect().map { true }.ifEmpty(true)
    singles = routed.single.map { group -> tuple(group.first()) }.combine(grouped_complete).map { root, ignored ->
        tuple(root.resolve('workflow_component_request_v3.json'), root.resolve('canonical_source.pdb'), root.resolve('frustrampnn_structure_map_v1.json'))
    }
    CanonicalFrustraMPNNV2(singles)
    grouped_results = RemoteFrustraMPNNGroupedTask.out.bundles.flatMap { root ->
        root.toFile().listFiles().sort { a, b -> a.name <=> b.name }.collect { bundle ->
            def result = new groovy.json.JsonSlurper().parse(new File(bundle, 'workflow_component_result_v3.json'))
            tuple(result, file(bundle.toPath()), file(new File(bundle, 'frustrampnn_result_manifest_v3.json').toPath()))
        }
    }
    terminal_results = CanonicalFrustraMPNNV2.out.result.mix(grouped_results)
    emit:
    result = terminal_results
}
