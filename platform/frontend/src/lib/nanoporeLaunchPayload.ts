import type { WorkflowKey } from '../components/ngs/NanoporeWorkflowChooser';

export type NanoporeOperatorStageSettings = {
    selectedWorkflow: WorkflowKey;
    inputSource: 'pod5' | 'bam' | 'fastq';
    runFastqQc: boolean;
    runAssembly: boolean;
};

export function buildNanoporeOperatorStageParams({
    selectedWorkflow,
    inputSource,
    runFastqQc,
    runAssembly,
}: NanoporeOperatorStageSettings): Record<string, boolean> {
    const params: Record<string, boolean> = {};
    const fastqQcApplies = selectedWorkflow === 'plasmidQc'
        || selectedWorkflow === 'bamQc'
        || selectedWorkflow === 'fastqQc'
        || ((selectedWorkflow === 'clone' || selectedWorkflow === 'constructScreening') && inputSource === 'fastq');
    if (fastqQcApplies) params.run_fastq_qc = runFastqQc;
    if (selectedWorkflow === 'clone') params.run_assembly = true;
    if (selectedWorkflow === 'constructScreening') params.run_assembly = runAssembly;
    return params;
}
