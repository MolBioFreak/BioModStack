nextflow.enable.dsl = 2

include { RunCalibyBinder } from '../modules/caliby.nf'

workflow {
    // API-owned immutable, uniquely named selected snapshots. Native batch and
    // result normalization are the same implementation used by antibody jobs.
    def selected = params.pdb_paths.toString().split(',').findAll { it }.collect { file(it) }
    def source = params.get('source_identity_json') ? file(params.source_identity_json) : []
    RunCalibyBinder(Channel.value(tuple(selected, source)))
}
