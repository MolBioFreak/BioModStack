import { api } from './api.js';
import type { CmLandscapePage, CmLandscapeRow } from '../components/conformationalMapping/conformationalMappingApi.js';
import type { FrustraMpnnStructureMap } from '../components/conformationalMapping/frustraMpnnViewerMetrics.js';
import { parseFrustraMpnnMultidimensionalPage, type FrustraMpnnMultidimensionalPage } from '../components/frustraMpnnMultidimensionalModel.js';

export type FrustraMpnnTerminalStatus = 'succeeded' | 'failed' | 'not_run';
export type FrustraMpnnJobStatus = 'queued' | 'running' | 'awaiting_input' | 'completed' | 'failed' | 'cancelled';
export type FrustraMpnnClass = 'high' | 'neutral' | 'minimal';
export type FrustraMpnnSlotStatus = 'ok' | 'missing';

export interface FrustraMpnnDesignSelection {
    design_id: string;
    source_sha256: string;
}

export interface FrustraMpnnAnalyzeRequest {
    selections: FrustraMpnnDesignSelection[];
}

export interface FrustraMpnnReceiptResult {
    parent_job_id: string;
    invocation_id: string;
    candidate_id: string;
    status: FrustraMpnnTerminalStatus;
    manifest_sha256: string;
}

export interface FrustraMpnnLineageEnvelope {
    schema_name: 'bms.frustrampnn.scheduler-child.v1';
    schema_version: 1;
    execution_owner_job_id: string;
    source_parent_job_id: string | null;
    source_batch_id: string | null;
    trigger: 'upload_analyze' | 'design_analyze' | 'antibody_iteration' | 'batch_completion' | 'reanalyze';
    selection: Array<{
        selection_ordinal: number;
        design_id: string | null;
        source_job_id: string | null;
        source_path: string | null;
        snapshot_relative_path: string;
        size_bytes: number;
        sha256: string;
        media_type: string;
        source_format: 'pdb' | 'mmcif';
        candidate_id: string;
        invocation_id: string;
        component_request_relative_path: string;
        component_request_sha256: string;
        normalized_source_relative_path: string;
        normalized_source_sha256: string;
        producer_coordinates: Record<string, string | number | null>;
    }>;
    component_invocation_ids: string[];
    batch_manifest_relative_path: string;
    batch_manifest_size_bytes: number;
    batch_manifest_sha256: string;
    supersedes_child_job_id: string | null;
    prior_invocation_ids: string[];
    result_persistence_identity: '(child_job_id, invocation_id)';
}

export interface FrustraMpnnChildReceipt {
    job_id: string;
    child_job_id: string;
    result_job_id: string;
    parent_job_id: string | null;
    status: FrustraMpnnJobStatus;
    queue_status: string | null;
    assigned_gpu: number | null;
    retry_count: number;
    max_retries: number;
    error_message: string | null;
    created_at: string;
    started_at: string | null;
    completed_at: string | null;
    lineage: FrustraMpnnLineageEnvelope;
    results: FrustraMpnnReceiptResult[];
}

export interface FrustraMpnnClassCounts {
    high: number;
    neutral: number;
    minimal: number;
}

export interface FrustraMpnnClassFractions {
    high: number;
    neutral: number;
    minimal: number;
}

export type FrustraMpnnFailureClass =
    | 'request_invalid'
    | 'source_missing'
    | 'source_hash_mismatch'
    | 'identity_ambiguous'
    | 'normalization_failed'
    | 'runtime_unavailable'
    | 'runtime_digest_mismatch'
    | 'checkpoint_mismatch'
    | 'gpu_admission_failed'
    | 'inference_nonzero_exit'
    | 'inference_timeout'
    | 'raw_output_missing'
    | 'raw_output_invalid'
    | 'position_mapping_failed'
    | 'wildtype_mismatch'
    | 'landscape_incomplete'
    | 'manifest_invalid'
    | 'publication_failed'
    | 'ingestion_failed';

export interface FrustraMpnnRuntimeIdentity {
    sif_sha256: string;
    executable_sha256: string;
    checkpoint_id: string;
    checkpoint_sha256: string;
}

export interface FrustraMpnnAssignedGpu {
    physical_device_id: string | null;
    task_visible_device_index: number | null;
}

