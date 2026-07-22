nextflow.enable.dsl=2

include { MD_ANALYZE_REPLICA } from '../../../modules/experimental/molecular_dynamics/analyze'

params.md_analysis_work_item = null
params.md_analysis_stride = 1
params.md_analysis_max_points = 2000

workflow {
    if (!params.md_analysis_work_item) error "--md_analysis_work_item is required"
    work_items = Channel.fromPath(params.md_analysis_work_item, checkIfExists: true)
        .map { work_item_file -> new groovy.json.JsonSlurper().parse(work_item_file) }
        .map { item ->
            if (item.schema != 'bms.md.analysis-work-item.v1') error "unsupported MD analysis work-item schema"
            def manifest = file(item.manifest)
            tuple(item.replica_index as int, file(manifest.parent), item.manifest_sha256.toString())
        }
    MD_ANALYZE_REPLICA(work_items)
}
