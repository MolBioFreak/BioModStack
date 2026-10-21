import { api, type Job } from './api';
import { fetchBC2Navigation, bc2TrajectoryNames, object } from './bindcraft2Results';
import { uniqueBinderComplex, type NativeGenerationDocument } from './nativeBinderResults';

/** Navigation transport only; full Jobs remain structurally compatible inputs. */
export interface BinderNavigationJob extends Pick<Job, 'id' | 'name' | 'model_id' | 'status' | 'created_at' | 'mode' | 'stage_family' | 'parent_job_id' | 'lineage_root_job_id' | 'selection_source_job_id' | 'source_stage_job_id'> {
    params?: unknown;
    provenance?: unknown;
}

const id = (value: unknown): string | null => typeof value === 'string' && value.length > 0 ? value : null;
export function familyReferences(job: BinderNavigationJob) {
    const params = object(job.params);
    return {
        root: id(job.lineage_root_job_id) ?? id(params.lineage_root_job_id) ?? id(params.iteration_source_root_job_id),
        source: id(job.selection_source_job_id) ?? id(job.source_stage_job_id)
            ?? id(params.selection_source_job_id) ?? id(params.source_stage_job_id) ?? id(params.iteration_source_job_id),
        scheduler: id(job.parent_job_id),
    };
}

