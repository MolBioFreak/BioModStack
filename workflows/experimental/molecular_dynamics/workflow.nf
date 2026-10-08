nextflow.enable.dsl=2

// Local and worker placement share the same complete native DAG.
include { MD_CLOSURE } from './orchestrator'
workflow { MD_CLOSURE() }
