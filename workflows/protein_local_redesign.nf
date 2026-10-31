#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

params.plr_validator_suite_active = true

include { RunFAMPNN ; FilterFAMPNN } from '../modules/fampnn.nf'
include { RunMPNN ; FilterMPNN } from '../modules/proteinmpnn.nf'
include { PrepBoltz ; RunBoltz } from '../modules/boltz.nf'
include { ESMFold2FromPdb } from '../modules/esmfold2_experimental.nf'
include { ProtenixFromComplex } from '../modules/protenix.nf'

def shellQuote(value) {
    return "'" + value.toString().replace("'", "'\"'\"'") + "'"
}

def parseProteinLocalValidators(raw) {
    def values = raw == null
        ? ['protenix_v2']
        : raw.toString().split(',').collect { it.trim() }.findAll { it }
    def supported = ['boltz2', 'esmfold2', 'protenix_v2']
    if (values.size() < 1 || values.size() > 3 || values.toSet().size() != values.size()) {
        throw new IllegalArgumentException('plr_structure_validators must contain one to three unique validators')
    }
    def unknown = values.findAll { !supported.contains(it) }
    if (unknown) {
        throw new IllegalArgumentException("Unsupported Protein Local Redesign validators: ${unknown.join(',')}")
    }
    return supported.findAll { values.contains(it) }
}

def loadProteinLocalDesignArtifacts(rawDir) {
    def resolvedDir = file(rawDir.toString())
    def pdbs = resolvedDir.exists()
        ? (resolvedDir.listFiles()?.findAll { candidate -> candidate.name.toLowerCase().endsWith('.pdb') }?.sort { left, right -> left.name <=> right.name }?.collect { candidate -> file(candidate.toString()) } ?: [])
        : []
    def jsons = resolvedDir.exists()
        ? (resolvedDir.listFiles()?.findAll { candidate -> candidate.name.toLowerCase().endsWith('.json') }?.sort { left, right -> left.name <=> right.name }?.collect { candidate -> file(candidate.toString()) } ?: [])
        : []
    return channel.of([pdbs, jsons])
}

def loadProteinLocalPdbCollection(rawDir) {
    def resolvedDir = file(rawDir.toString())
    def pdbs = resolvedDir.exists()
        ? (resolvedDir.listFiles()?.findAll { candidate -> candidate.name.toLowerCase().endsWith('.pdb') }?.sort { left, right -> left.name <=> right.name }?.collect { candidate -> file(candidate.toString()) } ?: [])
        : []
    return channel.of(pdbs)
}

def partitionProteinLocalGpuBatches(allPdbs, gpus) {
    def totalSize = allPdbs.size()
    if (totalSize == 0) {
        return []
    }
    def batchCount = Math.max(1, Math.min((gpus ?: 1) as int, totalSize))
    def batchSize = (totalSize / batchCount).doubleValue()
    def index = 0
    def batches = allPdbs.collect { pdb ->
        def position = index
        index = index + 1
        def batchId = (position / batchSize).intValue()
        [batchId, pdb]
    }
    return batches
}

// Closed outputs retain independent names; cache hits republish too.
def proteinlocalredesignPublishMode(workDir, outputDir) {
    def destination = file(outputDir).toAbsolutePath()
    while (!java.nio.file.Files.exists(destination)) destination = destination.parent
    java.nio.file.Files.getFileStore(workDir) == java.nio.file.Files.getFileStore(destination) ? 'link' : 'copy'
}

process PrepareProteinLocalValidatorInput {
    tag "${producer_meta.candidate_id}"
    label 'process_low'
    errorStrategy 'ignore'

    publishDir "${params.out_dir}/validation/contracts", mode: 'copy', pattern: '*.validator_contract.json'

    input:
    tuple val(producer_meta), path(source_pdb)

    output:
    tuple val(producer_meta), path(source_pdb), path('*.validator_contract.json'), path('*.protenix.json'), emit: prepared

    script:
    def candidateId = producer_meta.candidate_id
    """
    set -euo pipefail
    python3 ${params.code_root}/scripts/prepare_protein_local_validator_input.py \
        --input-pdb ${shellQuote(source_pdb)} \
        --contract-out ${candidateId}.validator_contract.json \
        --protenix-out ${candidateId}.protenix.json \
        --model-seeds ${shellQuote(params.protenix_seeds ?: '42')}
    """
}

