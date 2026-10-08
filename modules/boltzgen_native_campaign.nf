nextflow.enable.dsl = 2
include { RunBoltzGen; FilterBoltzGen } from './boltzgen.nf'

process PlanNativeBoltzGenCampaign {
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    input:
    val total
    val size
    val settings
    val parent
    path input_bundle
    output:
    path 'campaign_plan.json', emit: plan
    script:
    def encoded = groovy.json.JsonOutput.toJson(settings).bytes.encodeBase64().toString()
    """
    '${params.api_python}' '${params.code_root}/scripts/native_boltzgen_campaign.py' plan \
      --total '${total}' --size '${size}' --settings-base64 '${encoded}' --parent '${parent}' --input-bundle '${input_bundle}'
    """
}

process CollectNativeBoltzGenCampaign {
    label 'CPU'
    stageInMode 'copy'
    errorStrategy 'terminate'
    publishDir "${params.out_dir}/components/boltzgen", mode: 'copy', pattern: 'campaign'
    input:
    path plan
    val metadata
    path filtered, stageAs: 'child??/*'
    output:
    path 'campaign', emit: evidence
    path 'campaign/selected', emit: selected
    path 'campaign/collection_manifest.json', emit: receipt
    script:
    def roots = filtered instanceof List ? filtered : [filtered]
    if (metadata.size() != roots.size()) error('BoltzGen terminal metadata/output cardinality mismatch')
    def records = metadata.withIndex().collect { meta, index -> [meta, roots[index].toString()] }
    def encoded = groovy.json.JsonOutput.toJson(records).bytes.encodeBase64().toString()
    """
    '${params.api_python}' '${params.code_root}/scripts/native_boltzgen_campaign.py' collect \
      --plan '${plan}' --records-base64 '${encoded}'
    """
}

workflow NativeBoltzGenCampaign {
    take:
    input_bundle
    total
    size
    parent
    main:
    def settings = new LinkedHashMap()
    // Preparation science is bound by input_bindings_sha256, not rebased host
    // path strings. Child overrides must actually be consumed by native run or
    // filter; silently ignoring an input-preparation override is forbidden.
    def nativeSettings = ['boltzgen_additional_filters', 'boltzgen_alpha', 'boltzgen_batch_size', 'boltzgen_budget', 'boltzgen_checkpoint_mode', 'boltzgen_diffusion_batch_size', 'boltzgen_extra_config', 'boltzgen_filter_biased', 'boltzgen_inverse_fold_avoid', 'boltzgen_inverse_fold_num_sequences', 'boltzgen_max_rmsd', 'boltzgen_metrics_override', 'boltzgen_min_conf_score', 'boltzgen_min_plddt', 'boltzgen_noise_scale', 'boltzgen_protocol', 'boltzgen_refolding_rmsd_threshold', 'boltzgen_reuse', 'boltzgen_size_buckets', 'boltzgen_skip_inverse_folding', 'boltzgen_step_scale']
    nativeSettings.each { key -> settings[key] = params.get(key) }
    settings['core_protein_scientific_contract'] = params.get('core_protein_scientific_contract')
    def extras = params.get('boltzgen_extra_params')
    if (extras) {
        def parsed = new groovy.json.JsonSlurper().parseText(extras.toString())
        if (!(parsed instanceof Map) || parsed.keySet().any { !(it in nativeSettings) }) {
            error('BoltzGen child overrides must be known scientific settings; grouping and runtime paths cannot be overridden')
        }
        settings.putAll(parsed)
    }
    PlanNativeBoltzGenCampaign(total, size, Channel.value(settings), parent, input_bundle)
    children = PlanNativeBoltzGenCampaign.out.plan.flatMap { plan ->
        new groovy.json.JsonSlurper().parse(plan).children
    }.combine(input_bundle).map { meta, bundle -> tuple(meta, bundle) }
    RunBoltzGen(children)
    inference = RunBoltzGen.out.native_outputs.branch { meta, root ->
        succeeded: new groovy.json.JsonSlurper().parse(root.resolve('component_execution.json')).exit_code == 0
        failed: true
    }
    filterInputs = inference.succeeded.map { meta, root ->
        def files = root.toFile().listFiles().sort { a, b -> a.name <=> b.name }
        tuple(meta, files.findAll { it.name.endsWith('.pdb') }.collect { file(it.toPath()) },
            files.findAll { it.name != 'component_execution.json' && it.name ==~ /.*\.(json|npz|csv)/ }.collect { file(it.toPath()) })
    }
    FilterBoltzGen(filterInputs)
    joined = FilterBoltzGen.out.native_outputs.mix(inference.failed).toList().map { rows ->
        def ordered = rows.sort { a, b -> a[0].index <=> b[0].index }
        tuple(ordered.collect { it[0] }, ordered.collect { it[1] })
    }
    CollectNativeBoltzGenCampaign(PlanNativeBoltzGenCampaign.out.plan,
        joined.map { metadata, roots -> metadata }, joined.map { metadata, roots -> roots })
    emit:
    pdbs = CollectNativeBoltzGenCampaign.out.selected.flatMap { root ->
        root.toFile().listFiles().sort { a, b -> a.name <=> b.name }.collect { file(it.toPath()) }
    }
    receipt = CollectNativeBoltzGenCampaign.out.receipt
}