export interface FrustraMpnnTerminalArtifact {
    relative_path: string;
    schema_name: string | null;
    schema_version: number | null;
    sha256: string;
    bytes: number;
    cardinality: {
        kind: 'rows' | 'residues' | 'slots' | 'records';
        count: number;
    } | null;
}

export interface FrustraMpnnTerminalResult {
    schema_name: 'workflow_component_result';
    schema_version: 1;
    request_sha256: string;
    invocation_id: string;
    component_id: 'frustrampnn';
    component_contract_version: '1.0';
    candidate_id: string;
    parent_job_id: string;
    parent_workflow_id: string;
    status: FrustraMpnnTerminalStatus;
    failure_class: FrustraMpnnFailureClass | null;
    diagnostic: string | null;
    source_artifact: {
        relative_path: string;
        sha256: string;
        media_type: string;
        producer_stage: string;
        artifact_id: string | null;
    };
    runtime_identity: FrustraMpnnRuntimeIdentity;
    artifacts: FrustraMpnnTerminalArtifact[];
    result_payload: { schema_name: string; schema_version: number };
    started_at: string;
    ended_at: string;
    duration_seconds: number;
    assigned_gpu: FrustraMpnnAssignedGpu;
}

export interface FrustraMpnnSummary {
    schema_name: 'frustrampnn_summary';
    schema_version: 1;
    target_id: string;
    parent_job_id: string;
    candidate_id: string;
    landscape_sha256: string;
    residue_support: {
        expected: number;
        mapped: number;
        scoreable: number;
        excluded: number;
        ambiguous: number;
    };
    slot_support: { expected: number; observed: number; scoreable: number };
    missingness_by_reason: Record<string, number>;
    native_slot_counts: FrustraMpnnClassCounts;
    native_slot_fractions: FrustraMpnnClassFractions;
    complete_landscape_counts: FrustraMpnnClassCounts;
    complete_landscape_fractions: FrustraMpnnClassFractions;
    support_by_entity_chain: Array<{
        entity_instance_id: string;
        auth_asym_id: string;
        expected_residues: number;
        mapped_residues: number;
        scoreable_residues: number;
        expected_slots: number;
        observed_slots: number;
        scoreable_slots: number;
    }>;
    threshold_policy: { id: 'frustrampnn_threshold_v1'; high_max: -1; minimal_min: 0.58 };
    threshold_policy_sha256: string;
}

export interface FrustraMpnnResultListItem {
    invocation_id: string;
    parent_job_id: string;
    parent_workflow_id: string;
    candidate_id: string;
    design_id: string | null;
    requiredness: 'required' | 'optional';
    source_artifact_id: string | null;
    source_artifact_sha256: string;
    request_sha256: string;
    manifest_sha256: string;
    summary_sha256: string;
    created_at: string;
    status: FrustraMpnnTerminalStatus;
    component_contract_version: string;
    runtime_identity: FrustraMpnnRuntimeIdentity;
    assigned_gpu: FrustraMpnnAssignedGpu;
    failure_class: FrustraMpnnFailureClass | null;
    diagnostic: string | null;
}

export interface FrustraMpnnResultDetail extends FrustraMpnnResultListItem {
    summary: FrustraMpnnSummary;
    terminal_result: FrustraMpnnTerminalResult;
}

export interface FrustraMpnnResultList {
    items: FrustraMpnnResultListItem[];
    total: number;
    limit: number;
    offset: number;
}

export interface FrustraMpnnArtifact {
    artifact_id: string;
    invocation_id: string;
    role: string;
    relative_path: string;
    content_sha256: string;
    size_bytes: number;
    media_type: string;
    schema_name: string | null;
    schema_version: number | null;
    cardinality: FrustraMpnnTerminalArtifact['cardinality'];
}

export interface FrustraMpnnArtifactList {
    items: FrustraMpnnArtifact[];
    total: number;
}

interface FrustraMpnnLandscapeWireRow {
    id: string;
    invocation_id: string;
    candidate_id: string;
    target_id: string;
    entity_instance_id: string;
    auth_asym_id: string;
    auth_seq_id: number;
    insertion_code: string;
    sequence_index: number;
    wt: string;
    mutation_aa: string;
    score: number | null;
    class: FrustraMpnnClass | null;
    score_class: FrustraMpnnClass | null;
    scoreable: boolean;
    status: FrustraMpnnSlotStatus;
    reason: string | null;
    native: boolean;
    provenance: Record<string, unknown>;
    residue: {
        entity_instance_id: string;
        source_entity_id: string | null;
        label_asym_id: string | null;
        auth_asym_id: string;
        label_seq_id: number | null;
        auth_seq_id: number;
        insertion_code: string;
        sequence_index: number;
        pdb_chain_id: string;
        pdb_residue_id: number;
        pdb_insertion_code: string;
        model_position: number;
        residue_name: string;
        wt: string | null;
    } | null;
}

