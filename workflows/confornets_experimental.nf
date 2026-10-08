#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include {
    PrepConforNetsRequest;
    RunConforNets;
    FinalizeConforNetsOutputs;
} from '../modules/confornets_experimental.nf'

workflow CONFORNETS_EXPERIMENTAL {
    main:
        def task = (params.cn_task ?: 'diversity').toString()
        if (!(task in ['diversity', 'mse', 'transfer'])) {
            error("cn_task must be one of: diversity, mse, transfer")
        }
        if (!params.cn_sequence) {
            error("cn_sequence is required for confornets_experimental")
        }
        def sequenceText = params.cn_sequence.toString()
        if (sequenceText.contains(':') || sequenceText.contains(',') || sequenceText.contains(';') || sequenceText.contains('/')) {
            error("confornets_experimental is monomer-only; cn_sequence must be one single protein chain")
        }
        if (task == 'mse' && !params.cn_reference_pdb_1) {
            error("cn_reference_pdb_1 is required for ConforNets MSE steering")
        }
        if (task == 'transfer' && !params.cn_confornet_path && !params.cn_mse_dir) {
            error("ConforNets transfer requires cn_confornet_path or cn_mse_dir")
        }

        PrepConforNetsRequest()
        RunConforNets(PrepConforNetsRequest.out.request, PrepConforNetsRequest.out.assets_dir)
        FinalizeConforNetsOutputs(RunConforNets.out.results_dir)

    emit:
        prediction_dir = RunConforNets.out.results_dir
        normalized_dir = FinalizeConforNetsOutputs.out.results_dir
        cifs = FinalizeConforNetsOutputs.out.cifs
        jsons = FinalizeConforNetsOutputs.out.jsons
        csvs = FinalizeConforNetsOutputs.out.csvs
        states = FinalizeConforNetsOutputs.out.states
}

workflow {
    println("=" * 60)
    println("Conformational Mapping Experimental Workflow (ConforNets backend)")
    println("=" * 60)
    println("* Task: ${params.cn_task ?: 'diversity'}")
    println("* Benchmark: ${params.cn_benchmark_name ?: 'bms_confornets'}")
    println("* Test case: ${params.cn_test_case_name ?: 'monomer_case'}")
    println("* Samples per state: ${params.cn_num_samples ?: 5}")
    println("* Monomer-only: true")
    CONFORNETS_EXPERIMENTAL()
}
