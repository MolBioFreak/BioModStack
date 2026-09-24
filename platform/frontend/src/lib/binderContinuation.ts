import { isAxiosError } from 'axios';
import { api, submitJob, type Job } from './api';
import type { FrustraMpnnRequestedSettings } from '../components/frustrampnn/frustraMpnnSettingsState';
import type { CandidateDocuments } from './binderDiagnosticSelection';

export type BinderOperation = 'refine' | 'caliby' | 'frustrampnn' | 'fampnn' | 'proteinmpnn' | 'predict_boltz2' | 'predict_protenix';
export interface BinderSelectedRequest {
    source_job_id: string;
    design_ids: string[];
    operation: BinderOperation;
    params?: Record<string, unknown>;
    frustrampnn_settings?: FrustraMpnnRequestedSettings;
    execution_target_id?: string | null;
    candidate_documents?: CandidateDocuments;
    launch_context_id?: string | null;
}
export interface BinderSelectedResponse {
    source_job_id: string;
    root_job_id: string;
    operation: BinderOperation;
    selected_design_count: number;
    launched_jobs: Job[];
}
export async function submitBinderSelected(request: BinderSelectedRequest): Promise<BinderSelectedResponse> {
    try {
        return (await api.post<BinderSelectedResponse>('/api/binder-continuation/selected', request)).data;
    } catch (error) {
        if (!isAxiosError(error) || error.response?.status !== 409) throw error;
        const detail = error.response.data?.detail;
        if (detail?.code !== 'remote_prepared_job_review_required') throw error;
        const requests = detail.job_requests ?? [detail.job_request];
        if (!requests.length || requests.some((item: { execution_target_id?: string; execution_plan_approval?: unknown } | undefined) => !item?.execution_target_id || item.execution_plan_approval)) throw error;
        const launched: Job[] = [];
        try {
            // Shared review submits retained snapshots, not this endpoint again.
            for (const prepared of requests) launched.push((await submitJob(prepared, { launchContext: false })).data);
        } catch (reason) {
            if (launched.length) throw new Error(`Already queued Jobs: ${launched.map(job => job.id).join(', ')}. Remaining submission stopped: ${String(reason)}`);
            throw reason;
        }
        return { ...detail.response_context, launched_jobs: launched };
    }
}

export function readBinderCandidateDocuments(jobId: string): CandidateDocuments {
    try {
        const value: unknown = JSON.parse(sessionStorage.getItem(`bms:selected-designs:${jobId}:documents`) ?? '{}');
        if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
        return Object.fromEntries(Object.entries(value).filter(([, item]) => item && typeof item === 'object' && !Array.isArray(item)
            && Object.entries(item).every(([key, val]) => (key === 'artifact_id' || key === 'target_state') && typeof val === 'string')));
    } catch { return {}; }
}
export function writeBinderCandidateDocuments(jobId: string, documents: CandidateDocuments) {
    try { sessionStorage.setItem(`bms:selected-designs:${jobId}:documents`, JSON.stringify(documents)); } catch { /* storage unavailable */ }
    window.dispatchEvent(new CustomEvent('bms:binder-documents', { detail: { jobId, documents } }));
}

export function readBinderSelection(jobId: string): string[] {
    try {
        const value = JSON.parse(sessionStorage.getItem(`bms:selected-designs:${jobId}`) ?? '[]');
        return Array.isArray(value) ? [...new Set(value.filter((id): id is string => typeof id === 'string'))] : [];
    } catch { return []; }
}
export function writeBinderSelection(jobId: string, ids: string[]) {
    try { sessionStorage.setItem(`bms:selected-designs:${jobId}`, JSON.stringify(ids)); } catch { /* storage unavailable */ }
}