interface FrustraMpnnLandscapeWirePage {
    items: FrustraMpnnLandscapeWireRow[];
    candidate_id: string;
    total: number;
    limit: number;
    offset: number;
    next_offset: number | null;
}

export interface FrustraMpnnLandscapeFilters {
    target_id?: string;
    entity_instance_id?: string;
    auth_asym_id?: string;
    auth_seq_id?: number;
    insertion_code?: string;
    sequence_index?: number;
    mutation_aa?: string;
    status?: FrustraMpnnSlotStatus;
}

const requireCanonicalClass = (value: unknown): FrustraMpnnClass | null => {
    if (value == null) return null;
    if (value === 'high' || value === 'neutral' || value === 'minimal') return value;
    throw new Error(`Persisted FrustraMPNN row has unsupported class ${String(value)}`);
};

export interface NormalizedFrustraMpnnLandscapePage extends CmLandscapePage {
    total: number;
}

export const normalizeFrustraMpnnLandscapePage = (
    jobId: string,
    wire: FrustraMpnnLandscapeWirePage,
): NormalizedFrustraMpnnLandscapePage => {
    if (!wire.candidate_id || !Number.isInteger(wire.offset) || wire.offset < 0
        || !Number.isInteger(wire.limit) || wire.limit < 1 || wire.limit > 500
        || !Number.isInteger(wire.total) || wire.total < 0
        || wire.items.length > wire.limit || wire.offset + wire.items.length > wire.total) {
        throw new Error('Persisted FrustraMPNN landscape envelope is invalid');
    }
    const expectedNextOffset = wire.offset + wire.items.length < wire.total
        ? wire.offset + wire.items.length
        : null;
    if (wire.next_offset !== expectedNextOffset) {
        throw new Error('Persisted FrustraMPNN landscape pagination is inconsistent');
    }
    const rows: CmLandscapeRow[] = wire.items.map((row) => {
        if (row.candidate_id !== wire.candidate_id || !row.entity_instance_id || !row.auth_asym_id
            || !Number.isInteger(row.auth_seq_id) || !Number.isInteger(row.sequence_index) || row.sequence_index < 1
            || row.insertion_code.length > 1 || !/^[ACDEFGHIKLMNPQRSTVWY]$/.test(row.wt)
            || !/^[ACDEFGHIKLMNPQRSTVWY]$/.test(row.mutation_aa)
            || !row.residue
            || row.residue.entity_instance_id !== row.entity_instance_id
            || row.residue.auth_asym_id !== row.auth_asym_id
            || row.residue.auth_seq_id !== row.auth_seq_id
            || row.residue.insertion_code !== row.insertion_code
            || row.residue.sequence_index !== row.sequence_index
            || row.residue.wt !== row.wt) {
            throw new Error('Persisted FrustraMPNN landscape identity is invalid');
        }
        const canonicalClass = requireCanonicalClass(row.class);
        if (canonicalClass !== requireCanonicalClass(row.score_class)) {
            throw new Error('Persisted FrustraMPNN landscape class fields conflict');
        }
        if (row.status === 'ok' && (!row.scoreable || typeof row.score !== 'number' || !Number.isFinite(row.score) || canonicalClass == null)) {
            throw new Error('Persisted FrustraMPNN scoreable slot is incomplete');
        }
        if (row.status === 'missing' && (row.scoreable || row.score != null || canonicalClass != null)) {
            throw new Error('Persisted FrustraMPNN missing slot contains a score or class');
        }
        return {
            candidate_id: row.candidate_id,
            entity_instance_id: row.entity_instance_id,
            auth_asym_id: row.auth_asym_id,
            auth_seq_id: String(row.auth_seq_id),
            insertion_code: row.insertion_code,
            sequence_index: row.sequence_index,
            wt: row.wt,
            mutation_aa: row.mutation_aa,
            score: row.score,
            class: canonicalClass,
            scoreable: row.scoreable,
            status: row.status,
            reason: row.reason,
            provenance: row.provenance,
        };
    });
    return {
        request_id: jobId,
        offset: wire.offset,
        limit: wire.limit,
        total: wire.total,
        candidate_id: wire.candidate_id,
        entity_instance_id: null,
        sequence_start: null,
        sequence_end: null,
        next_offset: wire.next_offset,
        rows,
    };
};

