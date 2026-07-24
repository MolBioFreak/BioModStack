import axios from 'axios';

import { api, type JobLogs } from '../../lib/api';

export type CmBackend = 'protenix_v2_ensemble' | 'confornets' | 'external_import';
export type CmSourceKind =
    | 'complex_snapshot'
    | 'structure_upload'
    | 'structure_artifact'
    | 'protein_sequence'
    | 'confornets_checkpoint'
    | 'confornets_config'
    | 'confornets_state';
export type CmTask = 'diversity' | 'mse' | 'transfer';

export interface CmSource {
    source_id: string;
    source_kind: CmSourceKind;
    format: string;
    sha256: string;
    bytes: number;
    metadata: Record<string, unknown>;
    created_at?: string;
}

export interface CmFeaturePolicy {
    mode: 'regenerate_mutated_protein_v1' | 'paired_regenerate_changed_protein_v1' | 'features_disabled_control_v1';
    protein_msa_enabled?: boolean;
    templates_enabled?: boolean;
    rna_msa_enabled?: boolean;
}

export type CmRuntimePolicy =
    | { use_default_params: true }
    | { use_default_params: false; n_cycle: number; n_step: number };

export interface CmAnalysisPolicy {
    sign_zero_epsilon: number;
    clash_detector_id: 'bms_clash';
    clash_detector_version: '1';
    outer_support_minimum: number;
    inner_support_minimum: number;
    sign_consistency_minimum: number;
    clash_free_minimum: number;
    rank_stability_minimum: number;
    minimum_common_ranked_universe_size: number;
}

export interface CmConfornetsControls {
    chain_id: string;
    task: CmTask;
    test_case_id: string;
    benchmark_name: string;
    runs: number;
    saved_steps: number[];
    confornet_count: number;
    samples: number;
    max_steps: number;
    num_recycles: number;
    num_diffusion_steps: number;
    learning_rate: number;
    gradient_clip: number;
    skip_msa: boolean;
    compute_confidence: boolean;
    save_full_confidence: boolean;
    compute_evaluation: boolean;
}

export interface CmSubmitRequest {
    name: string;
    idempotency_key: string;
    backend: CmBackend;
    ordered_seeds: number[];
    samples_per_seed: number;
    feature_policy: CmFeaturePolicy;
    runtime_policy: CmRuntimePolicy;
    analysis_policy: CmAnalysisPolicy;
    registered_snapshot_id?: string;
    registered_artifact_ids?: string[];
    registered_sequence_id?: string;
    registered_reference_ids?: string[];
    registered_checkpoint_id?: string;
    registered_config_id?: string;
    registered_transfer_id?: string;
    confornets?: CmConfornetsControls;
}

export interface CmSubmitReceipt {
    request_id: string;
    job_id: string;
    status: string;
    backend: CmBackend;
    request_sha256: string;
    coordinate_plan_sha256: string;
    expected_cardinality: number;
    idempotent_retry?: boolean;
}

export interface CmStatus {
    request_id: string;
    job_id: string;
    backend: CmBackend;
    status: 'prepared' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
    job_status: string | null;
    progress: Record<string, unknown>;
    failure_receipt: Record<string, unknown> | null;
    retry_eligible: boolean;
    result_contract_id: string;
}

export interface CmProgress {
    request_id: string;
    status: string;
    progress: Record<string, unknown>;
    job_stage: string | null;
    job_progress: unknown;
}

export interface CmFailureReceipt {
    receipt_id: string;
    sha256: string;
    payload: Record<string, unknown>;
}

export interface CmArtifact {
    artifact_id: string;
    candidate_id: string | null;
    role: string;
    relative_path: string;
    sha256: string;
    bytes: number;
    media_type: string;
    metadata: Record<string, unknown>;
}

export interface CmRecord {
    type: string;
    key: string;
    sha256: string;
    payload: Record<string, unknown>;
}

export type CmStateLandscapeComparisonMode = 'pairwise' | 'reference';
export type CmStateLandscapeMetricStatus = 'ok' | 'unavailable';
export type CmStateLandscapeClass = 'high' | 'neutral' | 'minimally_frustrated';

export interface CmStateLandscapeIdentity {
    target_id: string;
    entity_instance_id: string;
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code: string;
    sequence_index: number;
    validated_wt: string;
}

