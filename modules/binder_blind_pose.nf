// Optional, selected-candidate blind pose leaf. Candidate PDBs are sequence
// sources only; the native runner receives components.json, never the PDBs.
process BinderBlindPoseESMFold2 {
    label 'ESMFold2'
    label 'gpu'
    container "${params.container_dir}/esmfold2.sif"

    input:
    path selection_manifest
    path candidate_pdbs
    path target_pdb

    output:
    path 'blind_pose_results', emit: results

    script:
    def codeRoot = params.code_root ?: workflow.projectDir
    def variant = params.esmfold2_validation_variant ?: params.esmf_model_variant ?: 'fast'
    def modelId = params.esmf_model_id_or_path ?: ''
    def loops = params.esmfold2_validation_num_loops ?: params.esmf_num_loops ?: 1
    def steps = params.esmfold2_validation_num_sampling_steps ?: params.esmf_num_sampling_steps ?: 25
    def samples = params.esmfold2_validation_num_diffusion_samples ?: params.esmf_num_diffusion_samples ?: 1
    def seedArg = params.containsKey('esmf_seed') && params.esmf_seed != null ? "--seed ${params.esmf_seed}" : ''
    """
    set -euo pipefail
    mkdir -p staged_candidates
    cp -- ${candidate_pdbs} staged_candidates/
    python3 ${codeRoot}/scripts/run_binder_blind_pose.py \\
        --selection-manifest ${selection_manifest} \\
        --candidate-dir staged_candidates \\
        --target-pdb ${target_pdb} \\
        --output-dir blind_pose_results \\
        --model-variant '${variant}' \\
        --model-id-or-path '${modelId}' \\
        --num-loops ${loops} \\
        --num-sampling-steps ${steps} \\
        --num-diffusion-samples ${samples} \\
        ${seedArg} --device cuda
    """
}
