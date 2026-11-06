export type WorkflowKey =
    | 'clone' | 'plasmidQc' | 'constructScreening' | 'fastqQc'
    | 'dna' | 'rna' | 'modified' | 'pooledAssignment';

/** Exact canonical ONT workflow id (ont_ngs_contract.CANONICAL_ONT_WORKFLOWS) for each form key. */
export const ONT_WORKFLOW_ID_BY_KEY: Readonly<Record<WorkflowKey, string>> = {
    dna: 'ont_basecall_dna',
    rna: 'ont_basecall_rna',
    plasmidQc: 'ont_plasmid_qc',
    constructScreening: 'ont_construct_screening',
    modified: 'ont_methylation_analysis',
    clone: 'wf_clone_validation',
    fastqQc: 'ont_fastq_qc',
    pooledAssignment: 'ont_pooled_reference_assignment',
};

const WORKFLOW_KEY_BY_ONT_ID: Readonly<Record<string, WorkflowKey>> = Object.fromEntries(
    Object.entries(ONT_WORKFLOW_ID_BY_KEY).map(([key, id]) => [id, key as WorkflowKey]),
);

export function isNanoporeWorkflowKey(value: unknown): value is WorkflowKey {
    return typeof value === 'string' && Object.prototype.hasOwnProperty.call(ONT_WORKFLOW_ID_BY_KEY, value);
}

/** Resolve a workflow only by its exact canonical id; unknown ids return null and are never guessed. */
export function workflowKeyForOntWorkflowId(workflowId: unknown): WorkflowKey | null {
    if (typeof workflowId !== 'string') return null;
    return Object.prototype.hasOwnProperty.call(WORKFLOW_KEY_BY_ONT_ID, workflowId)
        ? WORKFLOW_KEY_BY_ONT_ID[workflowId]
        : null;
}