export interface CmStateLandscapeResolvedPair {
    pair_id: string;
    candidate_a_id: string;
    candidate_b_id: string;
}

export type CmStateLandscapeNumericMetric =
    | { a: number; b: number; delta_b_minus_a: number; status: 'ok'; reason: null }
    | { a: null; b: null; delta_b_minus_a: null; status: 'unavailable'; reason: string };

export type CmStateLandscapeClassMetric =
    | { a: CmStateLandscapeClass; b: CmStateLandscapeClass; transition: string; status: 'ok'; reason: null }
    | { a: null; b: null; transition: null; status: 'unavailable'; reason: string };

export interface CmStateLandscapeMetrics {
    native_score: CmStateLandscapeNumericMetric;
    high_non_native_highly_frustrated_fraction: CmStateLandscapeNumericMetric;
    maximum_non_native_substitution_delta_relative_to_native: CmStateLandscapeNumericMetric;
    native_class: CmStateLandscapeClassMetric;
}

export interface CmStateLandscapeRow extends CmStateLandscapeResolvedPair {
    identity: CmStateLandscapeIdentity;
    metrics: CmStateLandscapeMetrics;
}

export interface CmStateLandscapeSupport extends CmStateLandscapeResolvedPair {
    eligible_row_count: number;
    excluded_row_count: number;
}

export interface CmStateLandscapeExclusion extends CmStateLandscapeResolvedPair {
    identity: CmStateLandscapeIdentity | null;
    reason: string;
    detail: string;
}

export interface CmStateLandscapeAnalysis {
    schema_name: 'cm_state_landscape_analysis';
    schema_version: 1;
    analysis_id: string;
    source_ensemble_sha256: string;
    source_landscape_sha256: string;
    source_structure_map_sha256: string;
    comparison_mode: CmStateLandscapeComparisonMode;
    comparison_target_id: string;
    comparison_scope: 'all_within_target' | 'all_other_within_target';
    reference_backend_coordinates: Record<string, unknown> | null;
    reference_candidate_id: string | null;
    resolved_pairs: CmStateLandscapeResolvedPair[];
    comparison_sha256: string;
    formula_version: 'cm_state_landscape_analysis_v1';
    formula_sha256: string;
    policy_sha256: string;
    rows: CmStateLandscapeRow[];
    support_ledger: CmStateLandscapeSupport[];
    exclusion_ledger: CmStateLandscapeExclusion[];
}

export interface CmResults {
    request_id: string;
    result_contract_id: string;
    records: CmRecord[];
    artifacts: CmArtifact[];
}

export interface CmStateLandscapeAnalysisAuthority {
    content_sha256: string;
    source_ensemble_sha256: string;
    source_landscape_sha256: string;
    source_structure_map_sha256: string;
    comparison_sha256: string;
    formula_version: string;
    formula_sha256: string;
    policy_sha256: string;
}

export interface CmStateLandscapeAnalysisSummary {
    request_id: string;
    analysis_id: string;
    authority: CmStateLandscapeAnalysisAuthority;
    comparison: {
        mode: CmStateLandscapeComparisonMode;
        target_id: string;
        scope: 'all_within_target' | 'all_other_within_target';
        reference_backend_coordinates: Record<string, unknown> | null;
        reference_candidate_id: string | null;
    };
    counts: { pairs: number; rows: number; exclusions: number };
    pairs: CmStateLandscapeResolvedPair[];
    artifact: { artifact_id: string; content_sha256: string; size_bytes: number; media_type: string; download_url: string } | null;
}

export interface CmStateLandscapeAnalysisRowsPage {
    request_id: string;
    selected_analysis_id: string;
    offset: number;
    limit: number;
    applied_filters: {
        pair_id: string | null;
        candidate_id: string | null;
        entity_instance_id: string | null;
        auth_asym_id: string | null;
        sequence_start: number | null;
        sequence_end: number | null;
    };
    next_offset: number | null;
    rows: Array<CmStateLandscapeRow & { availability: Record<string, unknown> }>;
}

export interface CmLandscapeRow {
    candidate_id: string;
    entity_instance_id: string;
    auth_asym_id: string;
    auth_seq_id: string;
    insertion_code: string;
    sequence_index: number;
    wt: string;
    mutation_aa: string;
    score: number | null;
    class: string | null;
    scoreable: boolean;
    status: string;
    reason: string | null;
    provenance: Record<string, unknown>;
}

