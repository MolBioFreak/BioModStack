#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

import groovy.json.JsonSlurper
include { RunLigandMPNNInterfaceContext } from '../modules/ligandmpnn_interface_context'

// Parent hook: --interface_context_manifest <JSON array of selected records>.
// Each record has invocation_id, request_path and source_path. Request JSON is
// the existing Foundry adapter's exact typed request; source_path is its sealed
// candidate PDB snapshot. No manifest/no selected records means no image or work.
workflow {
    def manifestName = params.get('interface_context_manifest')
    if (manifestName) {
        def records = new JsonSlurper().parse(file(manifestName.toString()))
        if (!(records instanceof List)) {
            throw new IllegalArgumentException('interface context manifest must be a list')
        }
        def ids = [] as Set
        def selected = records.collect { record ->
            if (!(record instanceof Map) || (record.keySet() as Set) != (['invocation_id', 'request_path', 'source_path'] as Set)) {
                throw new IllegalArgumentException('invalid interface context selected record')
            }
            def id = record.invocation_id?.toString()
            if (!(id ==~ /[A-Za-z0-9][A-Za-z0-9._-]{0,127}/) || !ids.add(id)) {
                throw new IllegalArgumentException('duplicate or unsafe interface context invocation identity')
            }
            tuple(id, file(record.request_path.toString()), file(record.source_path.toString()))
        }
        if (selected) {
            RunLigandMPNNInterfaceContext(Channel.fromList(selected))
        }
    }
}
