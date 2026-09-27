import { api, type PersistedAnalysisRun } from './api';

export interface BinderPrediction {
    job_id: string; design_id: string | null; name: string | null;
    status: string; model_id: string; target_state: unknown;
    binder_chains: string[]; target_chains: string[];
    pae_url: string | null; chain_metrics_url: string | null; design_url: string | null;
    analyses: PersistedAnalysisRun<unknown>[]; ipsae: PersistedAnalysisRun<unknown>[];
    error_message?: string | null;
}
export interface BinderSequence {
    job_id: string; design_id: string | null; name: string | null;
    status: string; model_id: string; native_identity: unknown;
    predictions: BinderPrediction[];
}
export interface BinderEvidenceRecord {
    source_design_id: string; candidate_key: string | null; sequences: BinderSequence[];
}
export interface BinderEvidencePage {
    schema_version: 1; job_id: string; offset: number; limit: number; total: number;
    records: BinderEvidenceRecord[];
}
export async function fetchBinderEvidence(jobId: string, offset: number, signal?: AbortSignal): Promise<BinderEvidencePage> {
    const { data } = await api.get<BinderEvidencePage>(`/api/designs/by-job/${encodeURIComponent(jobId)}/binder-evidence`, { params: { offset, limit: 100 }, signal });
    if (data.schema_version !== 1 || data.job_id !== jobId || !Array.isArray(data.records)) throw Error('Binder evidence readback unavailable');
    return data;
}
export const evidenceText = (value: unknown): string => value == null ? 'Unmeasured' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export const predictionKey = (prediction: BinderPrediction) => JSON.stringify([prediction.job_id, prediction.design_id, prediction.target_state]);