export const analyzeFrustraMpnnDesigns = async (
    parentJobId: string,
    payload: FrustraMpnnAnalyzeRequest,
    signal?: AbortSignal,
): Promise<FrustraMpnnChildReceipt> => (
    await api.post<FrustraMpnnChildReceipt>(
        `/api/frustrampnn/jobs/${encodeURIComponent(parentJobId)}/analyze`,
        payload,
        { signal },
    )
).data;

export const reanalyzeFrustraMpnn = async (childJobId: string, signal?: AbortSignal): Promise<FrustraMpnnChildReceipt> => (
    await api.post<FrustraMpnnChildReceipt>(
        `/api/frustrampnn/jobs/${encodeURIComponent(childJobId)}/reanalyze`,
        {},
        { signal },
    )
).data;

export const fetchFrustraMpnnReceipt = async (childJobId: string, signal?: AbortSignal): Promise<FrustraMpnnChildReceipt> => (
    await api.get<FrustraMpnnChildReceipt>(
        `/api/frustrampnn/jobs/${encodeURIComponent(childJobId)}/receipt`,
        { signal },
    )
).data;

export const listFrustraMpnnResults = async (
    jobId: string,
    limit = 200,
    offset = 0,
    signal?: AbortSignal,
): Promise<FrustraMpnnResultList> => (
    await api.get<FrustraMpnnResultList>(`/api/frustrampnn/jobs/${encodeURIComponent(jobId)}/results`, {
        params: { limit, offset }, signal,
    })
).data;

export const fetchFrustraMpnnResult = async (
    jobId: string,
    invocationId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnResultDetail> => (
    await api.get<FrustraMpnnResultDetail>(
        `/api/frustrampnn/results/${encodeURIComponent(invocationId)}`,
        { params: { job_id: jobId }, signal },
    )
).data;

export const fetchFrustraMpnnLandscape = async (
    jobId: string,
    invocationId: string,
    offset: number,
    limit: number,
    filters: FrustraMpnnLandscapeFilters = {},
    signal?: AbortSignal,
): Promise<NormalizedFrustraMpnnLandscapePage> => {
    const wire = (
        await api.get<FrustraMpnnLandscapeWirePage>(
            `/api/frustrampnn/results/${encodeURIComponent(invocationId)}/landscape`,
            { params: { job_id: jobId, offset, limit, ...filters }, signal },
        )
    ).data;
    return normalizeFrustraMpnnLandscapePage(jobId, wire);
};

export const listFrustraMpnnArtifacts = async (
    jobId: string,
    invocationId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnArtifactList> => (
    await api.get<FrustraMpnnArtifactList>(
        `/api/frustrampnn/results/${encodeURIComponent(invocationId)}/artifacts`,
        { params: { job_id: jobId }, signal },
    )
).data;

export const frustraMpnnArtifactUrl = (
    jobId: string,
    artifactId: string,
): string => `/api/frustrampnn/artifacts/${encodeURIComponent(artifactId)}?job_id=${encodeURIComponent(jobId)}`;

export const fetchFrustraMpnnStructureMap = async (
    jobId: string,
    artifactId: string,
    signal?: AbortSignal,
): Promise<FrustraMpnnStructureMap> => (
    await api.get<FrustraMpnnStructureMap>(frustraMpnnArtifactUrl(jobId, artifactId), { signal })
).data;

export const fetchFrustraMpnnMultidimensionalPoints = async (
    datasetIds: string[] = [],
    limit = 1000,
    signal?: AbortSignal,
): Promise<FrustraMpnnMultidimensionalPage> => {
    const response = await api.get<unknown>('/api/frustrampnn/analytics/points', {
        params: {
            level: 'result',
            limit,
            ...(datasetIds.length > 0 ? { dataset_ids: datasetIds.join(',') } : {}),
        },
        signal,
    });
    return parseFrustraMpnnMultidimensionalPage(response.data);
};
