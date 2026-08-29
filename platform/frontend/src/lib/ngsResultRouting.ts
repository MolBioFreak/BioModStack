export interface NgsJobRouteIdentity {
    id: string;
    model_id?: string | null;
    mode?: string | null;
    params?: Record<string, unknown> | null;
}

export type NgsToolkitView = 'launch' | 'instrument' | 'runs';

const NGS_MODEL_IDS = new Set([
    'nanopore',
    'ont_basecall_dna',
    'ont_basecall_rna',
    'ont_plasmid_qc',
    'ont_construct_screening',
    'ont_methylation_analysis',
    'ont_fastq_qc',
    'ont_pooled_reference_assignment',
    'wf_clone_validation',
]);

const NGS_WORKFLOW_IDS = new Set([
    'ont_basecall_dna',
    'ont_basecall_rna',
    'ont_plasmid_qc',
    'ont_construct_screening',
    'ont_methylation_analysis',
    'ont_fastq_qc',
    'ont_pooled_reference_assignment',
    'wf_clone_validation',
    'basecall_dna',
    'basecall_rna',
    'plasmid_qc',
    'construct_screening',
    'methylation_analysis',
    'fastq_qc',
    'pooled_reference_assignment',
    'pooled-reference-assignment',
    'wf_clone',
    'clone_validation',
]);

const LEGACY_NGS_MODES = new Set([
    ...NGS_WORKFLOW_IDS,
    'nanopore_methylation',
]);

function routeIdentity(value: unknown): string {
    return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

export function isNgsJob(job: Pick<NgsJobRouteIdentity, 'model_id' | 'mode' | 'params'>): boolean {
    const modelId = routeIdentity(job.model_id);
    if (NGS_MODEL_IDS.has(modelId)) return true;
    if (modelId) return false;

    const params = job.params && typeof job.params === 'object' ? job.params : {};
    const workflowId = routeIdentity(params.ont_workflow_id || params.workflow_id);
    if (NGS_WORKFLOW_IDS.has(workflowId)) return true;

    return LEGACY_NGS_MODES.has(routeIdentity(job.mode));
}

export function ngsResultHref(jobId: string, currentSearch = ''): string {
    if (!currentSearch) {
        return `/ngs?section=analyses&job_id=${encodeURIComponent(jobId)}`;
    }
    const params = new URLSearchParams(currentSearch);
    params.set('section', 'analyses');
    params.set('job_id', jobId);
    return `/ngs?${params.toString()}`;
}

export function ngsToolkitViewFromSearch(search: string): NgsToolkitView {
    const params = new URLSearchParams(search);
    const section = (params.get('section') || '').trim().toLowerCase();
    if (section === 'analyses' || section === 'evidence') return 'runs';
    if ((params.get('view') || '').trim().toLowerCase() === 'workbench') return 'runs';
    if (section === 'instrument') return 'instrument';
    if ((params.get('job_id') || '').trim()) return 'runs';
    return 'launch';
}

export function ngsToolkitSearchForView(search: string, view: NgsToolkitView): string {
    const params = new URLSearchParams(search);
    if (view === 'runs') {
        params.set('section', 'analyses');
    } else {
        params.delete('job_id');
        params.delete('view');
        params.delete('viewer_session_id');
        if (view === 'instrument') params.set('section', 'instrument');
        else params.delete('section');
    }
    const value = params.toString();
    return value ? `?${value}` : '';
}

export function ngsJobShouldPoll(status: string | null | undefined): boolean {
    return ['pending', 'queued', 'running', 'starting', 'cancelling', 'canceling'].includes(
        (status || '').trim().toLowerCase(),
    );
}