export interface CmLandscapePage {
    request_id: string;
    offset: number;
    limit: number;
    candidate_id: string | null;
    entity_instance_id: string | null;
    sequence_start: number | null;
    sequence_end: number | null;
    next_offset: number | null;
    rows: CmLandscapeRow[];
}

export const cmApiError = (value: unknown, fallback: string): string => {
    if (axios.isAxiosError(value)) {
        const detail = value.response?.data?.detail;
        if (typeof detail === 'string' && detail) return detail;
        if (Array.isArray(detail)) return detail.map((item) => String(item?.msg || item)).join('; ');
    }
    return value instanceof Error && value.message ? value.message : fallback;
};

export const listCmSources = async (): Promise<CmSource[]> =>
    (await api.get<{ sources: CmSource[] }>('/api/conformational-mapping/sources')).data.sources;

export const registerCmSource = async (
    sourceKind: CmSourceKind,
    file: File,
    metadata: Record<string, unknown> = {},
): Promise<CmSource> => {
    const body = new FormData();
    body.append('source_kind', sourceKind);
    body.append('metadata_json', JSON.stringify(metadata));
    body.append('file', file);
    return (await api.post<CmSource>('/api/conformational-mapping/sources', body)).data;
};

export const submitCmRequest = async (payload: CmSubmitRequest): Promise<CmSubmitReceipt> =>
    (await api.post<CmSubmitReceipt>('/api/conformational-mapping/requests', payload)).data;

export const getCmStatus = async (requestId: string): Promise<CmStatus> =>
    (await api.get<CmStatus>(`/api/conformational-mapping/requests/${encodeURIComponent(requestId)}`)).data;

export const getCmProgress = async (requestId: string): Promise<CmProgress> =>
    (await api.get<CmProgress>(`/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/progress`)).data;

export const getCmFailureReceipts = async (requestId: string): Promise<CmFailureReceipt[]> =>
    (await api.get<{ failure_receipts: CmFailureReceipt[] }>(
        `/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/failure-receipts`,
    )).data.failure_receipts;

// Logs remain a read-only job diagnostic contract. Canonical creation, lifecycle,
// result discovery, and artifact access never use the generic job submission route.
export const getCmLogs = async (jobId: string): Promise<JobLogs> =>
    (await api.get<JobLogs>(`/api/jobs/${encodeURIComponent(jobId)}/logs`)).data;

export const getCmResults = async (requestId: string): Promise<CmResults> =>
    (await api.get<CmResults>(`/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/results`)).data;

export const getCmLandscape = async (
    requestId: string,
    candidateId: string,
    offset = 0,
    limit = 1000,
): Promise<CmLandscapePage> =>
    (await api.get<CmLandscapePage>(`/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/landscape`, {
        params: { candidate_id: candidateId, offset, limit },
    })).data;

/** Compact B2 projection; callers validate it before rendering any scientific value. */
export const getCmStateLandscapeAnalysis = async (requestId: string): Promise<CmStateLandscapeAnalysisSummary> =>
    (await api.get<CmStateLandscapeAnalysisSummary>(
        `/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/state-landscape-analysis`,
    )).data;

/** B2 row pages are intentionally bounded to a single server-authoritative pair. */
export const getCmStateLandscapeAnalysisRows = async (
    requestId: string,
    analysisId: string,
    pairId: string,
    offset = 0,
    limit = 50,
): Promise<CmStateLandscapeAnalysisRowsPage> =>
    (await api.get<CmStateLandscapeAnalysisRowsPage>(
        `/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/state-landscape-analysis/rows`,
        { params: { analysis_id: analysisId, pair_id: pairId, offset, limit } },
    )).data;

export const cancelCmRequest = async (requestId: string): Promise<{ request_id: string; status: string }> =>
    (await api.post(`/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/cancel`)).data;

export const retryCmRequest = async (requestId: string): Promise<{ request_id: string; job_id: string; status: string; retry_count: number }> =>
    (await api.post(`/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/retry`)).data;

export const cmArtifactUrl = (requestId: string, artifactId: string): string =>
    `/api/conformational-mapping/requests/${encodeURIComponent(requestId)}/artifacts/${encodeURIComponent(artifactId)}`;