process FinalizeProteinLocalValidatorSuite {
    label 'process_low'

    publishDir "${params.out_dir}/validation", mode: 'copy', pattern: 'validator_suite_receipt.json'

    input:
    val requested_validators
    val expected_candidate_count
    val validator_summaries

    output:
    path 'validator_suite_receipt.json', emit: receipt

    script:
    def normalized = validator_summaries.collectEntries { [(it.validator): (it.completed_candidates as Integer)] }
    def expectedCount = expected_candidate_count as Integer
    def artifactRoots = [
        boltz2: 'validation/boltz2',
        esmfold2: 'validation/esmfold2',
        protenix_v2: 'validation/protenix_v2',
    ]
    def enrichedSummaries = requested_validators.collect { validator ->
        def completedCount = normalized.get(validator, 0) as Integer
        [
            validator: validator,
            state: expectedCount > 0 && completedCount == expectedCount ? 'complete' : 'failed',
            completed_candidates: completedCount,
            expected_candidate_count: expectedCount,
            artifact_root: artifactRoots[validator],
        ]
    }
    def completeCount = enrichedSummaries.count { it.state == 'complete' }
    def state = expectedCount < 1
        ? 'failed'
        : (completeCount == requested_validators.size() ? 'complete' : (completeCount == 0 ? 'failed' : 'partial'))
    def receipt = [
        schema_version: 1,
        workflow: 'protein_local_redesign',
        state: state,
        state_vocabulary: ['complete', 'partial', 'failed'],
        requested_validators: requested_validators,
        expected_candidate_count: expected_candidate_count,
        contract_root: 'validation/contracts',
        validator_summaries: enrichedSummaries,
    ]
    def receiptJson = groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(receipt))
    """
    cat > validator_suite_receipt.json <<'EOF'
${receiptJson}
EOF
    """
}

process EnforceProteinLocalValidatorSuite {
    label 'process_low'

    input:
    path suite_receipt

    output:
    path 'validator_suite_complete', emit: complete

    script:
    """
    python3 - <<'PY'
import json
from pathlib import Path

receipt_path = Path('${suite_receipt}')
receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
if receipt.get('state') != 'complete':
    raise SystemExit(
        'Protein Local Redesign validator suite is '
        + str(receipt.get('state'))
        + ': '
        + json.dumps(receipt, sort_keys=True, separators=(',', ':'))
    )
PY
    touch validator_suite_complete
    """
}

process StageProteinLocalValidatedCandidates {
    label 'process_low'

    publishDir "${params.out_dir}/validation/review_candidates", mode: "${proteinlocalredesignPublishMode(workflow.workDir, params.out_dir)}", pattern: 'review_candidates/*.pdb', saveAs: { filename -> filename.replace('review_candidates/', '') }

    input:
    path candidate_pdbs
    path suite_receipt
    path validator_suite_complete

    output:
    path 'review_candidates/*.pdb', emit: candidates

    script:
    """
    # Native writers and analysis have returned before these retained names.
    publish_closed_structure() {
        local source destination
        destination="\${@: -1}"
        for source in "\${@:1:\$#-1}"; do
            [ -f "\$source" ] || continue
            if [ "\$(stat -Lc %d "\$source")" = "\$(stat -Lc %d "\$destination")" ]; then
                cp -L -l --remove-destination "\$source" "\$destination/"
            else
                cp -L --remove-destination "\$source" "\$destination/"
            fi
        done
    }
    test -s ${suite_receipt}
    mkdir -p review_candidates
    publish_closed_structure ${candidate_pdbs} review_candidates/
    """
}

process OpenInteractiveGate {
    label 'process_low'

    publishDir "${params.out_dir}/gates", mode: 'copy', pattern: "*.json"

    input:
    val job_id
    val stage_name
    val gate_trigger
    val candidate_dir
    val raw_dir
    val filtered_dir
    val framework_type
    val antibody_chains
    val structure_validator

    output:
    path "gate_${stage_name}.json", emit: report

    script:
    def filteredArg = filtered_dir ? "--filtered_dir \"${filtered_dir}\"" : ""
    def rawArg = raw_dir ? "--raw_dir \"${raw_dir}\"" : ""
    """
    echo "Gate trigger ready: ${gate_trigger}" >&2
    python3 ${params.code_root}/scripts/open_stage_gate.py \\
        --job_id "${job_id}" \\
        --stage "${stage_name}" \\
        --candidate_dir "${candidate_dir}" \\
        ${rawArg} \\
        ${filteredArg} \\
        --framework_type "${framework_type ?: ''}" \\
        --antibody_chains "${antibody_chains ?: ''}" \\
        --structure_validator "${structure_validator ?: ''}" \\
        --api_url "${params.api_url}" \\
        --output "gate_${stage_name}.json"
    """
}

