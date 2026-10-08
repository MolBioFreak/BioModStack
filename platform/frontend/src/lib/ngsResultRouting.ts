export interface NgsJobRouteIdentity {
    id: string;
    model_id?: string | null;
    mode?: string | null;
}

export type NgsToolkitView = 'launch' | 'instrument' | 'runs';

export function isNgsJob(job: Pick<NgsJobRouteIdentity, 'model_id' | 'mode'>): boolean {
    const modelId = (job.model_id || '').toLowerCase();
    const mode = (job.mode || '').toLowerCase();
    return (
        modelId === 'nanopore'
        || modelId.includes('nanopore')
        || mode === 'methylation_analysis'
        || mode === 'nanopore_methylation'
    );
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
    const section = (new URLSearchParams(search).get('section') || '').trim().toLowerCase();
    if (section === 'analyses' || section === 'evidence') return 'runs';
    if (section === 'instrument') return 'instrument';
    return 'launch';
}

export function ngsToolkitSearchForView(search: string, view: NgsToolkitView): string {
    const params = new URLSearchParams(search);
    if (view === 'runs') {
        params.set('section', 'analyses');
    } else {
        params.delete('job_id');
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
