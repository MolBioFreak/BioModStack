import { useQuery } from '@tanstack/react-query';
import { api, type Job } from './api';
import { getProject } from './projectManager';
import { fetchBinderResultFamily } from './binderResultFamily';
import { fetchBC2Page, object } from './bindcraft2Results';

const text = (v: unknown): string => typeof v === 'string' ? v : '';
const rows = (v: unknown): Record<string, unknown>[] => Array.isArray(v) ? v.map(object) : [];
const step = (j: Job) => object(j.provenance?.binder_round_step);
export type ValidationKind = 'monomer' | 'trimer1' | 'trimer3' | 'other';
export interface CandidateSource { id: string; label: string; job: Job; draws: Record<string, unknown>[] }
export interface CandidateAttempt { id: string; status: Job['status']; model: string; href: string; kind: ValidationKind; retryOf: string; parentId: string }
export interface ProjectCandidate { key: string; sourceId: string; sourceLabel: string; nativeId: string; family: string; familyLabel: string; variant: string; length: number | null; href: string; attempts: CandidateAttempt[] }
export interface ProjectCandidateData { supported: boolean; sources: CandidateSource[]; candidates: ProjectCandidate[]; unlinkedJobCount: number; errors: string[] }
export function candidateHref(sourceId: string, nativeId: string, arm: unknown = null): string {
    return `/designs/${encodeURIComponent(sourceId)}?${new URLSearchParams({ native_scope: JSON.stringify(['bindcraft2', arm, 'draw']), native_candidate: JSON.stringify([arm, 'draw', nativeId]) })}`;
}
export function projectSourceReferences(project: unknown): { id: string; label: string }[] {
    const found = new Map<string, { id: string; label: string }>();
    for (const ref of rows(object(object(project).payload).external_references)) {
        const match = /^\/designs\/([a-zA-Z0-9-]+)$/.exec(text(ref.value));
        if (match) found.set(match[1], { id: match[1], label: text(ref.label) });
    }
    return [...found.values()];
}
// Role evidence is sequence-bound, never chain-letter, length or title heuristics.
export function validationKind(job: Job, binder: string, targets: Set<string>): ValidationKind {
    const explicit = rows(step(job).input_components);
    const components = explicit.length ? explicit : rows(job.params?.complex_components);
    if (!components.length) return 'other';
    let b = 0, t = 0;
    const targetSequences = new Set<string>();
    for (const component of components) {
        const sequence = text(component.sequence);
        const role = explicit.length ? text(component.role) : sequence === binder && !targets.has(sequence) ? 'binder' : targets.has(sequence) && sequence !== binder ? 'target' : '';
        const count = component.count ?? component.copies ?? 1;
        if (!sequence || typeof count !== 'number' || !Number.isInteger(count) || count < 1) return 'other';
        if (role === 'binder' && sequence === binder) b += count;
        else if (role === 'target') { t += count; targetSequences.add(sequence); }
        else return 'other';
    }
    if (targetSequences.size !== 1) return 'other';
    return b === 1 && t === 1 ? 'monomer' : b === 1 && t === 3 ? 'trimer1' : b === 3 && t === 3 ? 'trimer3' : 'other';
}
export function buildProjectCandidateData(sources: CandidateSource[], jobs: Job[], errors: string[] = []): ProjectCandidateData {
    const byId = new Map(jobs.map(j => [j.id, j]));
    const candidates = new Map<string, ProjectCandidate>();
    const linked = new Set<string>();
    for (const source of sources) {
        const drawById = new Map(source.draws.map(d => [text(d.design), d]));
        const targets = new Set<string>();
        for (const job of jobs) {
            const s = step(job), ref = object(s.source_ref);
            if ((ref.job_id ?? s.source_job_id) === source.id) for (const c of rows(s.input_components)) {
                if (c.role === 'target' && text(c.sequence)) targets.add(text(c.sequence));
            }
        }
        const identity = (job: Job, visited = new Set<string>()): string => {
            if (visited.has(job.id)) return '';
            visited.add(job.id);
            const s = step(job), ref = object(s.source_ref);
            const sid = text(ref.job_id) || text(s.source_job_id) || job.selection_source_job_id || job.source_stage_job_id;
            if (sid === source.id) {
                if (text(ref.native_record_id)) return text(ref.native_record_id);
                // Older selected-native-sequence jobs retain exact sequence_name, not a round step.
                // Accept it only against this source's real draw inventory below.
                if (job.params?.selection_source_type === 'selected_native_sequences') {
                    const name = text(job.params.sequence_name), draw = drawById.get(name);
                    const sequence = text(draw?.sequence) || text(object(draw?.values).Binder_Sequence);
                    if (sequence && rows(job.params.complex_components).some(c => c.sequence === sequence)) return name;
                }
            }
            const retry = byId.get(text(s.retry_of_job_id));
            return retry ? identity(retry, visited) : '';
        };
        for (const job of jobs) {
            if (job.id === source.id) continue;
            const nativeId = identity(job), draw = drawById.get(nativeId);
            if (!draw) continue;
            const binder = text(draw.sequence) || text(object(draw.values).Binder_Sequence);
            const key = JSON.stringify([source.id, nativeId]);
            let candidate = candidates.get(key);
            if (!candidate) {
                const match = /^design_l\d+_([a-f0-9]+)_candidate(\d+)$/.exec(nativeId);
                const family = text(draw.trajectory_design) || nativeId;
                candidate = { key, sourceId: source.id, sourceLabel: source.label, nativeId, family,
                    familyLabel: match ? `Family ${match[1].slice(0, 8)}` : family,
                    variant: match ? `Variant ${match[2]}` : 'Native candidate', length: binder.length || null,
                    href: candidateHref(source.id, nativeId, draw.arm ?? null), attempts: [] };
                candidates.set(key, candidate);
            }
            linked.add(job.id);
            candidate.attempts.push({ id: job.id, status: job.status, model: job.model_id,
                href: `/${job.status === 'completed' ? 'designs' : 'jobs'}/${encodeURIComponent(job.id)}${job.status === 'completed' ? '?include_children=false' : ''}`,
                kind: validationKind(job, binder, targets), retryOf: text(step(job).retry_of_job_id), parentId: job.parent_job_id || '' });
        }
    }
    const natural = new Intl.Collator(undefined, { numeric: true });
    const sorted = [...candidates.values()].sort((a, b) => natural.compare(a.sourceLabel, b.sourceLabel) || natural.compare(a.family, b.family) || natural.compare(a.variant, b.variant));
    return { supported: sources.length > 0, sources, candidates: sorted, errors,
        unlinkedJobCount: [...byId.values()].filter(j => !linked.has(j.id) && !sources.some(s => s.id === j.id)).length };
}
export async function fetchProjectCandidateData(projectId: string, signal?: AbortSignal): Promise<ProjectCandidateData> {
    const project = await getProject(projectId, signal);
    const sources: CandidateSource[] = [], jobs = new Map<string, Job>(), errors: string[] = [];
    for (const ref of projectSourceReferences(project)) {
        let job: Job;
        try {
            job = (await api.get<Job>(`/api/jobs/${encodeURIComponent(ref.id)}`, { signal })).data;
        } catch (error) {
            if (signal?.aborted) throw error;
            errors.push(`${ref.label || 'Referenced source'}: native Job metadata unavailable`);
            continue;
        }
        if (job.model_id !== 'bindcraft2') continue;
        const source: CandidateSource = { ...ref, label: ref.label || `BC2 source ${sources.length + 1}`, job, draws: [] };
        sources.push(source);
        try {
            const family = await fetchBinderResultFamily(job.id, signal);
            family.forEach(j => jobs.set(j.id, j));
            let total = Infinity;
            while (source.draws.length < total) {
                const page = await fetchBC2Page(job.id, null, 'draw', source.draws.length, signal);
                total = page.total;
                if (!page.rows.length && source.draws.length < total) throw new Error('Native candidate inventory changed; refresh.');
                source.draws.push(...page.rows);
            }
        } catch (error) {
            if (signal?.aborted) throw error;
            errors.push(`${source.label}: ${error instanceof Error ? error.message : 'Read unavailable'}`);
        }
    }
    if (!sources.length && errors.length) throw new Error(errors.join('; '));
    return buildProjectCandidateData(sources, [...jobs.values()], errors);
}
export function useProjectCandidateData(projectId: string, enabled: boolean) {
    return useQuery({ queryKey: ['project-candidate-overview', projectId], queryFn: ({ signal }) => fetchProjectCandidateData(projectId, signal),
        enabled: enabled && Boolean(projectId), retry: false, staleTime: Infinity, refetchInterval: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
}
