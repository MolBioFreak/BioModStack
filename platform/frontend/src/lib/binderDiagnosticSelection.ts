import { api } from './api';

export interface CandidateDocument { artifact_id?: string; target_state?: string }
export type CandidateDocuments = Record<string, CandidateDocument>;
export interface DiagnosticSelectionContext {
    source_job_id: string;
    lineage_root_job_id: string;
    targets: Array<{ name: string | null; owner_job_id: string }>;
    candidate_documents: Record<string, Array<CandidateDocument & { logical_path?: string; primary?: boolean }>>;
}
export const fetchDiagnosticSelectionContext = async (jobId: string): Promise<DiagnosticSelectionContext> =>
    (await api.get<DiagnosticSelectionContext>(`/api/blind-pose/${encodeURIComponent(jobId)}/selection-context`)).data;
