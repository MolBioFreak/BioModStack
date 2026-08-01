nextflow.enable.dsl = 2

import groovy.json.JsonOutput

include { OpenMMRelaxation ; OpenMMScore } from './openmm'

def antibodyOpenMMStableProducerKey(rawValue, String field) {
    def value = rawValue?.toString()?.trim()
    if (!(value ==~ /[A-Za-z0-9][A-Za-z0-9._-]*/)) {
        throw new IllegalArgumentException("antibody_denovo:invalid_${field}")
    }
    return value
}

workflow AntibodyOpenMMRefinement {
    take:
    validated_structures

    main:
    log.info("Step 3.5: Running OpenMM physics refinement...")
    log.info("  Compute tier: ${params.openmm_compute_tier ?: 'fast'}")
    log.info("  CDR-only mode: ${params.openmm_cdr_only ?: true}")
    log.info("  Restraint mode: ${params.openmm_restraint_mode ?: 'framework'}")

    openmm_records = validated_structures.map { meta, pdb_or_pdbs ->
        def pdbs = pdb_or_pdbs instanceof Collection ? new ArrayList(pdb_or_pdbs as Collection) : [pdb_or_pdbs]
        if (pdbs.size() != 1 || !(meta instanceof Map)) {
            error('antibody_denovo:openmm_requires_one_producer_bound_structure_per_tuple')
        }
        def artifactAuthority = meta.producer_artifact_key ?: meta.producer_candidate_key ?: meta.artifact_key ?: meta.id
        def artifactKey = antibodyOpenMMStableProducerKey(artifactAuthority, 'openmm_input_artifact_key')
        def batchId = "openmm_${java.security.MessageDigest.getInstance('SHA-256').digest(JsonOutput.toJson(new TreeMap(meta as Map)).getBytes('UTF-8')).encodeHex().toString()[0..<24]}"
        tuple(batchId, new LinkedHashMap(meta as Map), pdbs[0])
    }
    openmm_batched = openmm_records.map { batch_id, meta, pdb -> tuple(batch_id, [pdb]) }
    openmm_lineage = openmm_records.map { batch_id, meta, pdb -> tuple(batch_id, meta) }

    OpenMMRelaxation(
        openmm_batched,
        params.openmm_compute_tier ?: 'fast',
        params.openmm_cdr_only ?: true,
        params.openmm_restraint_mode ?: 'framework',
        params.openmm_antibody_chain ?: 'H',
        params.openmm_force_field ?: 'amber14sb'
    )

    OpenMMRelaxation.out.relaxed_pdbs.subscribe { pdbs ->
        try {
            def file_list = pdbs instanceof List ? pdbs : [pdbs]
            def report_files = file_list.size() > 50 ? file_list[0..49] : file_list
            def args = [params.job_id, "openmm_relaxation", "complete"] + report_files.collect { it.toString() }
            def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
            proc.waitFor()
        } catch (Exception e) {
            println "Warning: Failed to report stage openmm_relaxation: ${e.message}"
        }
    }

    if (params.openmm_compute_tier == 'full' || params.openmm_mmgbsa_mode != 'off') {
        log.info("  Running MM-GBSA binding affinity scoring...")

        mmgbsa_batched = OpenMMRelaxation.out.relaxed_pdbs
            .collect()
            .flatten()
            .buffer(size: 5, remainder: true)
            .map { batch -> tuple("mmgbsa_${batch.hashCode()}", batch) }

        OpenMMScore(
            mmgbsa_batched,
            params.openmm_mmgbsa_mode ?: 'interface',
            params.openmm_binder_chains ?: 'H',
            params.openmm_target_chains ?: 'A',
            params.openmm_force_field ?: 'amber14sb'
        )

        OpenMMScore.out.scores_json.subscribe { jsons ->
            try {
                def args = [params.job_id, "openmm_mmgbsa", "complete"]
                def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
                proc.waitFor()
            } catch (Exception e) {
                println "Warning: Failed to report stage openmm_mmgbsa: ${e.message}"
            }
        }
    }

    refined_structures = OpenMMRelaxation.out.relaxed_with_batch
        .join(openmm_lineage)
        .flatMap { batch_id, relaxed_pdbs, source_meta ->
            def pdbs = relaxed_pdbs instanceof Collection ? new ArrayList(relaxed_pdbs as Collection) : [relaxed_pdbs]
            if (pdbs.size() != 1) {
                error("antibody_denovo:openmm_output_cardinality_mismatch:${batch_id}")
            }
            def sourceArtifact = antibodyOpenMMStableProducerKey(
                source_meta.producer_artifact_key ?: source_meta.producer_candidate_key ?: source_meta.artifact_key ?: source_meta.id,
                'openmm_source_artifact_key'
            )
            def openmmArtifact = antibodyOpenMMStableProducerKey("${sourceArtifact}.openmm", 'openmm_artifact_key')
            def lineage = source_meta.transformation_lineage instanceof Collection
                ? new ArrayList(source_meta.transformation_lineage as Collection)
                : []
            lineage << 'openmm_relaxation'
            def outputMeta = new LinkedHashMap(source_meta as Map)
            outputMeta.putAll([
                producer_artifact_key: openmmArtifact,
                producer_method: 'openmm',
                producer_sample: source_meta.producer_sample ?: sourceArtifact,
                producer_rank: source_meta.producer_rank,
                producer_output_key: "antibody_denovo/openmm/${openmmArtifact}.pdb",
                transformation_lineage: lineage,
            ])
            [tuple(outputMeta, pdbs[0])]
        }

    emit:
    refined_structures = refined_structures
}
