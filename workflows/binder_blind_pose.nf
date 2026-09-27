nextflow.enable.dsl=2

include { BinderBlindPoseESMFold2 } from '../modules/binder_blind_pose'

/*
 * Explicit selected-candidate assessment route. The request supplies immutable
 * candidate artifacts, a v1 selection manifest, and an independent declared
 * target PDB. Neither input PDB is passed to ESMFold2 as geometry: the adapter
 * extracts only named chain sequences into native complex components.
 *
 * Parent API owns selection/authorization, saved typed settings, local/remote
 * placement and model-owned result publication. No implicit validation stage
 * or binary acceptance decision is introduced here.
 */
workflow {
    if (!params.containsKey('blind_pose_selection_manifest') || !params.blind_pose_selection_manifest ||
        !params.containsKey('blind_pose_candidate_pdbs') || !params.blind_pose_candidate_pdbs ||
        !params.containsKey('target_pdb') || !params.target_pdb) {
        error 'Blind pose route requires selection manifest, selected candidate PDBs and declared target PDB'
    }
    def candidatePaths = params.blind_pose_candidate_pdbs instanceof List
        ? params.blind_pose_candidate_pdbs
        : params.blind_pose_candidate_pdbs.toString().split(',').collect { it.trim() }.findAll { it }
    BinderBlindPoseESMFold2(
        Channel.value(file(params.blind_pose_selection_manifest)),
        Channel.value(candidatePaths.collect { file(it) }),
        Channel.value(file(params.target_pdb))
    )
}
