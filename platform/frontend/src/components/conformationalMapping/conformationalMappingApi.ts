import axios from 'axios';

import { api, type JobLogs } from '../../lib/api';
import type { FrustraMpnnRequestedSettings } from '../frustrampnn/frustraMpnnSettingsState';

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
    managed_checkpoint?: boolean;
    authority_receipt?: {
        schema_name: 'cm_source_authority_receipt';
        schema_version: 1;
        source_id: string;
        source_kind: CmSourceKind;
        content_sha256: string;
        authority_kind: 'complex_snapshot_normalization' | 'rcsb_download' | 'run_artifact' | 'completed_run_artifact';
        payload: Record<string, unknown>;
        receipt_sha256: string;
    } | null;
    submission_policy?: {
        chain_id: string;
        test_case_id: string;
        benchmark_name: string;
    } | null;
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

export const compileCmRuntimePolicy = (
    backend: CmBackend,
    useDefaults: boolean,
    nCycle: number,
    nStep: number,
): CmRuntimePolicy => backend !== 'protenix_v2_ensemble' || useDefaults
    ? { use_default_params: true }
    : { use_default_params: false, n_cycle: nCycle, n_step: nStep };

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
    task: CmTask;
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

export type CmBackendCoordinates =
    | { backend: 'protenix_v2_ensemble'; target_id: string; ordered_seed: number; sample_index: number }
    | { backend: 'confornets'; target_id: string; task: string; test_case_id: string; reference_id: string | null; run_index: number; saved_step: number; confornet_index: number; sample_index: number }
    | { backend: 'external_import'; target_id: string; staged_index: number; source_content_sha256: string; staged_receipt_sha256: string };

export type CmStateLandscapeComparison =
    | { mode: 'pairwise'; target_id: string; scope: 'all_within_target' }
    | { mode: 'reference'; target_id: string; scope: 'all_other_within_target'; reference_backend_coordinates: CmBackendCoordinates };

