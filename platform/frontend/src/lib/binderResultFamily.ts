import { api, type Job } from './api';

const id = (value: unknown): string | null => typeof value === 'string' && value.length > 0 ? value : null;
export function familyReferences(job: Job) {
    const params = job.params ?? {};
    return {
        root: id(job.lineage_root_job_id) ?? id(params.lineage_root_job_id) ?? id(params.iteration_source_root_job_id),
        source: id(job.selection_source_job_id) ?? id(job.source_stage_job_id)
            ?? id(params.selection_source_job_id) ?? id(params.source_stage_job_id) ?? id(params.iteration_source_job_id),
        scheduler: id(job.parent_job_id),
    };
}

export function familyRoot(job: Job, jobs: Job[]): string {
    const byId = new Map(jobs.map(row => [row.id, row]));
    const visited = new Set<string>();
    let current = job;
    while (!visited.has(current.id)) {
        visited.add(current.id);
        const ref = familyReferences(current);
        const next = ref.root ?? ref.source ?? ref.scheduler;
        if (!next || next === current.id) return current.id;
        const owner = byId.get(next);
        if (!owner) return next;
        current = owner;
    }
    return current.id;
}

export async function fetchBinderResultFamily(jobId: string, signal?: AbortSignal): Promise<Job[]> {
    const jobs = new Map<string, Job>();
    let offset = 0;
    let total = Infinity;
    while (offset < total) {
        const { data } = await api.get<{ jobs: Job[]; total: number }>('/api/jobs', {
            params: { scientific_family_job_id: jobId, include_children: true, summary: false, limit: 100, offset },
            signal, timeout: 10_000,
        });
        total = data.total;
        if (!data.jobs.length && offset < total) throw new Error('Family listing changed; refresh to reload it.');
        data.jobs.forEach(job => jobs.set(job.id, job));
        offset += data.jobs.length;
    }
    if (jobs.size !== total) throw new Error('Family listing changed; refresh to reload it.');
    return [...jobs.values()];
}
