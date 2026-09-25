import { api } from './api';

export const isNativeBinderGeneration = (job: { model_id: string; mode: string } | undefined) => Boolean(job && (
    (job.model_id === 'boltzgen' && ['protein_binder', 'nanobody_binder', 'peptide_binder'].includes(job.mode))
    || (job.model_id === 'ppiflow' && ['protein_binder', 'antibody_binder', 'nanobody_binder'].includes(job.mode))));
export interface NativeGenerationDocument {
    artifact_id: string; target_state?: string | null; logical_path?: string; download_url?: string; primary?: boolean; sha256?: string;
}
export interface NativeGenerationRecord extends Record<string, unknown> {
    candidate_key?: string; design_id?: string; structures?: NativeGenerationDocument[];
}
export interface NativeGenerationPage {
    receipt: Record<string, unknown>; records: NativeGenerationRecord[];
    publication: Record<string, unknown>; total: number; offset: number; limit: number;
    artifacts?: Array<{ path: string; download_url?: string; bytes?: number }>;
}
export async function fetchNativeBinderGeneration(jobId: string, offset = 0, limit = 100, signal?: AbortSignal): Promise<NativeGenerationPage> {
    return (await api.get<NativeGenerationPage>(`/api/jobs/${encodeURIComponent(jobId)}/generation-results`, { params: { offset, limit }, signal })).data;
}
export function nativeCandidateRoute(jobId: string, designId: string, document?: NativeGenerationDocument, launchContextId?: string | null) {
    const query = new URLSearchParams({ design_id: designId });
    if (launchContextId) query.set('launch_context_id', launchContextId);
    if (document) {
        query.set('artifact_id', document.artifact_id);
        if (document.target_state != null) query.set('target_state', document.target_state);
    }
    return `/designs/${encodeURIComponent(jobId)}?${query}`;
}
