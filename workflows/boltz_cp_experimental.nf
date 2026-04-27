#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include {
    RunBoltzCPExperimental;
    FinalizeBoltzCPExperimental;
    BuildBoltzCPPlanManifest;
    SpawnBoltzCPChildren;
    WaitForBoltzCPChildren;
    FinalizeBoltzCPExperimentalChildren;
} from '../modules/boltz_cp_experimental.nf'

workflow BOLTZ_CP_EXPERIMENTAL {
    main:
        if (!params.bcp_input_path) {
            error("bcp_input_path is required for boltz_cp_experimental")
        }

        def inputFormat = (params.bcp_input_format ?: 'config_files').toString()
        if (!(inputFormat in ['config_files', 'preprocessed'])) {
            error("bcp_input_format must be one of: config_files, preprocessed")
        }

        def inputTarget = file(params.bcp_input_path)
        if (!inputTarget.exists()) {
            error("Boltz-CP input path not found: ${params.bcp_input_path}")
        }

        def sizeCp = (params.bcp_size_cp ?: 4) as Integer
        def sizeCpAxis = Math.sqrt(sizeCp as double) as Integer
        if (sizeCpAxis * sizeCpAxis != sizeCp) {
            error("bcp_size_cp must be a perfect square")
        }

        def shardPlanId = (params.bcp_shard_plan_id ?: '2x2').toString()
        def logicalSizeCp = ['1x1': 1, '2x2': 4, '4x4': 16].get(shardPlanId, sizeCp) as Integer
        def bcpRole = params.get('bcp_role', 'coordinator').toString()
        def requestedBackend = (params.bcp_backend ?: 'dram-context-spill-workhorse').toString()
        def requiresPlanRuntime = requestedBackend == 'dram-context-spill-workhorse'
        def useCoordinator = bcpRole != 'child' && (logicalSizeCp > 1 || requiresPlanRuntime)
        def parentJobId = (params.containsKey('job_id') ? params['job_id'] : java.util.UUID.randomUUID().toString().take(8)).toString()
        def batchName = (params.batch_name ?: "boltz_cp_${parentJobId}").toString()

        def predictionDirChannel
        def processedDirChannel
        def emittedPdbs
        def emittedCifs
        def emittedJsons
        def emittedNpzs

        if (useCoordinator) {
            BuildBoltzCPPlanManifest(parentJobId, batchName, inputTarget)
            SpawnBoltzCPChildren(parentJobId, batchName, BuildBoltzCPPlanManifest.out.manifest, BuildBoltzCPPlanManifest.out.plan_store)
            WaitForBoltzCPChildren(parentJobId, SpawnBoltzCPChildren.out.result, batchName)
            FinalizeBoltzCPExperimentalChildren(WaitForBoltzCPChildren.out.result, BuildBoltzCPPlanManifest.out.plan_store)

            predictionDirChannel = FinalizeBoltzCPExperimentalChildren.out.results_dir
            processedDirChannel = FinalizeBoltzCPExperimentalChildren.out.bundle_manifests
            emittedPdbs = FinalizeBoltzCPExperimentalChildren.out.pdbs
            emittedCifs = FinalizeBoltzCPExperimentalChildren.out.cifs
            emittedJsons = FinalizeBoltzCPExperimentalChildren.out.jsons
            emittedNpzs = FinalizeBoltzCPExperimentalChildren.out.npzs
        } else {
            RunBoltzCPExperimental(inputTarget)
            FinalizeBoltzCPExperimental(RunBoltzCPExperimental.out.results_dir)

            predictionDirChannel = RunBoltzCPExperimental.out.results_dir
            processedDirChannel = RunBoltzCPExperimental.out.processed_dir
            emittedPdbs = FinalizeBoltzCPExperimental.out.pdbs
            emittedCifs = FinalizeBoltzCPExperimental.out.cifs
            emittedJsons = FinalizeBoltzCPExperimental.out.jsons
            emittedNpzs = FinalizeBoltzCPExperimental.out.npzs
        }

    emit:
        prediction_dir = predictionDirChannel
        processed_dir = processedDirChannel
        pdbs = emittedPdbs
        cifs = emittedCifs
        jsons = emittedJsons
        npzs = emittedNpzs
}

workflow {
    def shardPlanId = (params.bcp_shard_plan_id ?: '2x2').toString()
    def logicalSizeCp = ['1x1': 1, '2x2': 4, '4x4': 16].get(shardPlanId, params.bcp_size_cp ?: 4)
    def bcpRole = params.get('bcp_role', 'coordinator').toString()
    println("=" * 60)
    println("Boltz-CP Experimental Workflow")
    println("=" * 60)
    println("* Role: ${bcpRole}")
    println("* Input path: ${params.bcp_input_path}")
    println("* Logical shard plan: ${shardPlanId} (${logicalSizeCp} logical shards)")
    println("* Physical GPU IDs: ${params.bcp_gpu_ids ?: params.gpu_id}")
    println("* Physical launch size_cp: ${params.bcp_size_cp ?: 4}")
    println("* Backend: ${params.bcp_backend ?: 'dram-context-spill-workhorse'}")
    println("* Data-plane boundary: current large-protein coordinator is control-plane/tile-store; not native distributed context-parallel Boltz prediction")
    println("* Input format: ${params.bcp_input_format ?: 'config_files'}")
    println("* Sampling steps: ${params.bcp_sampling_steps ?: 200}")
    BOLTZ_CP_EXPERIMENTAL()
}