export function familyRoot(job: BinderNavigationJob, jobs: BinderNavigationJob[]): string {
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

export function fetchBinderResultFamily(jobId: string, signal?: AbortSignal): Promise<Job[]> {
    return fetchFamily<Job>(jobId, false, signal);
}
// Never store this reduced shape under the full binder-result-family query key.
export function fetchBinderNavigationFamily(jobId: string, signal?: AbortSignal): Promise<BinderNavigationJob[]> {
    return fetchFamily<BinderNavigationJob>(jobId, true, signal);
}
async function fetchFamily<T extends BinderNavigationJob>(jobId: string, compact: boolean, signal?: AbortSignal): Promise<T[]> {
    const jobs = new Map<string, T>();
    let offset = 0;
    let total = Infinity;
    while (offset < total) {
        const { data } = await api.get<{ jobs: T[]; total: number }>('/api/jobs', {
            params: { scientific_family_job_id: jobId, include_children: true, ...(compact ? { projection: 'binder_navigation' } : { summary: false }), limit: 100, offset },
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

const text = (v: unknown): string => typeof v === 'string' ? v : '';
const rows = (v: unknown): Record<string, unknown>[] => Array.isArray(v) ? v.map(object) : [];
const step = (j: BinderNavigationJob) => object(object(j.provenance).binder_round_step);
export type ValidationKind = 'monomer' | 'trimer1' | 'trimer3' | 'other';
export interface CandidateSource { id: string; label: string; job: BinderNavigationJob; draws: Record<string, unknown>[]; trajectoryNames?: Record<string, string> }
export interface CandidateAttempt { id: string; status: Job['status']; model: string; href: string; kind: ValidationKind; retryOf: string; parentId: string; label?: string; createdAt?: string }
export interface ProjectCandidate { key: string; sourceId: string; sourceLabel: string; nativeId: string; family: string; familyLabel: string; variant: string; length: number | null; href: string; backboneHref?: string; attempts: CandidateAttempt[] }
export interface ProjectCandidateData { supported: boolean; sources: CandidateSource[]; candidates: ProjectCandidate[]; unlinkedJobCount: number; errors: string[] }
export function candidateHref(sourceId: string, nativeId: string, arm: unknown = null, document?: NativeGenerationDocument): string {
    const params = new URLSearchParams({ native_scope: JSON.stringify(['bindcraft2', arm, 'draw']), native_candidate: JSON.stringify([arm, 'draw', nativeId]), view: 'structure' });
    if (document) {
        params.set('artifact_id', document.artifact_id);
        if (document.target_state != null) params.set('target_state', document.target_state);
    }
    return `/designs/${encodeURIComponent(sourceId)}?${params}`;
}
// Role evidence is sequence-bound, never chain-letter, length or title heuristics.
export function validationKind(job: BinderNavigationJob, binder: string, targets: Set<string>): ValidationKind {
    const explicit = rows(step(job).input_components);
    const components = explicit.length ? explicit : rows(object(job.params).complex_components);
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
export function candidateRunLabel(job: BinderNavigationJob, binder: string, targets: Set<string>): string {
    const kind = validationKind(job, binder, targets);
    if (kind === 'monomer') return 'Monomer target · 1 binder';
    if (kind === 'trimer1') return 'Trimer target · 1 binder';
    if (kind === 'trimer3') return 'Trimer target · 3 binders';
    const stage = text(step(job).stage) || job.stage_family || job.mode;
    return stage ? stage.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase()) : 'Follow-up result';
}
export function buildProjectCandidateData(sources: CandidateSource[], jobs: BinderNavigationJob[], errors: string[] = []): ProjectCandidateData {
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
        const identity = (job: BinderNavigationJob, visited = new Set<string>()): string => {
            if (visited.has(job.id)) return '';
            visited.add(job.id);
            const s = step(job), ref = object(s.source_ref);
            const sid = text(ref.job_id) || text(s.source_job_id) || job.selection_source_job_id || job.source_stage_job_id;
            if (sid === source.id) {
                if (text(ref.native_record_id)) return text(ref.native_record_id);
                // Older selected-native-sequence jobs retain exact sequence_name, not a round step.
                // Accept it only against this source's real draw inventory below.
                if (object(job.params).selection_source_type === 'selected_native_sequences') {
                    const name = text(object(job.params).sequence_name), draw = drawById.get(name);
                    const sequence = text(draw?.sequence) || text(object(draw?.values).Binder_Sequence);
                    if (sequence && rows(object(job.params).complex_components).some(c => c.sequence === sequence)) return name;
                }
            }
            const retry = byId.get(text(s.retry_of_job_id));
            if (retry) return identity(retry, visited);
            // Follow explicit scientific source links, never an execution parent or a shared root.
            // A source run is inherited only after it is itself bound to one exact candidate.
            const parentSource = byId.get(sid || text(object(job.params).selection_source_job_id) || text(object(job.params).iteration_source_job_id));
            return parentSource && parentSource.id !== source.id ? identity(parentSource, visited) : '';
        };
        for (const job of jobs) {
            if (job.id === source.id) continue;
            const nativeId = identity(job), draw = drawById.get(nativeId);
            if (!nativeId || !draw) continue;
            const binder = text(draw.sequence) || text(object(draw.values).Binder_Sequence);
            const key = JSON.stringify([source.id, nativeId]);
            let candidate = candidates.get(key);
            if (!candidate) {
                const match = /^design_l\d+_([a-f0-9]+)_candidate(\d+)$/.exec(nativeId);
                const family = text(draw.trajectory_design) || nativeId;
                candidate = { key, sourceId: source.id, sourceLabel: source.label, nativeId, family,
                    familyLabel: source.trajectoryNames?.[family] ?? 'Candidate',
                    variant: match ? `Variant ${match[2]}` : 'Native candidate', length: binder.length || null,
                    backboneHref: `/designs/${encodeURIComponent(source.id)}?${new URLSearchParams({ native_scope: JSON.stringify(['bindcraft2', draw.arm ?? null, 'trajectory']), native_candidate: JSON.stringify([draw.arm ?? null, 'trajectory', family]), view: 'structure' })}`,
                    href: candidateHref(source.id, nativeId, draw.arm ?? null, uniqueBinderComplex((Array.isArray(draw.structures) ? draw.structures : []) as NativeGenerationDocument[])), attempts: [] };
                candidates.set(key, candidate);
            }
            linked.add(job.id);
            candidate.attempts.push({ id: job.id, status: job.status, model: job.model_id,
                href: `/${job.status === 'completed' ? 'designs' : 'jobs'}/${encodeURIComponent(job.id)}${job.status === 'completed' ? '?include_children=false&view=structure' : ''}`,
                kind: validationKind(job, binder, targets), label: candidateRunLabel(job, binder, targets), createdAt: job.created_at, retryOf: text(step(job).retry_of_job_id), parentId: job.parent_job_id || '' });
        }
    }
    const natural = new Intl.Collator(undefined, { numeric: true });
    const sorted = [...candidates.values()].sort((a, b) => natural.compare(a.sourceLabel, b.sourceLabel) || natural.compare(a.familyLabel, b.familyLabel) || natural.compare(a.family, b.family) || natural.compare(a.variant, b.variant));
    return { supported: sources.length > 0, sources, candidates: sorted, errors,
        unlinkedJobCount: [...byId.values()].filter(j => !linked.has(j.id) && !sources.some(s => s.id === j.id)).length };
}

export async function fetchBinderCandidateData(references: { id: string; label: string }[], signal?: AbortSignal): Promise<ProjectCandidateData> {
    const sources: CandidateSource[] = [], jobs = new Map<string, BinderNavigationJob>(), errors: string[] = [];
    for (const ref of references) {
        let job: BinderNavigationJob;
        try {
            job = (await api.get<BinderNavigationJob>(`/api/jobs/${encodeURIComponent(ref.id)}`, { params: { projection: 'binder_navigation' }, signal })).data;
        } catch (error) {
            if (signal?.aborted) throw error;
            errors.push(`${ref.label || 'Referenced source'}: native Job metadata unavailable`);
            continue;
        }
        if (job.model_id !== 'bindcraft2') continue;
        const source: CandidateSource = { ...ref, label: ref.label || `BC2 source ${sources.length + 1}`, job, draws: [] };
        sources.push(source);
        try {
            const family = await fetchBinderNavigationFamily(job.id, signal);
            family.forEach(j => jobs.set(j.id, j));
            const navigation = await fetchBC2Navigation(job.id, null, signal);
            source.trajectoryNames = bc2TrajectoryNames(navigation.trajectories);
            source.draws = navigation.draws;
        } catch (error) {
            if (signal?.aborted) throw error;
            errors.push(`${source.label}: ${error instanceof Error ? error.message : 'Read unavailable'}`);
        }
    }
    if (!sources.length && errors.length) throw new Error(errors.join('; '));
    return buildProjectCandidateData(sources, [...jobs.values()], errors);
}
