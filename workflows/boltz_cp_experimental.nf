#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { RunBoltzCPExperimental ; FinalizeBoltzCPExperimental } from '../modules/boltz_cp_experimental.nf'
include { SchedulerFrustraMPNNParentFanout } from '../modules/frustrampnn_parent_fanout.nf'

workflow BOLTZ_CP_EXPERIMENTAL {
    main:
        if (!params.bcp_input_path) {
            error("bcp_input_path is required for boltz_cp_experimental")
        }

        def cpTopology = params.get('bcp_cp_topology', '2d').toString()
        if (!(cpTopology in ['2d', '1d'])) {
            error("bcp_cp_topology must be one of: 2d, 1d")
        }
        def rawSizeCp = params.get('bcp_size_cp', 4)
        if (cpTopology == '1d' && (!(rawSizeCp.toString() ==~ /[1-9][0-9]*/) || new BigInteger(rawSizeCp.toString()) > 16)) {
            error("bcp_size_cp must be a positive integer up to 16 for 1d topology")
        }
        def sizeCp = (rawSizeCp ?: 4) as Integer
        def sizeCpAxis = Math.sqrt(sizeCp as double) as Integer
        if (cpTopology == '2d' && sizeCpAxis * sizeCpAxis != sizeCp) {
            error("bcp_size_cp must be a perfect square")
        }

        def inputFormat = (params.bcp_input_format ?: 'config_files').toString()
        if (!(inputFormat in ['config_files', 'preprocessed'])) {
            error("bcp_input_format must be one of: config_files, preprocessed")
        }

        def inputTarget = file(params.bcp_input_path)
        if (!inputTarget.exists()) {
            error("Boltz-CP input path not found: ${params.bcp_input_path}")
        }

        def rosterPath = params.get('sequence_batch_json_path')
        def tasks = rosterPath ? new groovy.json.JsonSlurper().parse(file(rosterPath).toFile()).collect { row ->
            def name = row.name
            if (name in ['.', '..'] || !(name ==~ /[\p{L}\p{N}_.-]+/) || !inputTarget.resolve(name + '.yaml').isFile()) {
                error("Missing or invalid Fold-CP variant: ${name}")
            }
            tuple(name, inputTarget)
        } : [tuple('', inputTarget)]
        if (!tasks || tasks.collect { it[0] }.toSet().size() != tasks.size()) {
            error('Fold-CP requires a nonempty roster of unique native inputs')
        }
        RunBoltzCPExperimental(Channel.fromList(tasks))
        FinalizeBoltzCPExperimental(RunBoltzCPExperimental.out.results_dir)

        if (params.run_frustrampnn != false) {
            if (!params.job_id) {
                error("job_id is required when FrustraMPNN is enabled")
            }
            if ((params.frustrampnn_requiredness ?: 'required').toString() != 'required') {
                error("FrustraMPNN must remain required when enabled")
            }

            def selectedStructures = (params.bcp_output_format ?: 'mmcif').toString().toLowerCase() == 'pdb'
                ? FinalizeBoltzCPExperimental.out.pdbs
                : FinalizeBoltzCPExperimental.out.cifs
            def foldCpCandidates = selectedStructures
                .flatten()
                .map { structureFile ->
                    def relativePath = structureFile.toString().split('/published/', 2).last()
                    def stem = relativePath.replaceFirst(/\.[^.]+$/, '').replaceAll(/[^A-Za-z0-9._-]/, '_')
                    def digest = java.security.MessageDigest.getInstance('SHA-256').digest(relativePath.getBytes('UTF-8')).encodeHex().toString().take(12)
                    def candidateId = "foldcp_${stem.take(100)}_${digest}"
                    tuple([
                        candidate_id: candidateId,
                        parent_job_id: params.job_id.toString(),
                        parent_workflow_id: 'structure_prediction',
                        producer_stage: 'structure_prediction:fold_cp',
                        producer_candidate_key: "fold_cp/${relativePath}",
                        requiredness: 'required',
                    ], structureFile)
                }

            SchedulerFrustraMPNNParentFanout(
                foldCpCandidates,
                Channel.value(params.job_id.toString()),
                Channel.value('structure_prediction'),
                Channel.value((params.frustrampnn_settings ?: '').toString()),
                Channel.value(params.get('frustrampnn_settings_value_origin', 'bms_default').toString()),
            )
        }

    emit:
        prediction_dir = RunBoltzCPExperimental.out.results_dir.map { name, directory -> directory }
        processed_dir = RunBoltzCPExperimental.out.processed_dir
        pdbs = FinalizeBoltzCPExperimental.out.pdbs
        cifs = FinalizeBoltzCPExperimental.out.cifs
        jsons = FinalizeBoltzCPExperimental.out.jsons
        npzs = FinalizeBoltzCPExperimental.out.npzs
}

workflow {
    def gpuIdsParam = params.get('bcp_gpu_ids', null)
    if (gpuIdsParam == null || gpuIdsParam.toString().trim() == '') {
        gpuIdsParam = params.get('gpu_id', '')
    }
    def gpuIds = gpuIdsParam == null ? '' : gpuIdsParam.toString().trim()
    println("=" * 60)
    println("NVIDIA Fold-CP Structure Predictor")
    println("=" * 60)
    println("* Input path: ${params.bcp_input_path}")
    println("* GPU IDs: ${gpuIds}")
    println("* Context parallel size: ${params.bcp_size_cp ?: 4}")
    println("* Input format: ${params.bcp_input_format ?: 'config_files'}")
    println("* Sampling steps: ${params.bcp_sampling_steps ?: 200}")
    BOLTZ_CP_EXPERIMENTAL()
}