export interface CmSubmitRequest {
    name: string;
    notes: string;
    idempotency_key: string;
    backend: CmBackend;
    ordered_seeds: number[];
    samples_per_seed: number;
    feature_policy: CmFeaturePolicy;
    runtime_policy: CmRuntimePolicy;
    analysis_policy: CmAnalysisPolicy;
    frustrampnn_settings: FrustraMpnnRequestedSettings;
    state_landscape_comparison?: CmStateLandscapeComparison;
    registered_snapshot_id?: string;
    /** Bounded wire collection containing exactly one completed-run or uploaded mmCIF source. */
    registered_artifact_ids?: [string];
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

export interface CmReusableArtifact {
    artifact_id: string; name?: string; role?: string; artifact_type?: string; format: string;
    media_type?: string; candidate_id?: string | null;
    sha256: string; bytes: number; available?: boolean;
    model_id?: string | null; sample_id?: string | null; chain_ids?: string[]; entity_ids?: string[];
    backend_coordinates?: CmBackendCoordinates | null;
}
export interface CmReusableRun {
    run_id?: string; request_id?: string; job_id: string; run_name?: string; name?: string;
    workflow: string; status: string; backend?: CmBackend; completed_at?: string | null;
    artifacts: CmReusableArtifact[];
}
export interface CmRcsbEntry {
    accession: string; title: string; resolution?: number | null; organism?: string | null;
    method?: string | null; release_date?: string | null;
    models: Array<{ model_id: string; label: string }>;
    samples: Array<{ sample_id: string; label: string }>;
    chains: Array<{ chain_id: string; label: string; entity_id: string; entity_type: string; residue_count: number }>;
    entities: Array<{ entity_id: string; label: string; entity_type: string; residue_count: number }>;
    required_selection: Array<'model_id' | 'sample_id' | 'chain_ids' | 'entity_ids'>;
}
export interface CmRcsbSearchResponse { query: string; total_count: number; results: CmRcsbEntry[] }
export interface CmRcsbSelection { accession: string; model_id?: string; sample_id?: string; chain_ids?: string[]; entity_ids?: string[] }

export const CANONICAL_CM_ANALYSIS_POLICY: CmAnalysisPolicy = Object.freeze({
    sign_zero_epsilon: 0.000001,
    clash_detector_id: 'bms_clash',
    clash_detector_version: '1',
    outer_support_minimum: 0.8,
    inner_support_minimum: 0.6,
    sign_consistency_minimum: 0.8,
    clash_free_minimum: 0.9,
    rank_stability_minimum: 0.6,
    minimum_common_ranked_universe_size: 3,
});

export interface CmSelectedInputRecord {
    source_id: string;
    source_kind: string;
    source_label: string;
    source_sha256: string;
    provider?: string;
    accession?: string;
    model_id?: string;
    sample_id?: string;
    chain_ids?: string[];
}

export interface CmRunRecord {
    name: string;
    notes: string;
    selected_input: CmSelectedInputRecord;
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
    run_record: CmRunRecord | null;
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

export const cmSourceContentUrl = (sourceId: string): string =>
    `/api/conformational-mapping/sources/${encodeURIComponent(sourceId)}/content`;

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

export const registerCmRcsbSelection = async (selection: CmRcsbSelection): Promise<CmSource> => {
    const accession = selection.accession.trim().toUpperCase();
    const response = await api.post<CmSource>(
        `/api/conformational-mapping/sources/rcsb/${encodeURIComponent(accession)}`,
        { ...selection, accession },
    );
    return response.data;
};

/** Compatibility entry point for callers that only have an accession. */
export const registerCmRcsbMmcif = async (pdbId: string): Promise<CmSource> =>
    registerCmRcsbSelection({ accession: pdbId });

export const listCmReusableRuns = async (): Promise<CmReusableRun[]> =>
    (await api.get<{ runs: CmReusableRun[] }>('/api/conformational-mapping/runs')).data.runs;
export const registerCmRunArtifact = async (runId: string, artifactId: string): Promise<CmSource> => {
    const response = await api.post<CmSource>(
        `/api/conformational-mapping/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}/sources`,
    );
    return response.data;
};

const RCSB_REQUIRED_SELECTION = ['model_id', 'sample_id', 'chain_ids', 'entity_ids'] as const;

const rcsbContractError = (detail: string): Error =>
    new Error(`RCSB search contract error: ${detail}`);

const rcsbObject = (value: unknown, detail: string): Record<string, unknown> => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw rcsbContractError(detail);
    return value as Record<string, unknown>;
};

const rcsbString = (value: unknown, detail: string): string => {
    if (typeof value !== 'string' || !value.trim()) throw rcsbContractError(detail);
    return value;
};

const rcsbCollection = <T>(
    value: unknown,
    name: string,
    parse: (item: Record<string, unknown>, index: number) => T,
): T[] => {
    if (!Array.isArray(value) || value.length === 0) {
        throw rcsbContractError(`${name} must be a non-empty server-defined collection`);
    }
    return value.map((item, index) => parse(rcsbObject(item, `${name}[${index}] must be an object`), index));
};

const uniqueRcsbIds = (values: string[], name: string): void => {
    if (new Set(values).size !== values.length) throw rcsbContractError(`${name} identities must be unique`);
};

const parseCmRcsbEntry = (value: unknown): CmRcsbEntry => {
    const entry = rcsbObject(value, 'each entry must be an object');
    const accession = rcsbString(entry.accession, 'entry accession is required').toUpperCase();
    if (!/^[A-Z0-9]{4}$/.test(accession)) throw rcsbContractError('entry accession must be four letters or digits');
    const title = rcsbString(entry.title, `entry ${accession} title is required`);
    const models = rcsbCollection(entry.models, `entry ${accession} models`, (item, index) => ({
        model_id: rcsbString(item.model_id, `entry ${accession} models[${index}].model_id is required`),
        label: rcsbString(item.label, `entry ${accession} models[${index}].label is required`),
    }));
    const samples = rcsbCollection(entry.samples, `entry ${accession} samples`, (item, index) => ({
        sample_id: rcsbString(item.sample_id, `entry ${accession} samples[${index}].sample_id is required`),
        label: rcsbString(item.label, `entry ${accession} samples[${index}].label is required`),
    }));
    const entities = rcsbCollection(entry.entities, `entry ${accession} entities`, (item, index) => {
        const residueCount = item.residue_count;
        if (!Number.isInteger(residueCount) || Number(residueCount) < 1) {
            throw rcsbContractError(`entry ${accession} entities[${index}].residue_count must be a positive integer`);
        }
        return {
            entity_id: rcsbString(item.entity_id, `entry ${accession} entities[${index}].entity_id is required`),
            label: rcsbString(item.label, `entry ${accession} entities[${index}].label is required`),
            entity_type: rcsbString(item.entity_type, `entry ${accession} entities[${index}].entity_type is required`),
            residue_count: Number(residueCount),
        };
    });
    const chains = rcsbCollection(entry.chains, `entry ${accession} chains`, (item, index) => {
        const residueCount = item.residue_count;
        if (!Number.isInteger(residueCount) || Number(residueCount) < 1) {
            throw rcsbContractError(`entry ${accession} chains[${index}].residue_count must be a positive integer`);
        }
        return {
            chain_id: rcsbString(item.chain_id, `entry ${accession} chains[${index}].chain_id is required`),
            label: rcsbString(item.label, `entry ${accession} chains[${index}].label is required`),
            entity_id: rcsbString(item.entity_id, `entry ${accession} chains[${index}].entity_id is required`),
            entity_type: rcsbString(item.entity_type, `entry ${accession} chains[${index}].entity_type is required`),
            residue_count: Number(residueCount),
        };
    });
    uniqueRcsbIds(models.map((item) => item.model_id), `entry ${accession} model`);
    uniqueRcsbIds(samples.map((item) => item.sample_id), `entry ${accession} sample`);
    uniqueRcsbIds(chains.map((item) => item.chain_id), `entry ${accession} chain`);
    uniqueRcsbIds(entities.map((item) => item.entity_id), `entry ${accession} entity`);
    const entityById = new Map(entities.map((item) => [item.entity_id, item]));
    const entityIds = new Set(entityById.keys());
    const chainEntityIds = new Set(chains.map((item) => item.entity_id));
    if (chains.some((chain) => {
        const entity = entityById.get(chain.entity_id);
        return !entity
            || entity.entity_type !== chain.entity_type
            || entity.residue_count !== chain.residue_count;
    })
        || entityIds.size !== chainEntityIds.size
        || [...entityIds].some((entityId) => !chainEntityIds.has(entityId))) {
        throw rcsbContractError(`entry ${accession} chain/entity collections are inconsistent`);
    }
    const requiredSelection = entry.required_selection;
    if (!Array.isArray(requiredSelection)
        || requiredSelection.some((requirement) => typeof requirement !== 'string')
        || requiredSelection.length !== RCSB_REQUIRED_SELECTION.length
        || new Set(requiredSelection).size !== RCSB_REQUIRED_SELECTION.length
        || RCSB_REQUIRED_SELECTION.some((requirement) => !requiredSelection.includes(requirement))) {
        throw rcsbContractError(`entry ${accession} required_selection must explicitly require model, sample, chain, and entity`);
    }
    const resolution = entry.resolution;
    if (resolution != null && (typeof resolution !== 'number' || !Number.isFinite(resolution) || resolution <= 0)) {
        throw rcsbContractError(`entry ${accession} resolution must be a positive finite number or null`);
    }
    const optionalString = (field: string, raw: unknown): string | null => {
        if (raw == null) return null;
        if (typeof raw !== 'string') throw rcsbContractError(`entry ${accession} ${field} must be a string or null`);
        return raw;
    };
    const methods = entry.experimental_methods;
    if (methods != null && (!Array.isArray(methods) || methods.some((method) => typeof method !== 'string' || !method))) {
        throw rcsbContractError(`entry ${accession} experimental_methods must contain source-defined strings`);
    }
    return {
        accession,
        title,
        method: optionalString('method', entry.method ?? (Array.isArray(methods) ? methods[0] : null)),
        resolution: resolution == null ? null : resolution,
        organism: optionalString('organism', entry.organism),
        release_date: optionalString('release_date', entry.release_date ?? entry.deposition_date),
        models,
        samples,
        chains,
        entities,
        required_selection: [...RCSB_REQUIRED_SELECTION],
    };
};

export const searchCmRcsb = async (query: string): Promise<CmRcsbSearchResponse> => {
    const normalized = query.trim();
    const params = /^[A-Za-z0-9]{4}$/.test(normalized)
        ? { accession: normalized.toUpperCase() }
        : { keyword: normalized };
    const response = await api.get<unknown>('/api/conformational-mapping/sources/rcsb/search', { params });
    const body = rcsbObject(response.data, 'response must be an object');
    const responseQuery = rcsbString(body.query, 'response query is required');
    if (!Array.isArray(body.entries)) throw rcsbContractError('response entries must be an array');
    const entries = body.entries.map(parseCmRcsbEntry);
    return {
        query: responseQuery,
        total_count: entries.length,
        results: entries,
    };
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
