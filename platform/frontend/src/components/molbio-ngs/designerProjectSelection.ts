// A project selection browses existing work. It is not a new experiment binding.
export function designerProjectSelection(projectId: string | null, allSequences = false) {
    return {
        project_id: projectId, project_view: projectId ? 'work' : null,
        shelf: !projectId && allSequences ? 'all' : null,
        workspace_id: null, global_experiment_id: null, domain_experiment_id: null, state_revision_id: null,
        molbio_sequence_id: null, molbio_revision_id: null, sequence_id: null, revision_id: null,
        molbio_operation_id: null, operation_id: null, receipt_id: null,
        pcr_experiment_id: null, pcr_revision_id: null, action: null,
    };
}