process PrepProteinLocalFAMPNN {
    label 'pyrosetta_tools'

    input:
    tuple path(pdb_files), path(json_files)
    path manifest_json

    output:
    path('fampnn_input/*.pdb'), emit: pdbs
    path('fampnn_input/*.fampnn_prep.json'), emit: provenance, optional: true
    path('fampnn.csv'), emit: csv

    script:
    def fixSidechainsFlag = params.plr_fix_fixed_sidechains ? '--fix_sidechains' : ''
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    python3 ${params.code_root}/scripts/prep_fampnn_designs.py \
        --input_dir ./ \
        --out_dir fampnn_input ${params.get('core_protein_scientific_contract') != null ? '--publish_identity' : ''}

    python3 ${params.code_root}/scripts/prep_fampnn_constraints_from_spec.py \
        --input_dir fampnn_input \
        --manifest ${manifest_json} \
        --out_csv fampnn.csv \
        ${fixSidechainsFlag}
    """
}

process PrepProteinLocalMPNN {
    label 'pyrosetta_tools'

    input:
    tuple path(pdb_files), path(json_files)
    path manifest_json

    output:
    path('mpnn_fixed/*.pdb'), emit: pdbs

    script:
    """
    eval "\$(micromamba shell hook --shell bash)"
    micromamba activate pyrosetta

    python3 ${params.code_root}/scripts/prep_mpnn_designs.py \
        --input_dir ./ \
        --out_dir mpnn_input

    python3 ${params.code_root}/scripts/add_fixed_labels_from_spec.py \
        --input_dir mpnn_input \
        --output_dir mpnn_fixed \
        --manifest ${manifest_json}
    """
}

process ExportProteinLocalMPNNResults {
    label 'process_low'

    publishDir "${params.out_dir}/pdb_files", mode: "${proteinlocalredesignPublishMode(workflow.workDir, params.out_dir)}", pattern: 'published/*.pdb', saveAs: { fn -> fn.replace('published/', '') }
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: 'published/*.json', saveAs: { fn -> fn.replace('published/', '') }
    publishDir "${params.out_dir}/run/mpnn/results", mode: "${proteinlocalredesignPublishMode(workflow.workDir, params.out_dir)}", pattern: 'published/*.pdb', saveAs: { fn -> fn.replace('published/', '') }
    publishDir "${params.out_dir}/run/mpnn/results", mode: 'copy', pattern: 'published/*.json', saveAs: { fn -> fn.replace('published/', '') }

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path('published/*.pdb'), emit: pdbs
    path('published/*.json'), emit: jsons

    script:
    """
    mkdir -p published
    # Native writers and analysis have returned before these retained names.
    publish_closed_structure() {
        local source destination
        destination="\${@: -1}"
        for source in "\${@:1:\$#-1}"; do
            [ -f "\$source" ] || continue
            if [ "\$(stat -Lc %d "\$source")" = "\$(stat -Lc %d "\$destination")" ]; then
                cp -L -l --remove-destination "\$source" "\$destination/"
            else
                cp -L --remove-destination "\$source" "\$destination/"
            fi
        done
    }
    publish_closed_structure ${pdb_files} published/
    cp ${json_files} published/
    """
}

process ExportProteinLocalBoltzPredictions {
    label 'process_low'
    errorStrategy 'ignore'

    publishDir "${params.out_dir}/validation/boltz2", mode: "${proteinlocalredesignPublishMode(workflow.workDir, params.out_dir)}", pattern: 'published/*.pdb', saveAs: { fn -> fn.replace('published/', '') }
    publishDir "${params.out_dir}/validation/boltz2", mode: 'copy', pattern: 'published/*.json', saveAs: { fn -> fn.replace('published/', '') }

    input:
    tuple path(pdb_files), path(json_files)

    output:
    path('published/*.pdb'), emit: pdbs
    path('published/*.json'), emit: jsons

    script:
    """
    mkdir -p published
    # Native writers and analysis have returned before these retained names.
    publish_closed_structure() {
        local source destination
        destination="\${@: -1}"
        for source in "\${@:1:\$#-1}"; do
            [ -f "\$source" ] || continue
            if [ "\$(stat -Lc %d "\$source")" = "\$(stat -Lc %d "\$destination")" ]; then
                cp -L -l --remove-destination "\$source" "\$destination/"
            else
                cp -L --remove-destination "\$source" "\$destination/"
            fi
        done
    }
    publish_closed_structure ${pdb_files} published/
    cp ${json_files} published/
    """
}

workflow PROTEIN_LOCAL_REDESIGN {
    main:
    if (!params.containsKey('interactive_gating')) params.interactive_gating = false
    if (!params.containsKey('interactive_gate_stage') || !params.interactive_gate_stage) params.interactive_gate_stage = 'post_structure_validation'
    if (!params.containsKey('interactive_gate_continue')) params.interactive_gate_continue = false

    if (!params.plr_backbone_input_pdbs && !params.plr_sequence_input_pdbs && !params.plr_validation_input_pdbs) {
        error('Missing required parameter: plr_backbone_input_pdbs, plr_sequence_input_pdbs or plr_validation_input_pdbs')
    }
    if (params.plr_backbone_input_pdbs && !params.plr_design_chains) {
        error('Missing required parameter: plr_design_chains')
    }

    def interactiveGateEnabled = params.interactive_gating == true
    def resumeFromValidation = params.plr_validation_input_pdbs ? true : false
    def resumeFromSequences = !resumeFromValidation && params.plr_sequence_input_pdbs ? true : false
    def resumeFromBackbones = !resumeFromValidation && !resumeFromSequences && params.plr_backbone_input_pdbs ? true : false

    def mergedBackboneArtifacts
    def manifestChannel

    if (resumeFromBackbones) {
        if (!params.plr_region_manifest) {
            error('plr_backbone_input_pdbs resume requires plr_region_manifest')
        }
        manifestChannel = Channel.of(file(params.plr_region_manifest))
        mergedBackboneArtifacts = loadProteinLocalDesignArtifacts(params.plr_backbone_input_pdbs)
    } else {
        manifestChannel = Channel.empty()
    }

    def shouldPauseAfterBackboneRemodel = interactiveGateEnabled &&
        (params.interactive_gate_stage ?: 'post_structure_validation') == 'post_rfantibody' &&
        params.interactive_gate_continue != true &&
        !resumeFromSequences &&
        !resumeFromValidation

    if (shouldPauseAfterBackboneRemodel) {
        def backboneGateDir = params.out_dir ? "${params.out_dir}/collected/protein_local_redesign_backbones" : null
        def backboneGateTrigger = mergedBackboneArtifacts.map { pdbFiles, jsonFiles ->
            (pdbFiles instanceof Collection ? pdbFiles.size() : 0) as Integer
        }
        OpenInteractiveGate(
            params.job_id ?: "unknown",
            "post_rfantibody",
            backboneGateTrigger,
            backboneGateDir ?: "",
            backboneGateDir ?: "",
            "",
            "protein_local_redesign",
            params.plr_design_chains ?: "",
            params.pred_method ?: "boltz"
        )
        return
    }

    def sequenceMethod = params.plr_seq_method ?: 'fampnn'

    def finalDesignPdbs
    def rawSequenceDir = null
    def filteredSequenceDir = null

    if (resumeFromSequences || resumeFromValidation) {
        finalDesignPdbs = loadProteinLocalPdbCollection(params.plr_sequence_input_pdbs ?: params.plr_validation_input_pdbs)
        rawSequenceDir = params.plr_sequence_input_pdbs ?: params.plr_validation_input_pdbs
    } else if (sequenceMethod == 'mpnn') {
        PrepProteinLocalMPNN(mergedBackboneArtifacts, manifestChannel)

        if ((params.mpnn_relax_max_cycles ?: 0) > 0) {
            PrepProteinLocalMPNN.out.pdbs
                .collect()
                .flatten()
                .buffer(size: 2, remainder: true)
                .set { mpnn_input_pdbs }
        }
        else {
            PrepProteinLocalMPNN.out.pdbs
                .collect()
                .flatten()
                .buffer(size: 10, remainder: true)
                .set { mpnn_input_pdbs }
        }

        RunMPNN(mpnn_input_pdbs)
        ExportProteinLocalMPNNResults(RunMPNN.out.pdbs_jsons)
        FilterMPNN(RunMPNN.out.pdbs_jsons)

        finalDesignPdbs = FilterMPNN.out.pdbs
            .flatten()
            .collect()
        rawSequenceDir = params.out_dir ? "${params.out_dir}/run/mpnn/results" : null
    }
    else {
        PrepProteinLocalFAMPNN(mergedBackboneArtifacts, manifestChannel)

        PrepProteinLocalFAMPNN.out.csv
            .collectFile(name: 'merged_protein_local_redesign.csv', keepHeader: true)
            .set { megaCsv }

        PrepProteinLocalFAMPNN.out.pdbs
            .map { pdbs -> [0, pdbs] }
            .set { fampnnPdbs }

        def defaultGpu = params.pinned_gpus ? params.pinned_gpus.toString().split(',')[0].trim().toInteger() : (params.gpu_id ?: 0)
        fampnnPdbs
            .combine(megaCsv)
            .map { batch_id, pdbs, csv -> [batch_id, FampnnAnalysisPolicy.stagePrepared(params, pdbs), csv, defaultGpu] }
            .set { fampnnInput }

        RunFAMPNN(fampnnInput, params.plr_design_chains, FampnnAnalysisPolicy.forWorkflow(params, 'protein_local_redesign', 'sequence_redesign_positions_spec'))
        FilterFAMPNN(RunFAMPNN.out.pdbs_jsons)

        finalDesignPdbs = FilterFAMPNN.out.pdbs
            .flatten()
            .collect()
        rawSequenceDir = params.out_dir ? "${params.out_dir}/run/fampnn/results" : null
        filteredSequenceDir = params.out_dir ? "${params.out_dir}/collected/fampnn_filtered" : null
    }

    def shouldPauseAfterSequenceDesign = interactiveGateEnabled &&
        (params.interactive_gate_stage ?: 'post_structure_validation') == 'post_fampnn' &&
        params.interactive_gate_continue != true &&
        !resumeFromValidation

    if (shouldPauseAfterSequenceDesign) {
        def sequenceGateDir = filteredSequenceDir ?: rawSequenceDir
        def sequenceGateTrigger = finalDesignPdbs.map { pdbs ->
            (pdbs instanceof Collection ? pdbs.size() : 0) as Integer
        }
        OpenInteractiveGate(
            params.job_id ?: "unknown",
            "post_fampnn",
            sequenceGateTrigger,
            sequenceGateDir ?: "",
            rawSequenceDir ?: "",
            filteredSequenceDir ?: "",
            "protein_local_redesign",
            params.plr_design_chains ?: "",
            params.pred_method ?: "boltz"
        )
        return
    }

    def selectedValidators = parseProteinLocalValidators(params.plr_structure_validators)

    if (!resumeFromValidation) {
        def validatorSummaryChannels = []
        def expectedCandidateCount = finalDesignPdbs.map { pdbs -> (pdbs instanceof Collection ? pdbs.size() : 0) as Integer }

        if (selectedValidators.contains('boltz2')) {
            PrepBoltz(finalDesignPdbs)
            PrepBoltz.out.yamls
                .collect()
                .map { allPdbs -> partitionProteinLocalGpuBatches(allPdbs, params.gpus) }
                .flatten()
                .groupTuple()
                .set { boltzInput }
            RunBoltz(boltzInput)
            ExportProteinLocalBoltzPredictions(RunBoltz.out.pdbs_jsons)
            validatorSummaryChannels << RunBoltz.out.completion
                .map { completionReceipt ->
                    def payload = new groovy.json.JsonSlurper().parse(completionReceipt.toFile())
                    (payload.completed_candidates instanceof Collection ? payload.completed_candidates.size() : 0) as Integer
                }
                .collect()
                .map { completedCounts -> [validator: 'boltz2', completed_candidates: completedCounts.sum(0) as Integer] }
                .ifEmpty { [validator: 'boltz2', completed_candidates: 0] }
        }

        def needsTypedInputs = selectedValidators.any { it in ['esmfold2', 'protenix_v2'] }
        if (needsTypedInputs) {
            finalDesignPdbs
                .flatten()
                .map { pdb ->
                    def normalized = pdb.baseName.replaceAll(/[^A-Za-z0-9._-]+/, '_')
                    [[candidate_id: normalized, source_file: pdb.name], pdb]
                }
                .set { validatorCandidateInputs }
            PrepareProteinLocalValidatorInput(validatorCandidateInputs)
        }

        if (selectedValidators.contains('esmfold2')) {
            PrepareProteinLocalValidatorInput.out.prepared
                .map { producerMeta, sourcePdb, contract, protenixJson ->
                    [producerMeta, sourcePdb, producerMeta.candidate_id]
                }
                .set { esmfold2ValidatorInputs }
            ESMFold2FromPdb(esmfold2ValidatorInputs)
            validatorSummaryChannels << ESMFold2FromPdb.out.typed_results
                .map { producerMeta, validator, cifs, metrics -> producerMeta.candidate_id }
                .collect()
                .map { candidates -> [validator: 'esmfold2', completed_candidates: candidates.size()] }
                .ifEmpty { [validator: 'esmfold2', completed_candidates: 0] }
        }

        if (selectedValidators.contains('protenix_v2')) {
            PrepareProteinLocalValidatorInput.out.prepared
                .map { producerMeta, sourcePdb, contract, protenixJson ->
                    tuple(producerMeta, protenixJson, params.protenix_prepared_msa_dir ? file(params.protenix_prepared_msa_dir, checkIfExists: true) : [])
                }
                .set { protenixValidatorInputs }
            ProtenixFromComplex(protenixValidatorInputs)
            validatorSummaryChannels << ProtenixFromComplex.out.canonical_structures
                .map { producerMeta, producerCandidates, cifs -> producerMeta.candidate_id }
                .collect()
                .map { candidates -> [validator: 'protenix_v2', completed_candidates: candidates.size()] }
                .ifEmpty { [validator: 'protenix_v2', completed_candidates: 0] }
        }

        def mergedValidatorSummaries = validatorSummaryChannels[0]
        if (validatorSummaryChannels.size() > 1) {
            mergedValidatorSummaries = mergedValidatorSummaries.mix(validatorSummaryChannels[1])
        }
        if (validatorSummaryChannels.size() > 2) {
            mergedValidatorSummaries = mergedValidatorSummaries.mix(validatorSummaryChannels[2])
        }
        FinalizeProteinLocalValidatorSuite(
            Channel.value(selectedValidators),
            expectedCandidateCount,
            mergedValidatorSummaries.collect()
        )
        EnforceProteinLocalValidatorSuite(
            FinalizeProteinLocalValidatorSuite.out.receipt
        )
        StageProteinLocalValidatedCandidates(
            finalDesignPdbs,
            FinalizeProteinLocalValidatorSuite.out.receipt,
            EnforceProteinLocalValidatorSuite.out.complete
        )

        def shouldPauseAfterValidation = interactiveGateEnabled &&
            (params.interactive_gate_stage ?: 'post_structure_validation') == 'post_structure_validation' &&
            params.interactive_gate_continue != true

        if (shouldPauseAfterValidation) {
            def validationGateDir = params.out_dir ? "${params.out_dir}/validation" : null
            def validationReviewDir = params.out_dir ? "${params.out_dir}/validation/review_candidates" : null
            OpenInteractiveGate(
                params.job_id ?: "unknown",
                "post_structure_validation",
                StageProteinLocalValidatedCandidates.out.candidates.map { 1 },
                validationReviewDir ?: "",
                validationGateDir ?: "",
                "",
                "protein_local_redesign",
                params.plr_design_chains ?: "",
                selectedValidators.join(',')
            )
            return
        }
    }
}

workflow {
    println("Running resequence and validation of existing designs")
    println("* Design chain: ${params.plr_design_chains ?: 'not set'}")
    println("* Sequence method: ${params.plr_seq_method ?: 'fampnn'}")
    println("* Structure validators: ${parseProteinLocalValidators(params.plr_structure_validators).join(',')}")

    PROTEIN_LOCAL_REDESIGN()
}
