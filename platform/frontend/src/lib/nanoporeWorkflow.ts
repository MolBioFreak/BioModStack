// Older saved forms used separate keys for options of an existing workflow.
export type WorkflowKey =
    | 'clone' | 'plasmidQc' | 'constructScreening' | 'fastqQc' | 'bamQc'
    | 'dna' | 'rna' | 'duplex' | 'modified' | 'barcode' | 'pooledAssignment';

export function normalizeNanoporeWorkflowChoice(key: WorkflowKey): WorkflowKey {
    if (key === 'duplex' || key === 'barcode') return 'dna';
    if (key === 'bamQc') return 'plasmidQc';
    return key;
}
