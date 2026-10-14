import { api, fetchJobById, type Job } from './api';
import { submitPreparedSelectedMutation } from './binderContinuation';
import type { ExecutionPolicy } from './executionPolicy';
import type { BinderRoundStage } from './binderRound';

/** Identity envelope only. Native publications remain the measurement authority. */
export type CandidateRef =
    | { kind: 'design'; job_id: string; design_id: string; document?: { artifact_id?: string; target_state?: string; model?: number } }
    | { kind: 'native_document'; job_id: string; artifact_id: string; target_state?: string; model?: number }
    | { kind: 'native_sequence'; job_id: string; publication_ref: string; native_output_id: string }
    | { kind: 'native_observation'; job_id: string; publication_ref: string; native_record_id: string };
export type StageInput = { selected: true } | { stage_id: string };
export type SelectedRoundStage = { id: string; input: StageInput } & (
    | { kind: 'sequence_design' | 'prediction'; models: BinderRoundStage[] }
    | { kind: 'maturation'; model_id: 'binder_refinement'; mode: 'refine'; params: BinderRoundStage['params'] });
export interface SelectedRoundRequest {
    operation: 'round'; source_job_id: string; idempotency_key: string;
    selection: { candidate_refs: CandidateRef[] } | { saved_set_id: string; revision: number };
    round_plan: { schema_version: 1; stages: SelectedRoundStage[] };
    execution_target_id: string | null; launch_context_id?: string | null; execution_policy?: ExecutionPolicy;
}
export interface SelectedRound {
    round_id: string; source_job_id: string; root_job_id: string; state: string;
    request: SelectedRoundRequest; selection: unknown;
    steps: Array<{ step_id: string; stage_id: string; model_id: string; state: string; job_id?: string;
        parent_step_ids: string[]; outputs: Array<CandidateRef & { label?: string; name?: string }>;
        request?: Partial<Job>; error?: string }>;
}
export interface SelectedRoundResponse {
    operation: 'round'; source_job_id: string; root_job_id: string; round_id: string; state: string;
    selected_design_count: number; selected_native_count: number; selected_sequence_count: number;
    launched_jobs: Job[]; round: SelectedRound;
}
export const candidateKey = (ref: CandidateRef) => JSON.stringify(ref.kind === 'design'
    ? [ref.kind, ref.job_id, ref.design_id, ref.document?.artifact_id, ref.document?.target_state, ref.document?.model]
    : ref.kind === 'native_document' ? [ref.kind, ref.job_id, ref.artifact_id, ref.target_state, ref.model]
    : ref.kind === 'native_sequence' ? [ref.kind, ref.job_id, ref.publication_ref, ref.native_output_id]
    : [ref.kind, ref.job_id, ref.publication_ref, ref.native_record_id]);
/** Drop optional display metadata before strict CandidateRef transport. */
export function candidateIdentity(ref: CandidateRef): CandidateRef {
    const base = { kind: ref.kind, job_id: ref.job_id };
    if (ref.kind === 'design') return { ...base, kind: ref.kind, design_id: ref.design_id, ...(ref.document ? { document: { ...(ref.document.artifact_id != null ? { artifact_id: ref.document.artifact_id } : {}), ...(ref.document.target_state != null ? { target_state: ref.document.target_state } : {}), ...(ref.document.model != null ? { model: ref.document.model } : {}) } } : {}) };
    if (ref.kind === 'native_document') return { ...base, kind: ref.kind, artifact_id: ref.artifact_id, ...(ref.target_state != null ? { target_state: ref.target_state } : {}), ...(ref.model != null ? { model: ref.model } : {}) };
    if (ref.kind === 'native_sequence') return { ...base, kind: ref.kind, publication_ref: ref.publication_ref, native_output_id: ref.native_output_id };
    return { ...base, kind: ref.kind, publication_ref: ref.publication_ref, native_record_id: ref.native_record_id };
}
export const uniqueCandidates = (refs: CandidateRef[]) => [...new Map(refs.map(ref => [candidateKey(ref), candidateIdentity(ref)])).values()];
export function readRoundStorage<T>(jobId: string, slot: string, fallback: T): T {
    try { return JSON.parse(localStorage.getItem(`bms:binder-round:${jobId}:${slot}`) ?? 'null') ?? fallback; } catch { return fallback; }
}
export function writeRoundStorage(jobId: string, slot: string, value: unknown) {
    try { localStorage.setItem(`bms:binder-round:${jobId}:${slot}`, JSON.stringify(value)); } catch { /* private/disabled storage */ }
}
export const submitSelectedRound = (request: SelectedRoundRequest) => submitPreparedSelectedMutation(() =>
    api.post<SelectedRoundResponse>('/api/binder-continuation/selected', request).then(response => response.data));
export const fetchSelectedRounds = async (jobId: string, signal?: AbortSignal): Promise<SelectedRound[]> => {
    const { data } = await api.get<{ selected_rounds?: SelectedRound[] }>(`/api/binder-continuation/${encodeURIComponent(jobId)}/round`, { signal });
    return data.selected_rounds ?? [];
};
export interface CandidateSet {
    id: string; name: string; revision?: number; candidate_refs?: CandidateRef[]; design_ids?: string[];
    ranking?: unknown; filter_state: Record<string, unknown>; final_designation?: { designated: boolean; supersedes_set_id?: string };
}
export async function fetchCandidateSets(jobId: string): Promise<CandidateSet[]> {
    const { data } = await fetchJobById(jobId);
    return (data.saved_selection_sets ?? []) as CandidateSet[];
}
export async function saveCandidateSet(jobId: string, request: Omit<CandidateSet, 'id'>): Promise<CandidateSet[]> {
    await api.post(`/api/jobs/${encodeURIComponent(jobId)}/review-filter-sets`, request);
    // Read the owner back, not the mutation's optimistic echo.
    return fetchCandidateSets(jobId);
}
