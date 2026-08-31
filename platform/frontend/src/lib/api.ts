import axios from 'axios';
import type { TelemetryChartHistoryResponse } from './telemetryChart';
import type { ViewerSnapshotV2 } from '../structureViewer/contracts/m6Reproducibility';
import type { SpatialVolumeDescriptorV1, VolumeRegistrationV1, VolumeSegmentationV1 } from '../structureViewer/contracts/spatialVolumes';

// Use relative path - Vite's proxy handles /api -> localhost:8000
const API_BASE = '';

export const api = axios.create({
    baseURL: API_BASE,
});

api.interceptors.request.use((config) => {
    if (config.headers.get('X-BMS-Skip-Launch-Context') === '1') {
        config.headers.delete('X-BMS-Skip-Launch-Context');
        return config;
    }
    const launchContextId = typeof window !== 'undefined'
        ? new URLSearchParams(window.location.search).get('launch_context_id')
        : null;
    if (launchContextId) {
        config.headers.set('X-BMS-Launch-Context-ID', launchContextId);
    }
    return config;
});

// Types
export interface Job {
    id: string;
    name: string;
    status: 'queued' | 'running' | 'completed' | 'awaiting_input' | 'failed' | 'cancelled';
    model_id: string;
    mode: string;
    params: Record<string, UntypedApiValue>;
    launch_context_id?: string | null;
    created_at: string;
    design_count: number;
    requested_design_count?: number | null;
    output_dir: string | null;
    error_message?: string | null;
    // Batch grouping for job sets
    batch_id?: string | null;
    batch_name?: string | null;
    // Parent-child relationship for exploration mode
    parent_job_id?: string | null;
    lineage_root_job_id?: string | null;
    stage_family?: string | null;
    stage_mode?: string | null;
    source_stage_job_id?: string | null;
    source_stage_family?: string | null;
    source_stage_mode?: string | null;
    source_selection_manifest_path?: string | null;
    source_selection_count?: number | null;
    selected_input_artifact_class?: string | null;
    selected_input_schema_version?: number | null;
    selection_source_type?: string | null;
    selection_source_job_id?: string | null;
    selection_dataset_name?: string | null;
    selected_loop_scope?: Record<string, unknown> | null;
    provenance?: Record<string, unknown> | null;
    saved_selection_sets?: SavedReviewFilterSet[] | null;
    // GPU and timing info
    pinned_gpu?: number | null;  // User-specified GPU pin
    assigned_gpu?: number | null;
    started_at?: string | null;
    completed_at?: string | null;
    vram_estimate_mb?: number | null;
    // Stage tracking for multi-stage pipelines
    current_stage?: string | null;
    completed_stages?: string[] | null;
    all_stages?: string[] | null;
    stage_outputs?: Record<string, string[]> | null;
    files?: unknown;
    results?: unknown;
    awaiting_input?: boolean | null;
    awaiting_stage?: string | null;
    awaiting_payload?: Record<string, UntypedApiValue> | null;
    decision_history?: Array<Record<string, UntypedApiValue>> | null;
    frustrampnn_result_count?: number;
    frustrampnn_reopen_destination?: Record<string, UntypedApiValue> | null;
    conformational_mapping_request_id?: string | null;
    selected_cdr_loops?: string[] | null;
}

export interface MDArtifact {
    id: string;
    replica: number;
    name: string;
    bytes: number;
    sha256: string;
    semantic_role: 'analysis_topology' | 'analysis_trajectory' | 'representative_structure' | 'trajectory_frame_map' | 'atom_order_manifest' | null;
    atom_order_identity: string | null;
    selection_method?: string | null;
    source_frame?: number | null;
    time_ps?: number | null;
    source_trajectory_sha256?: string | null;
    format: string;
    content_url: string;
}

export interface RFD3LocalRedesignReadModel {
    schema: 'bms.rfd3.local-redesign.read-model.v1';
    job_id: string;
    capabilities: {
        source_structure: boolean;
        candidate_structures: boolean;
        native_metadata: boolean;
        trajectories: {
            requested: boolean;
            available: boolean;
            reason: 'produced' | 'not_requested' | 'requested_artifacts_unavailable';
        };
    };
    request: {
        request_id: string;
        schema_version: number;
        request_sha256: string;
        profile_id: string;
        profile_registry_sha256: string;
        redesign_mode: string;
        sequence_policy: string;
        status: string;
        request: Record<string, UntypedApiValue>;
        request_path_scope: 'basename';
        provenance_path_scope: 'basename';
        preparation_receipt?: Record<string, UntypedApiValue> | null;
        runtime_identity?: Record<string, UntypedApiValue> | null;
        result_manifest_sha256?: string | null;
        failure_receipt?: Record<string, UntypedApiValue> | null;
        created_at?: string | null;
        updated_at?: string | null;
        terminal_at?: string | null;
    };
    candidates: Array<{
        candidate_id: string;
        result_set: string;
        stage: string;
        status: string;
        artifact_manifest_sha256: string;
        metrics: Record<string, UntypedApiValue>;
        metadata: Record<string, UntypedApiValue>;
    }>;
    artifacts: Array<{
        artifact_id: string;
        candidate_id?: string | null;
        role: string;
        relative_path: string;
        sha256: string;
        bytes: number;
        media_type: string;
        metadata: Record<string, UntypedApiValue>;
    }>;
}

export interface RFD3GenerationRange {
    min: number | null;
    mean: number | null;
    max: number | null;
}

export interface RFD3GenerationReadModel {
    schema: 'bms.rfd3.generation.read-model.v1';
    job_id: string;
    request: Record<string, UntypedApiValue>;
    result_manifest_sha256: string;
    counts: {
        requested: number;
        generated: number;
        accepted: number;
    };
    aggregates: {
        length: RFD3GenerationRange;
        radius: RFD3GenerationRange;
        helix: RFD3GenerationRange;
        strand: RFD3GenerationRange;
    };
    candidates: Array<{
        candidate_id: string;
        status: string;
        length: number;
        radius: number;
        helix_count: number | null;
        strand_count: number | null;
        structure_url: string;
    }>;
}
export interface ProteinLocalRedesignResultSurface {
    schema: 'bms.workflow.protein-local-redesign.results.v1';
    job: {
        id: string;
        name: string;
        status: string;
        model_id: string;
        mode: string;
        request_sha256: string | null;
    };
    source: { artifacts: ProteinLocalRedesignResultArtifact[] };
    receipt: Record<string, UntypedApiValue> | null;
    tabs: Array<{
        id: 'rfd3' | 'fampnn' | 'esmfold2' | 'protenix_v2';
        label: string;
        role: string;
        status: 'complete' | 'partial';
        count: number;
        candidate_count: number;
        expected_candidate_count: number;
        items: ProteinLocalRedesignResultItem[];
    }>;
    artifacts: ProteinLocalRedesignResultArtifact[];
    capabilities: {
        model_native_tabs: string[];
        structure_viewer: boolean;
        sequence_viewer: boolean;
        volume_viewer: boolean;
    };
    counts: {
        persisted_design_rows: number;
        source_artifacts: number;
        tabs: Record<string, number>;
    };
    composition: { algorithm: string; sha256: string };
}

export interface ProteinLocalRedesignResultArtifact {
    artifact_id: string;
    kind: string;
    label: string;
    relative_path: string;
    sha256: string;
    bytes: number;
    media_type: string;
    content_url: string;
}

export interface ProteinLocalRedesignResultItem {
    item_id: string;
    design_id: string | null;
    candidate_id: string;
    candidate_label: string;
    sample_index: number | null;
    name: string;
    structure: ProteinLocalRedesignResultArtifact;
    metrics: Record<string, UntypedApiValue>;
    metadata: Record<string, UntypedApiValue>;
    sequence?: string;
    metrics_artifact?: string;
    confidence_artifact?: string;
    msa_artifact?: string;
    native_metadata_artifact?: string;
    msa?: Record<string, UntypedApiValue>;
}

export interface MDSummary {
    schema: 'bms.md.summary.v1';
    job_id: string;
    status: string;
    result_state: 'partial' | 'completed' | null;
    source: 'validated_job_owned_manifests';
    bounded: true;
    aggregate_manifest_sha256: string;
    replica_count: number;
    artifact_count: number;
    replicas: Array<{ replica: number; status: string; engine: { name?: string; version?: string; platform?: string }; performance: Record<string, number> }>;
    analysis_status: 'absent' | 'partial' | 'completed';
    trajectory_playback: { supported: false; reason: string } | {
        supported: true;
        replicas: Array<{
            replica: number;
            trajectory_sha256: string;
            frame_map_artifact_id: string;
            frame_count: number;
            first_source_frame: number;
            last_source_frame: number;
            first_time_ps: number;
            last_time_ps: number;
        }>;
    };
}

export interface MDTrajectoryFrameMap {
    schema: 'bms.md.trajectory-frame-map.v1';
    replica: number;
    trajectory_sha256: string;
    frames: Array<{ display_frame: number; source_frame: number; time_ps: number; step: number }>;
}

export interface MDAnalysisPoint {
    replica: number;
    time_ps: number;
    source_frame: number;
    rmsd_angstrom: number;
    radius_of_gyration_angstrom: number;
}

export interface MDAnalysisReplicaReport {
    schema: 'bms.md.analysis.v1';
    status: 'completed' | 'failed';
    replica?: number;
    points?: MDAnalysisPoint[];
    residue_metrics?: Array<{ segid: string; resid: number; resname: string; backbone_rmsf_angstrom: number; backbone_atom_count: number }>;
    block_statistics?: Array<{ block: number; count: number; mean_rmsd_angstrom: number; mean_radius_of_gyration_angstrom: number }>;
    summary?: { count: number; min: number; mean: number; max: number; final: number };
    failure?: { code: string; message: string };
    inputs: { manifest_sha256: string | null; topology_sha256?: string; trajectory_sha256?: string; atom_order_identity?: string };
}

export interface MDAnalysisReportSet {
    schema: 'bms.md.analysis-report-set.v1';
    job_id: string;
    status: 'absent' | 'partial' | 'completed';
    bounded: true;
    replica_states: Array<{ replica: number; status: 'absent' | 'completed' | 'failed' }>;
    reports: MDAnalysisReplicaReport[];
    ensemble: {
        statistical_unit: 'replica';
        frame_pooling: false;
        completed_replicas: number;
        mean_of_replica_mean_rmsd_angstrom: number | null;
        sample_stdev_of_replica_mean_rmsd_angstrom: number | null;
        mean_of_replica_final_rmsd_angstrom: number | null;
        sample_stdev_of_replica_final_rmsd_angstrom: number | null;
    };
    evidence: { status: 'insufficient_evidence'; reason: string; frames_are_independent_replicates: false };
    retry: { eligible: boolean; active: boolean; reason: string };
}

export const fetchMDSummary = (jobId: string) => api.get<MDSummary>(`/api/jobs/${jobId}/md/summary`);
export const fetchMDArtifacts = (jobId: string) => api.get<{ schema: string; job_id: string; source: string; bounded: true; artifacts: MDArtifact[] }>(`/api/jobs/${jobId}/md/artifacts`);
export const fetchMDAnalysis = (jobId: string) => api.get<MDAnalysisReportSet>(`/api/jobs/${jobId}/md/analysis`);
export const retryMDAnalysis = (jobId: string) => api.post<{ schema: 'bms.md.analysis-retry.v1'; status: string; created_child_ids: string[] }>(`/api/jobs/${jobId}/md/analysis/retry`);

export interface MDRunDetail {
    schema: 'bms.md.run-detail.v1'; job_id: string; job_status: string; queue_status: string; phase: string; state_version: number;
    chemistry: { profile_id: string; profile_sha256: string; assurance: string; verification_status: string; requested_scope?: string | null };
    engine: 'gromacs' | 'openmm'; replica_count: number; replica_summary: Record<string, number>;
    simulated_time_ps: number; requested_time_ps: number; checkpoint_available: boolean;
    allowed_actions: Array<'pause' | 'resume_dynamics' | 'retry_dynamics' | 'cancel' | 'view_logs' | 'reorchestrate' | 'delete_failed_launch'>;
    action_explanations?: Partial<Record<'resume_dynamics' | 'retry_dynamics', string>>;
    replicas: Array<{ id: string; replica_index: number; attempt: number; state: string; active: boolean; engine: string; failure: unknown; retry_eligible: boolean }>;
    segments: Array<{ id: string; replica_run_id: string; segment_index: number; state: string; source_segment_id: string | null; source_checkpoint_id: string | null; start_step: number | null; end_step: number | null; start_time_ps: number | null; end_time_ps: number | null }>;
    checkpoints: Array<{ id: string; segment_id: string; logical_role: string; relative_path: string; sha256: string; bytes: number; step: number; time_ps: number }>;
    events: Array<{ id: string; event_type: string; state_version: number; payload: Record<string, unknown>; created_at: string }>;
}

export const fetchMDRun = (jobId: string) => api.get<MDRunDetail>(`/api/molecular-dynamics/runs/${jobId}`);
export const pauseMDRun = (jobId: string, stateVersion: number, idempotencyKey: string) => api.post<MDRunDetail>(`/api/molecular-dynamics/runs/${jobId}/pause`, { expected_state_version: stateVersion, idempotency_key: idempotencyKey });
export const cancelMDRun = (jobId: string, stateVersion: number, idempotencyKey: string) => api.post<MDRunDetail>(`/api/molecular-dynamics/runs/${jobId}/cancel`, { expected_state_version: stateVersion, idempotency_key: idempotencyKey });
export const resumeMDRun = (jobId: string, stateVersion: number, idempotencyKey: string) => api.post(`/api/molecular-dynamics/runs/${jobId}/resume`, { expected_state_version: stateVersion, idempotency_key: idempotencyKey });
export const retryMDDynamics = (jobId: string, replicaIndex: number, stateVersion: number, idempotencyKey: string) => api.post<{ schema: 'bms.md.retry-receipt.v1'; job_id: string; replica_run_id: string; child_job_id: string; replica_index: number; attempt: number }>(`/api/molecular-dynamics/runs/${jobId}/retry`, { replica_index: replicaIndex, expected_state_version: stateVersion, idempotency_key: idempotencyKey });
export const reorchestrateMDRun = (jobId: string, stateVersion: number, idempotencyKey: string) => api.post<{ new_job_id: string }>(`/api/molecular-dynamics/runs/${jobId}/reorchestrate`, { expected_state_version: stateVersion, idempotency_key: idempotencyKey });
export const deleteFailedMDLaunch = (jobId: string, stateVersion: number) => api.delete(`/api/molecular-dynamics/runs/${jobId}/failed-launch`, { data: { expected_state_version: stateVersion } });

// Log data for View Logs modal
export interface JobLogs {
    job_id: string;
    job_name: string;
    status: string;
    command_log: string | null;
    command_err: string | null;
    nextflow_log: string | null;
    exit_code: number | null;
    parsed_error: string | null;
    nextflow_log_source?: 'job_output' | 'legacy_global' | null;
}

export interface GPUProcess {
    pid: number;
    name: string;
    memory_mb: number;
}

export interface GPUStatus {
    index: number;
    name: string;
    utilization: number;
    memory_utilization: number;
    memory_used_mb: number;
    memory_total_mb: number;
    reserved_memory_mb: number;
    power_draw_w: number;
    power_limit_w: number;
    min_power_watts: number;
    default_power_watts: number;
    max_power_watts: number;
    temperature: number;
    fan_speed: number;
    clock_graphics_mhz: number;
    clock_memory_mhz: number;
    clock_max_graphics_mhz: number;
    clock_max_memory_mhz: number;
    processes: GPUProcess[];
}

export interface CPUPowerTelemetry {
    source: 'rapl' | string;
    available: boolean;
    status: 'ok' | 'priming' | 'unreadable' | 'no_sources' | 'read_error' | string;
    message: string;
    discovered_sources: number;
    readable_sources: number;
    setup_hint?: string | null;
}

export interface CPUStatus {
    name: string;
    cores_physical: number;
    cores_logical: number;
    utilization: number;
    per_core_utilization: number[];
    frequency_current_mhz: number;
    frequency_max_mhz: number;
    temperature: number | null;
    power_watts: number | null;  // Package power via RAPL
    power_telemetry?: CPUPowerTelemetry;
}

export interface RAMStatus {
    total_gb: number;
    used_gb: number;
    available_gb: number;
    utilization: number;
    swap_total_gb: number;
    swap_used_gb: number;
    swap_percent: number;
}

export interface SystemStatus {
    gpus: GPUStatus[];
    gpu_error?: string | null;
    cpu: CPUStatus;
    ram: RAMStatus;
    timestamp: string;
    cpu_history: number[];
    ram_history: number[];
}

export interface PowerProfile {
    eco_mode: boolean;
    message: string;
}

export interface PowerControlState {
    limits: Record<number, number>;
    eco_mode: boolean;
    power_percentage: number;
    total_current_watts: number;
    total_max_watts: number;
    hardware_limits: Record<number, {
        min: number;
        default: number;
        max: number;
        eco: number;
        name: string;
    }>;
}

export interface PowerControlResponse {
    success: boolean;
    message: string;
    limits: Record<number, number>;
    eco_mode: boolean;
}

export interface PerGpuFanStatus {
    gpu_index: number;
    gpu_name: string;
    settings_gpu_target?: number | null;
    fan_targets?: string[];
    mapping_source?: string;
    mode: 'auto' | 'manual' | 'unknown';
    target_percent: number | null;
    current_percent: number | null;
    current_rpm: number | null;
    min_percent: number;
    max_percent: number;
    profile_mode: 'auto' | 'manual' | 'unknown';
    profile_target_percent: number;
    writable: boolean;
    warning?: string | null;
}

export interface FanControlState {
    supported: boolean;
    message: string;
    backend: string;
    available_modes: string[];
    gpus: Record<string, PerGpuFanStatus>;
}

export interface FanControlResponse {
    success: boolean;
    message: string;
    fan_control: FanControlState;
}

export interface HardwareDiscoveryResponse {
    success: boolean;
    message: string;
    gpu_count: number;
    gpu_error?: string | null;
    cpu_power_telemetry: CPUPowerTelemetry;
    power_control: PowerControlState;
    fan_control: FanControlState;
    timestamp: string;
}

// API functions
// API functions
export const fetchJobs = (params?: {
    status?: string;
    q?: string;
    model_id?: string;
    mode?: string;
    limit?: number;
    offset?: number;
    include_children?: boolean;
    summary?: boolean;
}) => api.get<{ jobs: Job[]; total: number }>('/api/jobs', {
    params: {
        ...params,
        limit: Math.min(500, Math.max(1, params?.limit ?? 100)),
        summary: params?.summary ?? true,
    },
});
// Bound live telemetry requests so a half-open connection cannot permanently
// occupy the shared collector and suppress its recovery/backoff loop.
export interface TelemetryHistoryPoint {
    timestamp_ms: number;
    sample_count: number;
    payload: SystemStatus & { timestamp_ms?: number };
}

export interface TelemetryHistoryResponse {
    source: 'immutable_server_telemetry';
    database: 'dedicated_telemetry_store';
    resolution: 'raw' | 'minute';
    start_ms: number;
    end_ms: number;
    generated_at_ms: number;
    points: TelemetryHistoryPoint[];
}

export const fetchSystemStatus = () => api.get<SystemStatus>('/api/gpu/status', { timeout: 10_000 });
export const fetchTelemetryHistory = (startMs: number, endMs: number, resolution: 'raw' | 'minute', limit = 4000) =>
    api.get<TelemetryHistoryResponse>('/api/telemetry/history', {
        params: { start_ms: startMs, end_ms: endMs, resolution, limit },
        timeout: 10_000,
    });
export const fetchTelemetryChartHistory = (
    startMs: number,
    endMs: number,
    bucketMs: number,
    sinceMs: number | null,
) => api.get<TelemetryChartHistoryResponse>('/api/telemetry/chart-history', {
    params: {
        start_ms: startMs,
        end_ms: endMs,
        bucket_ms: bucketMs,
        ...(sinceMs == null ? {} : { since_ms: sinceMs }),
    },
    timeout: 10_000,
});
export const fetchJobById = (id: string) => api.get<Job>(`/api/jobs/${id}`);
export const fetchRFD3LocalRedesign = (id: string) => api.get<RFD3LocalRedesignReadModel>(`/api/jobs/${id}/rfd3-local-redesign`);
export const fetchRFD3Generation = (id: string) => api.get<RFD3GenerationReadModel>(`/api/jobs/${encodeURIComponent(id)}/rfd3-generation`);
export const fetchProteinLocalRedesignResults = (id: string) => api.get<ProteinLocalRedesignResultSurface>(`/api/jobs/${encodeURIComponent(id)}/workflow-results`);
export const fetchDesignById = (id: string) => api.get<Design>(`/api/designs/${id}`);
export interface ProteinBaseBundleImportRequest {
    bundle_path: string;
    dataset_name: string;
    job_name?: string;
}
export const importProteinBaseBundle = (payload: ProteinBaseBundleImportRequest) => api.post<Job>('/api/jobs/imports/proteinbase', payload);

export interface ExternalImportPreview {
    provider: string;
    resource_type: string;
    provider_job_id: string;
    model: string | null;
    model_version: string | null;
    status: string;
    sample_count: number;
    entities: Array<Record<string, unknown>>;
    source_fingerprint: string;
    run_metadata_sha256: string;
    archive_sha256: string | null;
    importable: boolean;
    error_code: string | null;
    errors: string[];
    warnings: string[];
    provider_metadata: Record<string, unknown>;
}

export interface ExternalResultImport {
    id: string;
    provider_id: string;
    resource_type: string;
    provider_job_id: string;
    state: 'discovered' | 'validating' | 'staging' | 'normalizing' | 'committing' | 'completed' | 'failed';
    source_fingerprint: string;
    bms_job_id: string | null;
    failure_code: string | null;
    failure_message: string | null;
    created_at: string;
    updated_at: string;
    imported_at: string | null;
}

export const previewExternalImport = (sourcePath: string) => api.post<ExternalImportPreview>(
    '/api/jobs/imports/external/preview',
    { source_path: sourcePath, provider_hint: 'boltz_api' },
);
export const createExternalImport = (payload: {
    source_path: string;
    provider: 'boltz_api';
    preview_fingerprint: string;
    dataset_name: string;
    job_name?: string;
}) => api.post<ExternalResultImport>('/api/jobs/imports/external', payload);
export const fetchExternalImport = (importId: string) => api.get<ExternalResultImport>(
    `/api/jobs/imports/external/${importId}`,
);
export const cancelJob = (id: string) => api.delete(`/api/jobs/${id}`);
export const deleteJobPermanently = (id: string) => api.delete<{
    message: string;
    job_id: string;
    children_deleted: number;
    directories_deleted: string[];
}>(`/api/jobs/${id}/permanent`);
export const resubmitJob = (id: string) => api.post<{
    message: string;
    original_job_id: string;
    new_job_id: string;
    new_job_name: string;
}>(`/api/jobs/${id}/resubmit`);



// Upload file
export interface FileUploadResponse {
    filename: string;
    path: string;
    size: number;
}

export const uploadFile = async (path: string, file: File) => {
    const formData = new FormData();
    formData.append('path', path);
    formData.append('file', file);
    return api.post<FileUploadResponse>('/api/files/upload', formData, {
        headers: {
            'Content-Type': 'multipart/form-data',
        },
    });
};

export interface ImmutableFileUploadResponse extends FileUploadResponse {
    sha256: string;
    existing: boolean;
}

export const uploadImmutableFile = async (path: string, file: File, sha256: string) => {
    const formData = new FormData();
    formData.append('path', path);
    formData.append('sha256', sha256);
    formData.append('file', file);
    return api.post<ImmutableFileUploadResponse>('/api/files/upload-immutable-structure', formData, {
        headers: {
            'Content-Type': 'multipart/form-data',
        },
    });
};

// Extract a single chain from a multi-chain PDB
export interface ExtractChainResult {
    success: boolean;
    input_path: string;
    output_path: string;
    chain_extracted: string;
    atom_count: number;
    renamed_to: string | null;
}

export const extractChain = async (
    inputPath: string,
    chainId: string,
    renameTo?: string,
    modelNumber?: number
): Promise<{ data: ExtractChainResult }> => {
    const formData = new FormData();
    formData.append('input_path', inputPath);
    formData.append('chain_id', chainId);
    if (renameTo) {
        formData.append('rename_to', renameTo);
    }
    if (modelNumber != null) {
        formData.append('model_number', String(modelNumber));
    }
    return api.post<ExtractChainResult>('/api/files/extract-chain', formData, {
        headers: {
            'Content-Type': 'multipart/form-data',
        },
    });
};

// Start a job
export const submitJob = (jobData: Partial<Job>, options: { launchContext?: boolean } = {}) => {
    const useLaunchContext = options.launchContext !== false;
    const launchContextId = typeof window !== 'undefined' && useLaunchContext
        ? new URLSearchParams(window.location.search).get('launch_context_id')
        : null;
    const payload = launchContextId && !jobData.launch_context_id
        ? { ...jobData, launch_context_id: launchContextId }
        : jobData;
    return api.post('/api/jobs', payload, useLaunchContext
        ? undefined
        : { headers: { 'X-BMS-Skip-Launch-Context': '1' } });
};

export const completeCurrentLaunchContext = async (responseData: unknown): Promise<string | null> => {
    const data = responseData && typeof responseData === 'object'
        ? responseData as Record<string, unknown>
        : {};
    const directReturn = data.return_uri;
    if (typeof directReturn === 'string' && directReturn.startsWith('/') && !directReturn.startsWith('//')) {
        return directReturn;
    }
    const launchContextId = typeof window !== 'undefined'
        ? new URLSearchParams(window.location.search).get('launch_context_id')
        : null;
    const launchedJob = data.launched_job && typeof data.launched_job === 'object'
        ? data.launched_job as Record<string, unknown>
        : null;
    const jobId = typeof data.id === 'string'
        ? data.id
        : typeof data.job_id === 'string'
            ? data.job_id
            : typeof launchedJob?.id === 'string'
                ? launchedJob.id
                : null;
    if (!launchContextId || !jobId) return null;
    const binding = await api.post<{ return_uri: string }>(
        `/api/launch-contexts/${encodeURIComponent(launchContextId)}/bind`,
        { job_id: jobId },
    );
    const returnUri = binding.data.return_uri;
    return typeof returnUri === 'string' && returnUri.startsWith('/') && !returnUri.startsWith('//')
        ? returnUri
        : null;
};

export interface ShapeGeometrySummary {
    geometry_id: string;
    source_id: string;
    geometry_sha256: string;
    manifest_sha256: string;
    source_sha256: string;
    preview_obj_sha256: string | null;
    point_pool_sha256: string;
    sdf_sha256: string;
    sdf_sign: 'positive_inside';
    sdf_grid_shape: [number, number, number];
    vertex_count: number;
    face_count: number;
    point_count: number;
    bounds_angstrom: [number, number, number, number, number, number];
    dimensions_angstrom: [number, number, number];
    source_format: 'obj' | 'stl';
    source_parser: 'obj_triangle_v1' | 'obj_triangle_v2' | 'stl_ascii_v1' | 'stl_binary_v1';
    source_unit: string;
    angstrom_per_unit: number;
}

export interface ShapeLengthPolicy {
    mode: 'fixed' | 'uniform_integer_range' | 'deterministic_range';
    min: number;
    max: number;
}

export interface ShapeLaunchRequest {
    client_request_id: string;
    name: string;
    geometry_id: string;
    expected_geometry_sha256: string;
    expected_geometry_manifest_sha256: string;
    expected_point_pool_sha256: string;
    target_length?: number;
    length_policy?: ShapeLengthPolicy;
    num_backbones: number;
    sequences_per_backbone: number;
    seed: number;
    sequence_policy?: 'auto' | 'skip' | 'external';
    sequence_engine?: 'proteinmpnn' | 'fampnn';
    validator_suite?: Array<'boltz2' | 'esmfold2' | 'protenix_v2'>;
    guidance_profile: 'rfd3_unguided_control_v1' | 'rfd3_ca_shape_transfer_control_v1';
}

export const listShapeGeometries = () =>
    api.get<{ geometries: ShapeGeometrySummary[] }>('/api/shape-blueprint/geometries');

export const uploadShapeGeometry = (file: File, unit: string) => {
    const body = new FormData();
    body.append('file', file);
    body.append('unit', unit);
    return api.post<ShapeGeometrySummary>('/api/shape-blueprint/geometries', body);
};

export const submitShapeBlueprint = (request: ShapeLaunchRequest) =>
    api.post<{ request_id: string; request_sha256: string; job_id: string; job_status: string; reused: boolean }>(
        '/api/shape-blueprint/requests',
        request,
    );

export interface BoltzApiStructureRequest {
    name: string;
    client_request_id: string;
    model: 'boltz-2.1';
    sequence: string;
    primary_chain_id: string;
    complex_components: Array<Record<string, unknown>>;
    num_samples: number;
    use_msa: boolean;
}

export interface BoltzApiEstimateResponse {
    model: string;
    provider_input: Record<string, unknown>;
    estimate: Record<string, unknown>;
    estimate_fingerprint: string;
}

export type BoltzApiEntityType = 'protein' | 'dna' | 'rna' | 'ligand_ccd' | 'ligand_smiles';
export type BoltzApiUpdateCheckStatus =
    | 'current'
    | 'update_available'
    | 'unavailable'
    | 'unavailable_pending_official_feed_verification';

export interface BoltzApiProviderCapabilities {
    contract_version: 'bms.boltz_api.capabilities.v1';
    entities: {
        status: 'supported';
        types: BoltzApiEntityType[];
    };
    msa: {
        status: 'supported';
        provider_default: 'omit';
        disable_value: { type: 'empty' };
    };
    num_samples: {
        status: 'supported';
        minimum: number;
        maximum: number;
    };
    templates: {
        status: 'unavailable_pending_schema_verification';
    };
    unsupported_local_controls: {
        diffusion_sampling_steps: 'unsupported';
        recycling_steps: 'unsupported';
        potentials: 'unsupported';
        denoiser_chunking: 'unsupported';
        gpu_pinning: 'unsupported';
        parallelism: 'unsupported';
        oom_retry: 'unsupported';
        conditioning: 'unsupported';
    };
}

export interface BoltzApiCliUpdateStatus {
    check_status: BoltzApiUpdateCheckStatus;
    installed_version: string | null;
    latest_version: string | null;
    source: 'boltz_api_static_cli';
    release_feed_url: string | null;
    release_url: string | null;
    checked_at: string | null;
}

export interface BoltzApiProviderStatus {
    available: boolean;
    cli_available: boolean;
    credential_configured: boolean;
    model: string;
    message: string;
    capabilities: BoltzApiProviderCapabilities;
    cli_update: BoltzApiCliUpdateStatus;
}

export const fetchBoltzApiProviderStatus = () => (
    api.get<BoltzApiProviderStatus>('/api/jobs/boltz-api/status')
);

export const estimateBoltzApiJob = (payload: BoltzApiStructureRequest) => (
    api.post<BoltzApiEstimateResponse>('/api/jobs/boltz-api/estimate', payload)
);

export const submitBoltzApiJob = (
    payload: BoltzApiStructureRequest & { approved_estimate_fingerprint: string },
) => api.post<Job>('/api/jobs/boltz-api', payload);

export interface OntManagedReferenceRequest {
    global_domain_experiment_id: string;
    molbio_ngs_state_revision_id: string;
    ngs_reference_revision_id: string;
}

export interface OntNgsSubmitRequest {
    name?: string;
    params: Record<string, unknown>;
    pinned_gpu?: number | null;
    source_instrument_run_id?: string | null;
    managed_reference?: OntManagedReferenceRequest | null;
}

export const submitOntNgsJob = (workflowId: string, request: OntNgsSubmitRequest) =>
    api.post<Job>(`/api/ont/ngs/${workflowId}/submit`, request);

export interface MolBioNgsReceiptRequest {
    revision_id: string;
}

export interface MolBioNgsReceipt {
    receipt_id: string;
    sequence_id: string;
    revision_id: string;
    revision_sha256?: string;
    reference_snapshot_sha256?: string;
    expires_at?: string;
}

export const issueMolBioNgsReceipt = (
    sequenceId: string,
    request: MolBioNgsReceiptRequest,
) => api.post<MolBioNgsReceipt>(
    `/api/molbio/sequences/${encodeURIComponent(sequenceId)}/ngs-receipts`,
    request,
);

export interface OntBarcodeUnit {
    schema: 'biomodstack.ont_barcode_resubmission_unit.v1';
    unit_id: string;
    sample_alias?: string | null;
    bam_path: string;
    bam_sha256: string;
    read_count: number;
    manifest_sha256: string;
    unit_manifest_sha256: string;
}

export const fetchOntBarcodeUnits = (jobId: string) =>
    api.get<{ job_id: string; units: OntBarcodeUnit[] }>(
        `/api/jobs/${encodeURIComponent(jobId)}/barcode-units`,
    );

export interface OntBarcodeBatchMapping {
    unit_id: string;
    sample_alias: string | null;
    molbio_ngs_receipt_id: string;
}

export interface OntBarcodeBatchSubmitRequest {
    idempotency_key: string;
    target_workflow: 'ont_plasmid_qc' | 'ont_construct_screening';
    name_prefix?: string;
    pinned_gpu?: number | null;
    mappings: OntBarcodeBatchMapping[];
}

export interface OntBarcodeBatchSubmitResponse {
    reference_set_id: string;
    manifest_sha256: string;
    child_job_ids: string[];
}

export const submitOntBarcodeBatch = (
    sourceJobId: string,
    request: OntBarcodeBatchSubmitRequest,
) => api.post<OntBarcodeBatchSubmitResponse>(
    `/api/jobs/${encodeURIComponent(sourceJobId)}/barcode-batches`,
    request,
);

export interface PooledReferenceAssignmentTarget {
    target_id: string;
    label: string;
    indistinguishable_group?: string;
    molbio_ngs_receipt_id: string;
}

export interface PooledReferenceAssignmentSubmitRequest {
    idempotency_key: string;
    fastq_path: string;
    targets: PooledReferenceAssignmentTarget[];
    min_mapq: number;
    min_alignment_score_margin: number;
}

export interface PooledReferenceAssignmentSubmitResponse {
    schema: 'bms.ngs.reference-set.v1';
    assignment_job_id: string;
    reference_set_id: string;
    manifest_sha256: string;
    scientific_status: 'REVIEW';
    release_state: 'awaiting_operator_release';
}

export const submitPooledReferenceAssignment = (
    request: PooledReferenceAssignmentSubmitRequest,
) => api.post<PooledReferenceAssignmentSubmitResponse>('/api/ont/ngs/pooled-reference-assignment/submit', request);

export type PooledAssignmentTargetWorkflow = 'ont_plasmid_qc' | 'ont_construct_screening';

export interface PooledAssignmentManifest {
    schema?: string;
    mode?: string;
    assignment_job_id: string;
    reference_set_id: string;
    manifest_id: string;
    manifest_sha256: string;
    scientific_status?: 'REVIEW';
    execution_status?: string | null;
    execution?: { status?: string | null } | null;
}

export interface PooledAssignmentTarget {
    target_id: string;
    label: string;
    sequence_id: string;
    revision_id: string;
    revision_sha256: string;
    revision_digest?: string | null;
    indistinguishable_group?: string | null;
    selectable?: boolean;
    disposition?: string | null;
    status?: string | null;
}

export interface PooledAssignmentManifestResponse extends PooledAssignmentManifest {
    manifest?: PooledAssignmentManifest | null;
}

export interface PooledAssignmentTargetsResponse {
    assignment_job_id: string;
    reference_set_id: string;
    targets: PooledAssignmentTarget[];
}

export interface PooledAssignmentReleaseRequest {
    idempotency_key: string;
    target_workflow: PooledAssignmentTargetWorkflow;
    name_prefix?: string;
    pinned_gpu?: number;
    target_ids: string[];
}

export interface PooledAssignmentReleaseResponse {
    release_id: string;
    assignment_job_id: string;
    reference_set_id: string;
    child_job_ids: string[];
}

export const fetchPooledAssignmentManifest = (assignmentJobId: string) => api.get<PooledAssignmentManifestResponse>(
    `/api/jobs/${encodeURIComponent(assignmentJobId)}/pooled-assignment/manifest`,
);

export const fetchPooledAssignmentTargets = (assignmentJobId: string) => api.get<PooledAssignmentTargetsResponse>(
    `/api/jobs/${encodeURIComponent(assignmentJobId)}/pooled-assignment/targets`,
);

export const releasePooledAssignment = (
    assignmentJobId: string,
    request: PooledAssignmentReleaseRequest,
) => api.post<PooledAssignmentReleaseResponse>(
    `/api/jobs/${encodeURIComponent(assignmentJobId)}/pooled-assignment/release`,
    request,
);

export interface BoltzGenPreviewResponse {
    yaml_text: string;
    scaffold_specs: Array<Record<string, UntypedApiValue>>;
    resolved_params: Record<string, UntypedApiValue>;
    notes: string[];
    check_ok: boolean;
    check_stdout?: string | null;
    check_stderr?: string | null;
}

export const previewBoltzGenDesignSpec = (payload: {
    params: Record<string, UntypedApiValue>;
    validate?: boolean;
}) => api.post<BoltzGenPreviewResponse>('/api/boltzgen/preview', payload);

export type AntibodyIterationAction =
    | 'validate_boltz2'
    | 'validate_protenix'
    | 'ppiflow_backbone_refine'
    | 'ppiflow_maturation'
    | 'fampnn_redesign'
    | 'frustrampnn'
    | 'cdr_indel_round'
    | 'mutation_seeded_refinement'
    | 'ui_refinement';

export interface AntibodyCdrIndelConfig {
    loop_ids: string[];
    variants_per_design: number;
    allow_insertions: boolean;
    allow_deletions: boolean;
    indel_sizes: number[];
    indel_probability: number;
    allowed_aas?: string[];
    blocked_aas?: string[];
    predictor: 'boltz2' | 'protenix';
    msa_provider: 'local' | 'colabfold_api';
}

export interface LaunchAntibodyIterationRequest {
    source_job_id: string;
    design_ids: string[];
    review_filter_set_id?: string;
    action: AntibodyIterationAction;
    name_suffix?: string;
    param_overrides?: Record<string, unknown>;
    frustrampnn_settings?: import('../components/frustrampnn/frustraMpnnSettingsState.js').FrustraMpnnRequestedSettings;
    cdr_indel_config?: AntibodyCdrIndelConfig;
    manual_mutagenesis_config?: ManualMutagenesisConfig;
}

export interface LaunchAntibodyIterationResponse {
    message: string;
    action: AntibodyIterationAction;
    source_job_id: string;
    root_job_id: string;
    selection_dir: string;
    selected_design_count: number;
    launched_job: Job;
    launched_jobs: Job[];
    fanout_id?: string | null;
}

export const launchAntibodyIteration = (request: LaunchAntibodyIterationRequest) =>
    api.post<LaunchAntibodyIterationResponse>('/api/jobs/antibody-iteration/from-designs', request);

export interface ManualMutagenesisConfig {
    chain_id?: string;
    mutation_sets: string[];
    predictor: 'boltz2' | 'protenix';
    msa_provider: 'local' | 'colabfold_api';
}

export interface LaunchManualMutagenesisRequest {
    source_job_id: string;
    design_ids: string[];
    review_filter_set_id?: string;
    config: ManualMutagenesisConfig;
    name_suffix?: string;
    param_overrides?: Record<string, unknown>;
}

export interface LaunchManualMutagenesisResponse {
    message: string;
    source_job_id: string;
    selected_design_count: number;
    variant_count: number;
    launched_job: Job;
}

export const launchManualMutagenesis = (request: LaunchManualMutagenesisRequest) =>
    api.post<LaunchManualMutagenesisResponse>('/api/jobs/mutagenesis/from-designs', request);

export interface SavedReviewFilterSet {
    id: string;
    name: string;
    created_at: string;
    visible_count?: number | null;
    source_total_count?: number | null;
    design_ids?: string[];
    filter_state: Record<string, unknown>;
}

export interface SaveReviewFilterSetRequest {
    name?: string;
    visible_count?: number | null;
    source_total_count?: number | null;
    design_ids?: string[];
    filter_state: Record<string, unknown>;
}

export interface SaveReviewFilterSetResponse {
    message: string;
    filter_set: SavedReviewFilterSet;
    filter_sets: SavedReviewFilterSet[];
}

export interface DeleteReviewFilterSetResponse {
    message: string;
    filter_sets: SavedReviewFilterSet[];
}

export const saveReviewFilterSet = (jobId: string, request: SaveReviewFilterSetRequest) =>
    api.post<SaveReviewFilterSetResponse>(`/api/jobs/${jobId}/review-filter-sets`, request);

export const deleteReviewFilterSet = (jobId: string, filterSetId: string) =>
    api.delete<DeleteReviewFilterSetResponse>(`/api/jobs/${jobId}/review-filter-sets/${filterSetId}`);

export interface MsaCacheEntry {
    name: string;
    profile: string;
    path: string;
    size_bytes: number;
    modified_at: string;
    depth: number | null;
    canonical: boolean;
}

export interface MsaCacheInfo {
    sequence_hash: string;
    cache_dir: string;
    canonical_exists: boolean;
    canonical_path: string | null;
    cache_entries: number;
    best_depth: number | null;
    entries: MsaCacheEntry[];
}

export const fetchMsaCacheInfo = (sequence: string) =>
    api.get<MsaCacheInfo>('/api/msa/cache-info', { params: { sequence } });

export type SequenceQcArtifactState =
    | 'present'
    | 'not_requested'
    | 'not_applicable_to_input_mode'
    | 'failed'
    | 'missing_after_workflow'
    | 'legacy_unavailable'
    | 'missing_optional'
    | 'missing_required';

export interface SequenceQcArtifact {
    kind: string;
    path: string | null;
    declared_path?: string | null;
    required: boolean;
    exists?: boolean;
    size_bytes?: number | null;
    schema?: string;
    state?: SequenceQcArtifactState;
    missing_reason?: string | null;
    unavailable_reason?: string | null;
    source_stage?: string | null;
    declared_sha256?: string | null;
    actual_sha256?: string | null;
    integrity_valid?: boolean;
    declared_size_bytes?: number | null;
    semantic_validation?: SequenceQcSemanticValidation;
    [key: string]: unknown;
}

export interface SequenceQcPathSection {
    name?: string | null;
    path?: string | null;
    declared_path?: string | null;
    exists?: boolean;
    size_bytes?: number | null;
    length?: number | null;
    status?: string | null;
    method?: string | null;
    fallback?: boolean;
    state?: SequenceQcArtifactState;
    missing_reason?: string | null;
    unavailable_reason?: string | null;
    [key: string]: unknown;
}

export type ConstructVerificationVerdict = 'PASS' | 'FAIL' | 'REVIEW';
export type ConstructVerificationCheckStatus = 'pass' | 'fail' | 'review' | 'not_evaluated';

export interface ConstructVerificationCheck {
    status: ConstructVerificationCheckStatus;
    reason_codes: string[];
    metrics: Record<string, unknown>;
}

export interface ConstructVerificationVariant {
    id?: string;
    kind?: 'SNV' | 'INS' | 'DEL' | 'MNV' | 'COMPLEX';
    position_1based?: number;
    end_1based?: number;
    ref?: string;
    alt?: string;
    support_status?: 'supported' | 'ambiguous' | 'unsupported' | 'not_evaluated';
    depth?: number | null;
    support_fraction?: number | null;
    circular_event_id?: string | null;
    [key: string]: unknown;
}

export interface SequenceQcSemanticValidation {
    status?: 'valid' | 'invalid' | 'unavailable' | 'not_applicable' | string;
    validator?: string;
    reason?: string | null;
}

export interface ConstructVerificationInputEvidence {
    state?: string;
    role?: string;
    declared_path?: string | null;
    sha256?: string | null;
    size_bytes?: number | null;
    source_kind?: string | null;
    independent_from_expected?: boolean | null;
    reason?: string | null;
    semantic_validation?: SequenceQcSemanticValidation;
    normalized_sequence_sha256?: string | null;
    declared_sequence_sha256?: string | null;
    [key: string]: unknown;
}

export interface ConstructVerificationThresholdProfile {
    id?: string;
    version?: string;
    sha256?: string;
    calibration_status?: 'experimental' | 'calibrated';
    public_accuracy_validated?: boolean;
    values?: Record<string, unknown>;
    [key: string]: unknown;
}

export interface SequenceQcManifest {
    artifact_schema_version: number;
    schema?: string;
    job_id: string;
    sample_name?: string | null;
    execution?: {
        status?: string;
        exit_code?: number;
        reason_codes?: string[];
        [key: string]: unknown;
    };
    verdict?: ConstructVerificationVerdict;
    reason_codes?: string[];
    threshold_profile?: ConstructVerificationThresholdProfile;
    inputs?: Partial<Record<'reference' | 'observed' | 'support' | 'alignment' | 'alignment_index' | 'topology', ConstructVerificationInputEvidence>>;
    checks?: Record<string, ConstructVerificationCheck>;
    variants?: ConstructVerificationVariant[];
    summary?: Record<string, unknown>;
    provenance?: Record<string, unknown>;
    reference?: SequenceQcPathSection;
    consensus?: SequenceQcPathSection;
    artifacts: SequenceQcArtifact[];
    interpretation?: {
        verified_construct_status?: string;
        notes?: string[];
        [key: string]: unknown;
    };
    [key: string]: unknown;
}

export const fetchSequenceQcManifest = (jobId: string) =>
    api.get<SequenceQcManifest>(`/api/jobs/${encodeURIComponent(jobId)}/sequence-qc-manifest`);


// Get job logs with parsed errors
export const fetchJobLogs = (jobId: string): Promise<{ data: JobLogs }> => {
    return api.get<JobLogs>(`/api/jobs/${jobId}/logs`);
};

// Get job stages for progress display
export const fetchJobStages = (jobId: string) => {
    return api.get<{
        job_id: string;
        mode: string;
        all_stages: string[];
        current_stage: string | null;
        completed_stages: string[];
        stage_outputs: Record<string, string[]>;
        can_resume: boolean;
    }>(`/api/jobs/${jobId}/stages`);
};

// Resume a failed job from checkpoint
export const resumeJob = (
    jobId: string,
    fromStage?: string,
    paramOverrides?: Record<string, unknown>,
    nameSuffix?: string
) => {
    const hasOverrides = !!paramOverrides && Object.keys(paramOverrides).length > 0;
    const hasNameSuffix = !!nameSuffix && nameSuffix.trim().length > 0;
    const requestBody = (hasOverrides || hasNameSuffix)
        ? {
            ...(hasOverrides ? { param_overrides: paramOverrides } : {}),
            ...(hasNameSuffix ? { name_suffix: nameSuffix } : {}),
        }
        : null;

    return api.post<{
        message: string;
        original_job_id: string;
        new_job_id: string;
        new_job_name: string;
        resume_from_stage: string;
        resume_stage_mode?: string;
        resume_stage_note?: string;
        preserved_stages: string[];
        applied_overrides?: string[];
    }>(`/api/jobs/${jobId}/resume`, requestBody, { params: { from_stage: fromStage } });
};

export const continueProteinLocalReview = (
    jobId: string,
    designIds: string[],
    nameSuffix?: string,
) => {
    const requestBody = {
        design_ids: designIds,
        ...(nameSuffix && nameSuffix.trim() ? { name_suffix: nameSuffix } : {}),
    };
    return api.post<{
        message: string;
        original_job_id: string;
        new_job_id: string;
        new_job_name: string;
        resume_from_stage: string;
        resume_stage_mode?: string;
        resume_stage_note?: string;
        preserved_stages: string[];
        applied_overrides?: string[];
    }>(`/api/jobs/${jobId}/continue-protein-local-review`, requestBody);
};

// Models API
export interface ModelWorkflowIntegration {
    default_enabled: boolean;
    enabled_summary: string;
}

export interface ModelIntegrationConfig {
    model_id: string;
    model_name: string;
    model_version: string;
    stage_parameter: string;
    operator_label: string;
    checkpoint_label: string | null;
    model_summary: string;
    semantic_roles: string[];
    workflows: Record<string, ModelWorkflowIntegration>;
}

export const fetchModels = (category?: string) =>
    api.get<UntypedApiValue[]>('/api/models', { params: { category } });

export const fetchModelById = (id: string) =>
    api.get<UntypedApiValue>(`/api/models/${id}`);

export const fetchModelIntegration = (id: string) =>
    api.get<ModelIntegrationConfig>(`/api/models/${id}/integration`);

// Files API
export const fetchFiles = (path: string = '/') =>
    api.get<UntypedApiValue>('/api/files/browse', { params: { path } });

// Templates API
export const fetchTemplates = () =>
    api.get<UntypedApiValue[]>('/api/templates');

export const fetchTemplateById = (id: string) =>
    api.get<UntypedApiValue>(`/api/templates/${id}`);

// Inputs Presets API
export const fetchInputPresets = (type: string) =>
    api.get<UntypedApiValue[]>('/api/inputs/presets', { params: { type } });

export const fetchStandardPaths = () =>
    api.get<UntypedApiValue>('/api/inputs/paths');

// Directory presets for batch processing
export const fetchPresetDirectories = () =>
    api.get<UntypedApiValue[]>('/api/inputs/preset-directories');

// Designs API
export type RfScreeningScope = 'cdr_loops' | 'whole_antibody';

export interface RfScopeHeadlineMetrics {
    epitope_contact_count?: number | null;
    epitope_min_distance?: number | null;
    epitope_min_atom_distance?: number | null;
    epitope_centroid_distance?: number | null;
    target_contact_count?: number | null;
    target_min_distance?: number | null;
    target_min_atom_distance?: number | null;
    target_centroid_distance?: number | null;
}

export interface RfLoopScreeningSummary {
    requested_scope?: RfScreeningScope | string | null;
    effective_scope?: RfScreeningScope | string | null;
    fallback_reason?: string | null;
    headline_metrics_by_scope?: Partial<Record<RfScreeningScope, RfScopeHeadlineMetrics>> | null;
    redesign_candidate_loops?: string[] | null;
    engaged_loops?: string[] | null;
    detached_loops?: string[] | null;
}

export interface RfLoopMetric {
    residue_count?: number | null;
    epitope_contact_count?: number | null;
    epitope_min_distance?: number | null;
    epitope_min_atom_distance?: number | null;
    epitope_centroid_distance?: number | null;
    epitope_nearest_antibody_residue?: string | null;
    epitope_nearest_target_residue?: string | null;
    target_contact_count?: number | null;
    target_min_distance?: number | null;
    target_min_atom_distance?: number | null;
    target_centroid_distance?: number | null;
    target_nearest_antibody_residue?: string | null;
    target_nearest_target_residue?: string | null;
    engaged_epitope?: boolean | null;
    engaged_target?: boolean | null;
    redesign_candidate?: boolean | null;
    screening_status?: string | null;
    passes_reference_filters?: boolean | null;
    screening_note?: string | null;
}

export type RfLoopMetrics = Record<string, RfLoopMetric | RfLoopScreeningSummary | unknown> & {
    _screening?: RfLoopScreeningSummary;
};

export interface RfaModifiableResidue {
    chain_id: string;
    residue_number: number;
    insertion_code?: string | null;
    loop_id?: string | null;
    label?: string | null;
}

export interface RfaModifiableRange {
    chain_id: string;
    start_residue_number: number;
    end_residue_number: number;
    label?: string | null;
}

export interface RfaConfidenceScope {
    metric_family?: string | null;
    primary_scope: 'modifiable_residues' | 'all_residues' | 'unknown' | string;
    source?: string | null;
    modifiable_residues?: RfaModifiableResidue[] | null;
    modifiable_ranges?: RfaModifiableRange[] | null;
    counts?: Record<string, number> | null;
    plddt?: Record<string, number | null> | null;
    status?: string | null;
}

export interface DesignFrustraMPNNCanonicalProjection {
    contract_version?: string | null;
    status?: string | null;
    source_sha256?: string | null;
    manifest_relpath?: string | null;
    landscape_relpath?: string | null;
    summary_relpath?: string | null;
    runtime_sha256?: string | null;
    failure_class?: string | null;
    failure_detail?: string | null;
}

export interface DesignFrustraMPNNLegacySummaryProjection {
    high_count?: number | null;
    min_count?: number | null;
    pct_high?: number | null;
    csv_relpath?: string | null;
}

export interface DesignFrustraMPNNProjection {
    authority: 'canonical' | 'legacy_summary';
    canonical?: DesignFrustraMPNNCanonicalProjection | null;
    legacy_summary?: DesignFrustraMPNNLegacySummaryProjection | null;
}

export interface Design {
    id: string;
    job_id: string;
    name: string;
    pdb_path: string | null;
    num_helices: number | null;
    num_strands: number | null;
    rog: number | null;
    rfd_rog: number | null;
    mpnn_score: number | null;
    fampnn_psce: number | null;
    fampnn_max_residue_psce?: number | null;
    fampnn_min_residue_psce?: number | null;
    plddt_overall: number | null;
    plddt_binder: number | null;
    plddt_target: number | null;
    pae_overall: number | null;
    pae_interaction: number | null;
    ptm: number | null;
    conf_score: number | null;
    rmsd_overall: number | null;
    rmsd_binder: number | null;
    rmsd_target?: number | null;
    ligand_iptm: number | null;
    affinity_score: number | null;
    binder_probability: number | null;
    // Interface metrics (complexes)
    iptm: number | null;
    protein_iptm: number | null;
    complex_iplddt: number | null;
    complex_ipde: number | null;
    disorder: number | null;
    num_recycles: number | null;
    has_clash: boolean | null;
    chains_ptm: Record<string, number> | number[] | null;
    pair_chains_iptm: Record<string, Record<string, number>> | number[][] | null;
    confidence_metrics: Record<string, unknown> | null;
    metric_provenance?: Record<string, unknown> | null;
    metric_completeness?: {
        overall_status?: string;
        status?: string;
        missing?: string[];
        [key: string]: unknown;
    } | null;
    ipsae: number | null;
    // Backbone grouping & epitope analysis
    backbone_id: number | null;
    epitope_contact_count: number | null;
    epitope_min_distance: number | null;
    epitope_min_atom_distance?: number | null;
    epitope_nearest_antibody_residue?: string | null;
    epitope_nearest_target_residue?: string | null;
    epitope_nearest_antibody_atom?: string | null;
    epitope_nearest_target_atom?: string | null;
    epitope_mapping_mode?: string | null;
    epitope_centroid_distance?: number | null;
    target_contact_count?: number | null;
    target_min_distance?: number | null;
    target_min_atom_distance?: number | null;
    target_nearest_antibody_residue?: string | null;
    target_nearest_target_residue?: string | null;
    target_nearest_antibody_atom?: string | null;
    target_nearest_target_atom?: string | null;
    target_centroid_distance?: number | null;
    detected_antibody_chains?: string | null;
    detected_target_chain?: string | null;
    antibody_residue_count?: number | null;
    target_residue_count?: number | null;
    epitope_residue_count?: number | null;
    passed_screen?: boolean | null;
    screening_reason?: string | null;
    source_stage?: string | null;
    artifact_group?: string | null;
    stage_family?: string | null;
    stage_mode?: string | null;
    source_stage_job_id?: string | null;
    source_stage_family?: string | null;
    source_stage_mode?: string | null;
    source_pdb_path?: string | null;
    source_design_name?: string | null;
    artifact_class?: string | null;
    artifact_schema_version?: number | null;
    review_profile_id?: string | null;
    review_contract_version?: number | null;
    review_contract_source?: string | null;
    review_artifact_manifest?: {
        schema?: string;
        artifacts?: Record<string, { kind?: string; state?: 'ready' | 'missing' | 'invalid'; path?: string | null; reason?: string | null }>;
        roles?: Record<string, unknown> & { has_binder?: boolean };
    } | null;
    review_role_map?: Record<string, unknown> | null;
    result_set?: string | null;
    result_set_label?: string | null;
    analysis_contract_id?: string | null;
    supported_analyzers?: string[];
    viewer_capabilities?: string[];
    required_fields?: string[];
    required_artifacts?: string[];
    result_contract_schema_version?: number | null;
    result_contract_source?: string | null;
    lineage_root_job_id?: string | null;
    parent_design_id?: string | null;
    origin_design_id?: string | null;
    origin_job_id?: string | null;
    origin_backbone_design_id?: string | null;
    provenance?: Record<string, unknown> | null;
    is_imported?: boolean;
    import_source?: string | null;
    import_method?: string | null;
    import_label?: string | null;
    rfa_loop_metrics?: RfLoopMetrics | null;
    rfa_hotspot_metrics?: Record<string, unknown> | null;
    rfa_hotspot_covered_count?: number | null;
    rfa_hotspot_min_distance?: number | null;
    rfa_hotspot_avg_min_distance?: number | null;
    rfa_runtime_seconds?: number | null;
    rfa_device?: string | null;
    rfa_diffusion_steps?: number | null;
    rfa_noise_scale_ca?: number | null;
    rfa_noise_scale_frame?: number | null;
    rfa_guide_scale?: number | null;
    rfa_plddt_initial?: number | null;
    rfa_plddt_final?: number | null;
    rfa_plddt_delta?: number | null;
    rfa_plddt_selected?: number | null;
    rfa_plddt_nonselected?: number | null;
    rfa_plddt_primary?: number | null;
    rfa_plddt_modifiable?: number | null;
    rfa_plddt_all_residue?: number | null;
    rfa_plddt_nonmodifiable?: number | null;
    rfa_plddt_framework?: number | null;
    rfa_plddt_target?: number | null;
    rfa_modifiable_residues?: RfaModifiableResidue[] | null;
    rfa_modifiable_ranges?: RfaModifiableRange[] | null;
    rfa_confidence_scope?: RfaConfidenceScope | null;
    rfa_design_loops?: string[] | null;
    rfa_hotspots?: string[] | null;
    // Antibody annotation
    binder_length: number | null;
    binder_sequence?: string | null;
    antibody_type: string | null;
    cdr_h1: string | null;
    cdr_h2: string | null;
    cdr_h3: string | null;
    cdr_l1: string | null;
    cdr_l2: string | null;
    cdr_l3: string | null;
    cdr_h1_length: number | null;
    cdr_h2_length: number | null;
    cdr_h3_length: number | null;
    cdr_l1_length: number | null;
    cdr_l2_length: number | null;
    cdr_l3_length: number | null;
    // Framework contact hotspots (Zavrtanik 2018)
    fr2_contacts: string | null;  // IMGT 37, 42, 44, 45, 47
    de_loop: string | null;       // IMGT 72-75
    fr3_contacts: string | null;  // IMGT 82-87
    fr4_contacts: string | null;  // IMGT 101-103
    // Frustration analysis (FrustraMPNN)
    frustration_high_count: number | null;
    frustration_min_count: number | null;
    frustration_pct_high: number | null;
    frustration_residues: Array<{ pos: number; chain: string; frust: number; frustClass: string }> | null;
    frustration_csv_path: string | null;
    frustration_csv_relpath?: string | null;
    frustrampnn?: DesignFrustraMPNNProjection | null;
    // PPIFlow maturation metrics
    maturation_delta_interface: number | null;
    maturation_interface_score: number | null;
    maturation_rmsd: number | null;
    maturation_selected_delta_interface?: number | null;
    maturation_selected_interface_score?: number | null;
    maturation_selected_rmsd?: number | null;
    maturation_nonselected_rmsd?: number | null;
    ppiflow_primary_loop?: string | null;
    ppiflow_primary_loop_rmsd?: number | null;
    ppiflow_primary_loop_target_contact_delta?: number | null;
    ppiflow_primary_loop_target_distance_delta?: number | null;
    ppiflow_primary_loop_epitope_contact_delta?: number | null;
    ppiflow_primary_loop_epitope_distance_delta?: number | null;
    ppiflow_objective_mode?: string | null;
    ppiflow_objective_score?: number | null;
    ppiflow_filter_passed?: boolean | null;
    ppiflow_filter_reason?: string | null;
    ppiflow_loop_metrics?: Record<string, unknown> | null;
    rosetta_interface_score?: number | null;
    rosetta_interface_dg?: number | null;
    rosetta_interface_dsasa?: number | null;
    rosetta_interface_packstat?: number | null;
    rosetta_interface_shape_complementarity?: number | null;
    rosetta_interface_hbond_count?: number | null;
    rosetta_interface_id?: string | null;
    rosetta_interface_score_unit?: string | null;
    rosetta_interface_score_direction?: string | null;
    rosetta_interface_analyzer_used?: boolean | null;
    rosetta_interface_warning?: string | null;
    is_favorite: boolean;
    notes: string | null;
    created_at: string;
}

export interface DesignAggregateSummary {
    total: number;
    favorites: number;
    avg_plddt: number | null;
    avg_pae: number | null;
    avg_ptm: number | null;
    avg_iptm: number | null;
    avg_ipsae: number | null;
    avg_affinity: number | null;
    avg_binder_probability: number | null;
    avg_epitope_contacts: number | null;
    avg_target_contacts: number | null;
    avg_epitope_distance: number | null;
    avg_target_distance: number | null;
    avg_hotspot_coverage: number | null;
    avg_psce: number | null;
    high_confidence: number;
    low_error: number;
    high_contacts: number;
    screen_passed: number;
    screen_failed: number;
}

export interface DesignListResponse {
    designs: Design[];
    total: number;
    summary?: DesignAggregateSummary | null;
}

export interface DesignFilters {
    job_id?: string;
    include_children?: boolean;
    design_ids?: string[];
    q?: string;
    backbone_id?: number;
    plddt_min?: number;
    pae_max?: number;
    iptm_min?: number;
    ipsae_min?: number;
    epitope_contacts_min?: number;
    target_contacts_min?: number;
    epitope_max_dist?: number;
    target_max_dist?: number;
    binder_length_min?: number;
    binder_length_max?: number;
    cdr_h1_min?: number;
    cdr_h1_max?: number;
    cdr_h2_min?: number;
    cdr_h2_max?: number;
    cdr_h3_min?: number;
    cdr_h3_max?: number;
    rog_min?: number;
    rog_max?: number;
    rfd_rog_min?: number;
    rfd_rog_max?: number;
    favorites_only?: boolean;
    artifact_group?: string;
    artifact_class?: string;
    stage_family?: string;
    source_stage_family?: string;
    sort_by?: DesignSortField;
    sort_desc?: boolean;
    limit?: number;
    offset?: number;
    include_summary?: boolean;
}

export type DesignSortField =
    | 'name'
    | 'plddt'
    | 'plddt_overall'
    | 'plddt_binder'
    | 'plddt_target'
    | 'iptm'
    | 'ptm'
    | 'pae'
    | 'pae_overall'
    | 'pae_interaction'
    | 'conf_score'
    | 'ligand_iptm'
    | 'rmsd_binder'
    | 'rmsd_overall'
    | 'rmsd_target'
    | 'has_clash'
    | 'rog'
    | 'rfd_rog'
    | 'backbone'
    | 'backbone_id'
    | 'binder_length'
    | 'cdr_h1_length'
    | 'cdr_h2_length'
    | 'cdr_h3_length'
    | 'epitope_contact_count'
    | 'target_contact_count'
    | 'epitope_min_distance'
    | 'target_min_distance'
    | 'epitope_min_atom_distance'
    | 'target_min_atom_distance'
    | 'epitope_centroid_distance'
    | 'target_centroid_distance'
    | 'affinity_score'
    | 'binder_probability'
    | 'fampnn_psce'
    | 'ipsae'
    | 'rfa_hotspot_covered_count'
    | 'rfa_hotspot_min_distance'
    | 'rfa_hotspot_avg_min_distance'
    | 'rfa_runtime_seconds'
    | 'rfa_plddt_final'
    | 'rfa_plddt_selected'
    | 'rfa_plddt_delta'
    | 'frustration_high_count'
    | 'frustration_pct_high'
    | 'maturation_delta_interface'
    | 'maturation_interface_score'
    | 'maturation_rmsd'
    | 'maturation_selected_delta_interface'
    | 'maturation_selected_interface_score'
    | 'maturation_selected_rmsd'
    | 'maturation_nonselected_rmsd'
    | 'ppiflow_primary_loop'
    | 'ppiflow_primary_loop_rmsd'
    | 'ppiflow_primary_loop_target_contact_delta'
    | 'ppiflow_primary_loop_target_distance_delta'
    | 'ppiflow_primary_loop_epitope_contact_delta'
    | 'ppiflow_primary_loop_epitope_distance_delta'
    | 'ppiflow_objective_score'
    | 'fr2_contacts'
    | 'binding_tier'
    | 'is_favorite';

export const buildFileDownloadUrl = (relativePath: string) =>
    `/api/files/download/${encodeURIComponent(relativePath)}`;

export const buildFileStreamUrl = (relativePath: string) =>
    `/api/files/stream/${encodeURIComponent(relativePath)}`;

export interface BackboneSummary {
    job_id: string;
    total: number;
    assigned_total?: number;
    unassigned_total?: number;
    backbones: Record<number, {
        count: number;
        avg_plddt: number | null;
        max_plddt?: number | null;
        avg_iptm: number | null;
        avg_ptm: number | null;
        min_pae: number | null;
        avg_target_contacts?: number | null;
        min_target_contacts?: number | null;
        max_target_contacts?: number | null;
        avg_epitope_contacts?: number | null;
        min_epitope_contacts?: number | null;
        max_epitope_contacts?: number | null;
        avg_target_distance?: number | null;
        min_target_distance?: number | null;
        max_target_distance?: number | null;
        avg_epitope_distance?: number | null;
        min_epitope_distance?: number | null;
        max_epitope_distance?: number | null;
        avg_cdr_h1_length?: number | null;
        avg_cdr_h2_length?: number | null;
        avg_cdr_h3_length?: number | null;
        representative?: {
            id: string;
            name: string;
            pdb_path: string | null;
            plddt_overall: number | null;
            epitope_contact_count: number | null;
            epitope_min_distance: number | null;
            target_contact_count?: number | null;
            target_min_distance?: number | null;
            rfa_hotspot_covered_count?: number | null;
        } | null;
    }>;
}

export const fetchDesigns = (filters: DesignFilters = {}) =>
    filters.design_ids?.length
        ? api.post<DesignListResponse>('/api/designs/query', filters)
        : api.get<DesignListResponse>('/api/designs', { params: filters });

export const fetchBackboneSummary = (jobId: string, artifactGroup?: string) =>
    api.get<BackboneSummary>(`/api/designs/by-job/${jobId}/backbone-summary`, {
        params: artifactGroup ? { artifact_group: artifactGroup } : undefined,
    });

export const toggleDesignFavorite = (designId: string, isFavorite: boolean) =>
    api.post(`/api/designs/${designId}/favorite`, { is_favorite: isFavorite });

export const downloadDesignPdb = (designId: string) =>
    `/api/designs/${designId}/pdb`;

// Per-residue metrics for charts
export interface ResidueMetrics {
    design_id: string;
    design_name: string;
    residue_numbers: number[];
    plddt: number[];
    length: number;
}

// Fetch per-residue metrics for a design
export const fetchDesignResidueMetrics = (designId: string) =>
    api.get<ResidueMetrics>(`/api/designs/${designId}/residue-metrics`);

export interface ChainMetric {
    type: 'protein' | 'dna' | 'rna' | 'ligand' | 'unknown';
    length: number;
    avg_plddt: number | null;
    plddt: number[];
    residue_numbers: number[];
}

export interface FampnnPsceChainMetric {
    type: 'protein';
    length: number;
    avg_psce: number | null;
    max_psce: number | null;
    min_psce: number | null;
    residue_numbers: number[];
    residue_names: string[];
    psce: number[];
}

export interface FampnnPsceProfile {
    design_id: string;
    design_name: string;
    metric_kind: 'fampnn_psce';
    direction: 'lower_is_better';
    scope: 'all_chains';
    ignore_cbeta: boolean;
    chains: Record<string, FampnnPsceChainMetric>;
}

// Fetch per-chain metrics for a design
export const fetchChainMetrics = (designId: string) =>
    api.get<Record<string, ChainMetric>>(`/api/designs/${designId}/chain-metrics`);

// Power control (eco mode + manual)
export const fetchPowerProfile = () =>
    api.get<PowerProfile>('/api/gpu/power-profile');

export const setPowerProfile = (enableEco: boolean) =>
    api.post<PowerProfile>('/api/gpu/power-profile', null, {
        params: { enable_eco: enableEco }
    });

export const fetchPowerControl = () =>
    api.get<PowerControlState>('/api/gpu/power-control');

export const setPowerControlPreset = (preset: 'eco' | 'stock') =>
    api.post<PowerControlResponse>('/api/gpu/power-control', { preset });

export const setPowerControlManual = (gpuIndex: number, limitWatts: number) =>
    api.post<PowerControlResponse>('/api/gpu/power-control', {
        gpu_index: gpuIndex,
        limit_watts: limitWatts
    });

export const fetchFanControl = () =>
    api.get<FanControlState>('/api/gpu/fan-control');

export const setFanControl = (
    gpuIndex: number,
    mode: 'auto' | 'manual',
    targetPercent?: number
) =>
    api.post<FanControlResponse>('/api/gpu/fan-control', {
        gpu_index: gpuIndex,
        mode,
        ...(targetPercent != null ? { target_percent: targetPercent } : {}),
    });

export const discoverHardware = () =>
    api.post<HardwareDiscoveryResponse>('/api/gpu/hardware/discover');

// Analytics API
export interface MetricDistribution {
    min: number;
    max: number;
    avg: number;
    median: number;
    std_dev: number;
    histogram_bins: number[];
    histogram_counts: number[];
}

export interface JobAnalytics {
    job_id: string;
    design_count: number;
    metrics: Record<string, MetricDistribution | null>;
    correlations: Record<string, Array<{ x: number; y: number; id: string }>> | null;
    pipeline_summary: Record<string, UntypedApiValue>;
}

export interface DesignMetricPoint {
    id: string;
    name: string;
    metrics: Record<string, number>;
}

export interface BatchAnalytics {
    job_ids: string[];
    metrics_summary: Record<string, Record<string, number>>; // metric -> {job_id -> avg}
    common_metrics: string[];
}

export const fetchJobAnalytics = (jobId: string) =>
    api.get<JobAnalytics>(`/api/analytics/job/${jobId}`);

export const fetchJobDesignMetrics = (jobId: string) =>
    api.get<DesignMetricPoint[]>(`/api/analytics/job/${jobId}/designs`);

export const fetchBatchAnalytics = (jobIds: string[]) =>
    api.post<BatchAnalytics>('/api/analytics/batch', { job_ids: jobIds });

// Structure Analysis (Biotite-powered)
export interface StructureAnalysis {
    design_id: string;
    design_name: string;
    residue_count: number;
    chain_ids: string[];
    gyration_radius: number | null;
    secondary_structure: {
        helix: number;
        sheet: number;
        coil: number;
    };
}

export interface StructureComparison {
    design_id: string;
    other_design_id: string;
    rmsd_backbone: number | null;
    rmsd_all_atom: number | null;
}

export interface IpsaeInterfacePairScore {
    chain_1: string;
    chain_2: string;
    pair_type: string;
    ipsae_d0res_asym: number | null;
    ipsae_d0chn_asym: number | null;
    ipsae_d0dom_asym: number | null;
    ipsae_d0res_max: number | null;
    ipsae_d0chn_max: number | null;
    ipsae_d0dom_max: number | null;
    iptm_d0chn_asym: number | null;
    iptm_d0chn_max: number | null;
    n0chn: number | null;
    n0dom: number | null;
    n0dom_max: number | null;
    n0res: number | null;
    n0res_max: number | null;
    d0chn: number | null;
    d0dom: number | null;
    d0dom_max: number | null;
    d0res: number | null;
    d0res_max: number | null;
    residue_label_iptm_asym: string | null;
    residue_label_ipsae_d0chn_asym: string | null;
    residue_label_ipsae_d0dom_asym: string | null;
    residue_label_ipsae_d0res_asym: string | null;
    residue_label_ipsae_d0res_max: string | null;
    interface_residue_count_chain_1: number | null;
    interface_residue_count_chain_2: number | null;
    interface_dist_residue_count_chain_1: number | null;
    interface_dist_residue_count_chain_2: number | null;
    valid_pair_count: number | null;
    dist_valid_pair_count: number | null;
}

export interface IpsaeInterfaceAnalysis {
    ipsae: number | null;
    ipsae_binder_to_target: number | null;
    ipsae_target_to_binder: number | null;
    ipsae_global_max: number | null;
    ipsae_d0chn: number | null;
    ipsae_d0dom: number | null;
    ipsae_chain_pair: string | null;
    ipsae_pair_type: string | null;
    ipsae_n0res: number | null;
    ipsae_n0chn: number | null;
    ipsae_n0dom: number | null;
    ipsae_selected_d0res: number | null;
    ipsae_selected_d0chn: number | null;
    ipsae_selected_d0dom: number | null;
    ipsae_selected_residue: string | null;
    pae_cutoff: number | null;
    dist_cutoff: number | null;
    pair_scores: IpsaeInterfacePairScore[];
}

export interface PersistedAnalysisRun<T = Record<string, unknown>> {
    run_id: string | null;
    analysis_type: string;
    subject_kind: string;
    subject_id: string;
    status: 'missing' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'stale';
    resource_class: string | null;
    params: Record<string, unknown>;
    cache_hit: boolean;
    summary: Record<string, unknown> | null;
    result: T | null;
    error_message: string | null;
    artifacts: Record<string, unknown>;
    queued_at: string | null;
    started_at: string | null;
    completed_at: string | null;
    last_accessed_at: string | null;
}

export interface JobAnalysisScopeParams {
    include_children?: boolean;
    design_ids?: string[];
}

export const fetchStructureAnalysis = (designId: string) =>
    api.get<StructureAnalysis>(`/api/designs/${designId}/structure-analysis`);

export const fetchStructureComparison = (id1: string, id2: string) =>
    api.get<StructureComparison>(`/api/designs/${id1}/compare/${id2}`);

export const fetchDesignAnalysis = <T = Record<string, unknown>>(
    designId: string,
    analysisType: string,
    params?: Record<string, unknown>,
) =>
    api.get<PersistedAnalysisRun<T>>(`/api/designs/${designId}/analyses/${analysisType}`, { params });

export const triggerDesignAnalysis = <T = Record<string, unknown>>(
    designId: string,
    analysisType: string,
    params?: Record<string, unknown>,
    forceRefresh: boolean = false,
) =>
    api.post<PersistedAnalysisRun<T>>(`/api/designs/${designId}/analyses/${analysisType}`, {
        params: params || {},
        force_refresh: forceRefresh,
    });

export const fetchJobAnalysis = <T = Record<string, unknown>>(
    jobId: string,
    analysisType: string,
    params?: JobAnalysisScopeParams,
) =>
    api.get<PersistedAnalysisRun<T>>(`/api/jobs/${jobId}/analyses/${analysisType}`, {
        params: {
            include_children: params?.include_children,
            design_ids: params?.design_ids?.join(','),
        },
    });

export const triggerJobAnalysis = <T = Record<string, unknown>>(
    jobId: string,
    analysisType: string,
    params?: JobAnalysisScopeParams,
    forceRefresh: boolean = false,
) =>
    api.post<PersistedAnalysisRun<T>>(`/api/jobs/${jobId}/analyses/${analysisType}`, {
        params: {
            include_children: params?.include_children ?? true,
            design_ids: params?.design_ids ?? [],
        },
        force_refresh: forceRefresh,
    });

// User Sequences API
export interface UserSequence {
    id: string;
    name: string;
    sequence: string;
    description: string | null;
    length: number;
    organism: string | null;
    uniprot_id: string | null;
    ncbi_id: string | null;
    is_preset: boolean;
    created_at: string;
    updated_at: string | null;
}

export interface UserSequenceCreate {
    name: string;
    sequence: string;
    description?: string;
    organism?: string;
    uniprot_id?: string;
    ncbi_id?: string;
}

export const fetchUserSequences = (search?: string) =>
    api.get<UserSequence[]>('/api/user-sequences', { params: { search } });

export const fetchUserSequence = (id: string) =>
    api.get<UserSequence>(`/api/user-sequences/${encodeURIComponent(id)}`);

export const createUserSequence = (data: UserSequenceCreate) =>
    api.post<UserSequence>('/api/user-sequences', data);

export const updateUserSequence = (id: string, data: Partial<UserSequenceCreate>) =>
    api.put<UserSequence>(`/api/user-sequences/${id}`, data);

export const deleteUserSequence = (id: string) =>
    api.delete(`/api/user-sequences/${id}`);

// User Templates API
export interface UserTemplate {
    id: string;
    name: string;
    description: string | null;
    icon: string;
    color: string;
    base_template_id: string | null;
    model_id: string | null;
    mode: string | null;
    params: Record<string, UntypedApiValue>;
    created_at: string;
    updated_at: string | null;
}

export interface UserTemplateCreate {
    name: string;
    description?: string;
    icon?: string;
    color?: string;
    base_template_id?: string;
    model_id?: string;
    mode?: string;
    params: Record<string, UntypedApiValue>;
}

export const fetchUserTemplates = (search?: string, model_id?: string) =>
    api.get<UserTemplate[]>('/api/user-templates', { params: { search, model_id } });

export const createUserTemplate = (data: UserTemplateCreate) =>
    api.post<UserTemplate>('/api/user-templates', data);

export const updateUserTemplate = (id: string, data: Partial<UserTemplateCreate>) =>
    api.put<UserTemplate>(`/api/user-templates/${id}`, data);

export const deleteUserTemplate = (id: string) =>
    api.delete(`/api/user-templates/${id}`);

// SMILES Converter API
export interface SmilesConvertRequest {
    sequence: string;
    sequence_type: 'peptide' | 'dna' | 'rna' | 'ntp';
}

export interface SmilesConvertResponse {
    smiles: string;
    sequence: string;
    sequence_type: string;
    length: number;
    notes?: string;
}

export const convertToSmiles = (data: SmilesConvertRequest) =>
    api.post<SmilesConvertResponse>('/api/smiles/convert', data);

export const getNtpLibrary = () =>
    api.get('/api/smiles/ntp-library');

// 3D Conformer Generation API
export interface Generate3DRequest {
    smiles: string;
    name: string;
    energy_minimize?: boolean;
    output_format?: 'pdb' | 'sdf';
}

export interface Generate3DResponse {
    success: boolean;
    pdb_block?: string;
    file_path?: string;
    smiles: string;
    name: string;
    num_atoms: number;
    energy?: number;
    error?: string;
}

export const generate3DConformer = (data: Generate3DRequest) =>
    api.post<Generate3DResponse>('/api/smiles/generate-3d', data);

export const generateNTP3D = (ntpName: string) =>
    api.get<Generate3DResponse>(`/api/smiles/generate-3d/ntp/${ntpName}`);

// ═══════════════════════════════════════════════════════════════════════════════
// GPU SCHEDULER CONFIG API
// ═══════════════════════════════════════════════════════════════════════════════

export interface SchedulerConfig {
    global: {
        busy_threshold: number;
        cooldown_ms: number;
        cpu_threads_per_job: number;
        auto_cpu_threads: boolean;
        auto_cpu_thread_job_threshold: number;
        enabled: boolean;
        target_vram_fill: number;
        vram_safety_margin_mb: number;
        capacity_weight: number;
        emptiness_weight: number;
        max_launches_per_cycle: number;
        msa_concurrency_limit: number;
        msa_preferred_gpu_ids?: number[];
        msa_avoid_heavy_gpus?: boolean;
        force_run_excluded_gpu_ids?: number[];
    };
    overrides: Record<string, {
        disabled?: boolean;
        force_available?: boolean;
        quick_enable?: boolean;
        threshold?: number | null;
        priority_tier?: number | null;
        vram_safety_margin_mb?: number | null;
        max_concurrent_jobs?: number | null;
    }>;
}

export const fetchSchedulerConfig = () =>
    api.get<SchedulerConfig>('/api/gpu/scheduler-config');

export const toggleGpuDisabled = (gpuId: number) =>
    api.post(`/api/gpu/scheduler-config/gpu/${gpuId}/toggle-disable`);

// ═══════════════════════════════════════════════════════════════════════════════
// JOB QUEUE API (GPU Orchestrator)
// ═══════════════════════════════════════════════════════════════════════════════

export interface QueuedJob {
    id: string;
    name: string;
    model_id: string;
    mode: string;
    queue_status: 'queued' | 'running' | 'paused' | 'pending_msa';
    paused: boolean;
    pinned_gpu: number | null;
    assigned_gpu: number | null;
    display_gpu_ids: number[] | null;
    priority: number;
    vram_estimate_mb: number | null;
    live_vram_mb: number | null;
    sequence_length: number | null;
    batch_id: string | null;
    batch_name: string | null;
    retry_count: number;
    max_retries: number;
    created_at: string;
    started_at: string | null;
    current_stage: string | null;  // Current workflow step (e.g., 'rfantibody', 'fampnn', 'boltz2')
    stage_progress: string | null;  // Granular progress (e.g., '5/30', 'step 500/1000')
    scheduler_required_mb: number | null;
    scheduler_candidate_gpus: number[] | null;
    scheduler_ready: boolean | null;
    scheduler_blockers: string[] | null;
}

export interface QueueStats {
    queued: number;
    running: number;
    paused: number;
    total: number;
}

export const fetchQueue = (status?: string) =>
    api.get<QueuedJob[]>('/api/queue', { params: { status } });

export const fetchQueueStats = () =>
    api.get<QueueStats>('/api/queue/stats');

export const pauseQueueJob = (jobId: string) =>
    api.post(`/api/queue/${jobId}/pause`);

export const resumeQueueJob = (jobId: string) =>
    api.post(`/api/queue/${jobId}/resume`);

export const cancelQueueJob = (jobId: string) =>
    api.delete(`/api/queue/${jobId}`);

export const pinQueueJob = (jobId: string, gpuId: number | null) =>
    api.post(`/api/queue/${jobId}/pin`, { gpu_id: gpuId });

export const setQueueJobPriority = (jobId: string, priority: number) =>
    api.post(`/api/queue/${jobId}/priority`, { priority });

export const retryQueueJob = (jobId: string) =>
    api.post(`/api/queue/${jobId}/retry`);

export const cancelAllQueuedJobs = () =>
    api.delete('/api/queue/clear-all');

export const fetchCancelledJobs = (limit: number = 20) =>
    api.get<QueuedJob[]>('/api/queue/cancelled', { params: { limit } });

export const killActiveNextflowJobs = () =>
    api.post('/api/queue/kill-active');

export const forceLaunchQueueJob = (jobId: string, gpuId: number) =>
    api.post(`/api/queue/${jobId}/force-launch`, { gpu_id: gpuId });


// ═══════════════════════════════════════════════════════════════════════════════
// NUCLEOTIDE SEQUENCES API (BioDesigner)
// ═══════════════════════════════════════════════════════════════════════════════

export interface SequenceFeature {
    id: string;
    name: string;
    type: string;
    start: number;
    end: number;
    strand: number;
    color?: string;
    description?: string;
    notes?: Record<string, UntypedApiValue>;
    qualifiers?: Record<string, UntypedApiValue>;
    provenance?: Record<string, UntypedApiValue>;
    segments?: Array<{
        start: number;
        end: number;
    }>;
}

export interface SequencePrimer {
    id: string;
    name: string;
    sequence: string;
    sequence_type?: 'dna' | 'rna';
    start: number;
    end: number;
    strand?: number;
    tm?: number;
    gc_percent?: number;
    tm_algorithm?: string;
    tm_salt_correction?: string;
    tm_settings?: PrimerTmSettings;
    notes?: Record<string, UntypedApiValue>;
    provenance?: Record<string, UntypedApiValue>;
    sites?: Array<{
        start: number;
        end: number;
        strand: number;
        tm?: number;
        note?: string;
    }>;
}

export interface SequenceAnalysisTrack {
    id: string;
    name: string;
    kind: 'reactivity' | 'coverage' | 'mismatch' | 'custom';
    description?: string | null;
    color?: string | null;
    source_format?: string | null;
    source_name?: string | null;
    source_url?: string | null;
    normalization?: string | null;
    values: Array<number | null>;
    min_value?: number | null;
    max_value?: number | null;
    created_at?: string | null;
}

export type NucleotideMoleculeStrandedness = 'single' | 'double' | 'unknown';
export type NucleotideMoleculeOrientation = 'positive' | 'negative' | 'ambisense' | 'not_applicable' | 'unknown';

export interface NucleotideSequence {
    id: string;
    name: string;
    description: string | null;
    sequence: string;
    sequence_type: 'dna' | 'rna';
    molecule_strandedness: NucleotideMoleculeStrandedness;
    molecule_orientation: NucleotideMoleculeOrientation;
    molecule_label: string;
    is_circular: boolean;
    length: number;
    features: SequenceFeature[] | null;
    primers: SequencePrimer[] | null;
    analysis_tracks?: SequenceAnalysisTrack[] | null;
    organism: string | null;
    accession: string | null;
    source_file: string | null;
    gc_content: number | null;
    parent_id?: string | null;
    operation?: string | null;
    operation_params?: Record<string, unknown> | null;
    version?: number | null;
    entity_kind?: string;
    topology?: 'circular' | 'linear';
    created_at: string;
    updated_at: string | null;
}

export interface NucleotideSequenceListItem {
    id: string;
    revision_id?: string;
    reopen_href?: string;
    name: string;
    description: string | null;
    sequence_type: 'dna' | 'rna';
    molecule_strandedness: NucleotideMoleculeStrandedness;
    molecule_orientation: NucleotideMoleculeOrientation;
    molecule_label: string;
    is_circular: boolean;
    length: number;
    gc_content: number | null;
    feature_count: number;
    organism: string | null;
    accession: string | null;
    source_file: string | null;
    entity_kind: string;
    topology: 'circular' | 'linear';
    created_at: string;
    updated_at: string | null;
}

export interface SavedGibsonWorkupListItem {
    id: string;
    name: string;
    description: string | null;
    length: number;
    topology: 'circular' | 'linear';
    engine: string | null;
    engine_version: string | null;
    fragment_count: number;
    primer_count: number;
    created_at: string;
    updated_at: string | null;
}

export interface NucleotideSequenceCreate {
    name: string;
    description?: string;
    sequence: string;
    sequence_type?: 'dna' | 'rna';
    molecule_strandedness?: NucleotideMoleculeStrandedness;
    molecule_orientation?: NucleotideMoleculeOrientation;
    is_circular?: boolean;
    features?: SequenceFeature[];
    primers?: SequencePrimer[];
    analysis_tracks?: SequenceAnalysisTrack[];
    organism?: string;
    accession?: string;
    source_file?: string;
}

export interface AssemblyFragmentEnd {
    type: 'blunt' | 'sticky_5' | 'sticky_3';
    overhang?: string;
    label?: string;
}

export interface AssemblyFragmentInput {
    id: string;
    name: string;
    sequence: string;
    orientation?: 'forward' | 'reverse';
    circular?: boolean;
    role?: string;
    source_sequence_id?: string;
    source_name?: string;
    source_revision?: number;
    source_start?: number;
    source_end?: number;
    source_wraps_origin?: boolean;
    left_end?: AssemblyFragmentEnd | null;
    right_end?: AssemblyFragmentEnd | null;
    metadata?: Record<string, unknown>;
}

export interface AssemblyFragmentResult {
    id: string;
    name: string;
    orientation: 'forward' | 'reverse' | string;
    role?: string | null;
    source_sequence_id?: string | null;
    source_name?: string | null;
    source_revision?: number | null;
    source_start?: number | null;
    source_end?: number | null;
    source_wraps_origin?: boolean;
    left_end?: AssemblyFragmentEnd | null;
    right_end?: AssemblyFragmentEnd | null;
    metadata?: Record<string, unknown> | null;
}

export interface AssemblyJunction {
    left_fragment_id: string;
    right_fragment_id: string;
    left_fragment_name: string;
    right_fragment_name: string;
    mode: 'ligation' | 'gibson' | 'golden_gate' | string;
    left_end_type?: string | null;
    right_end_type?: string | null;
    overhang_sequence?: string | null;
    overlap_sequence?: string | null;
    overlap_length: number;
    junction_sequence: string;
    validation: string;
    notes: string[];
}

export interface AssemblyProduct {
    sequence: string;
    circular: boolean;
    length: number;
    mode: 'ligation' | 'gibson' | 'golden_gate' | string;
    fragments: AssemblyFragmentResult[];
    junctions: AssemblyJunction[];
    warnings: string[];
    validation_notes: string[];
}

export interface AssemblyOperationResponse {
    product: AssemblyProduct;
    saved_sequence?: NucleotideSequence | null;
    message: string;
}

export interface LigationAssemblyRequest {
    fragments: AssemblyFragmentInput[];
    circular?: boolean;
    new_name?: string;
    save_description?: string;
}

export interface GibsonAssemblyRequest {
    fragments: AssemblyFragmentInput[];
    circular?: boolean;
    minimum_overlap?: number;
    preferred_overlap?: number | null;
    maximum_overlap?: number | null;
    new_name?: string;
    save_description?: string;
}

export interface DnaWeaverPlanRequest {
    target_sequence: string;
    target_sequence_id?: string;
    circular?: boolean;
    min_fragment_length?: number;
    max_fragment_length?: number;
    overlap_length?: number;
    vendor_name?: string;
    price_per_bp?: number;
    lead_time_days?: number;
}

export interface DnaWeaverPlanSaveRequest extends DnaWeaverPlanRequest {
    selected_plan_checksum: string;
    new_name?: string;
    save_description?: string;
}

export interface DnaWeaverQualityCheck {
    check_id: string;
    status: 'pass' | 'advisory' | 'blocker';
    detail: string;
    [key: string]: unknown;
}

export interface DnaWeaverPlanResponse {
    planner_engine: string;
    planner_version: string;
    validator_engine: string;
    validator_version: string;
    vendor_name: string;
    estimated_price?: number | null;
    estimated_lead_time_days?: number | null;
    ordered_fragments: (AssemblyFragmentInput & { sequence_sha256: string })[];
    quote: Record<string, unknown>;
    pydna_exact_candidate_count: number;
    selected_product: AssemblyProduct;
    target_checksum: string;
    plan_checksum: string;
    planning_parameters: Record<string, unknown>;
    manufacturability_profile: string;
    quality_checks: DnaWeaverQualityCheck[];
    order_ready: boolean;
    warnings: string[];
    validation_notes: string[];
    saved_sequence?: NucleotideSequence | null;
    message: string;
}

export interface GibsonDesignFragmentInput extends AssemblyFragmentInput {
    preparation: 'pcr' | 'ready_linear';
}

export interface GibsonDesignRequest {
    fragments: GibsonDesignFragmentInput[];
    circular?: boolean;
    overlap?: number;
    target_tm?: number;
    min_anneal?: number;
    selected_candidate_checksum?: string;
    new_name?: string;
    save_description?: string;
}

export interface GibsonDesignedPrimer {
    id: string;
    fragment_id: string;
    fragment_name: string;
    direction: 'forward' | 'reverse';
    full_sequence: string;
    annealing_sequence: string;
    tail_sequence: string;
    tm: number;
    warnings: string[];
}

export interface GibsonDesignedFragment {
    id: string;
    name: string;
    preparation: 'pcr' | 'ready_linear';
    sequence: string;
    checksum: string;
    primer_ids: string[];
}

export interface GibsonDesignCandidate {
    checksum: string;
    product: AssemblyProduct;
    exact_match: boolean;
}

export interface GibsonDesignResponse {
    engine: string;
    engine_version: string;
    circular: boolean;
    overlap: number;
    target_tm: number;
    min_anneal: number;
    primers: GibsonDesignedPrimer[];
    designed_fragments: GibsonDesignedFragment[];
    candidates: GibsonDesignCandidate[];
    selected_candidate_checksum: string;
    selected_product: AssemblyProduct;
    warnings: string[];
    source_provenance: Array<Record<string, unknown>>;
    saved_sequence?: NucleotideSequence | null;
    message: string;
}

export interface GoldenGateAssemblyRequest {
    fragments: AssemblyFragmentInput[];
    circular?: boolean;
    enzyme_name?: string;
    new_name?: string;
    save_description?: string;
}

export interface GoldenGateAssemblyOptionsResponse {
    enzymes: Array<{
        name: string;
        site: string;
        overhang_length: number;
    }>;
}

export interface FetchNucleotideSequencesParams {
    limit?: number;
    offset?: number;
    search?: string;
    sequence_type?: 'dna' | 'rna';
    topology?: 'all' | 'circular' | 'linear';
    sort_by?: 'updated_at' | 'created_at' | 'name' | 'length' | 'gc_content' | 'feature_count';
    sort_desc?: boolean;
}

export const fetchNucleotideSequences = (params: FetchNucleotideSequencesParams = {}) =>
    api.get<NucleotideSequenceListItem[]>('/api/sequences/', { params });

export interface MolBioSequenceRevision {
    id: string;
    revision_id: string;
    sequence_id: string;
    revision_number: number;
    change_kind: string;
    content_sha256: string;
    content_length: number;
    topology: 'circular' | 'linear';
    created_at: string;
    created_by?: string | null;
    is_current: boolean;
}

export const fetchMolBioSequenceRevisions = (sequenceId: string) =>
    api.get<MolBioSequenceRevision[]>(
        `/api/molbio/sequences/${encodeURIComponent(sequenceId)}/revisions`,
    );

export interface MolBioRawDnaImportRow {
    name: string;
    sequence: string;
    topology: 'circular' | 'linear';
}

export interface MolBioSequenceImportPayload {
    source_format: 'fasta' | 'genbank' | 'raw_dna';
    source_text?: string;
    raw_rows?: MolBioRawDnaImportRow[];
    topology_default: 'circular' | 'linear';
    topology_overrides?: Record<number, 'circular' | 'linear'>;
    idempotency_key: string;
    origin_surface?: 'molbio' | 'ngs';
    source_provider?: 'upload' | 'paste' | 'ncbi' | 'library';
    source_id?: string;
}

export interface MolBioSequenceImportPreviewRecord {
    record_ordinal: number;
    source_name: string;
    name: string;
    canonical_digest: string;
    content_sha256: string;
    topology: 'circular' | 'linear';
    length: number;
    exact_duplicate_of?: number | null;
}

export interface MolBioSequenceImportError {
    record_ordinal?: number | null;
    code: string;
    field?: string | null;
    message: string;
}

export interface MolBioSequenceImportPreviewResponse {
    valid: boolean;
    records: MolBioSequenceImportPreviewRecord[];
    errors: MolBioSequenceImportError[];
}

export interface MolBioSequenceImportCommitResponse {
    records: Array<{
        sequence_id: string;
        name: string;
        revision_id?: string;
        reused_existing_revision?: boolean;
    }>;
}

export interface MolBioAnnotationSourceArtifact {
    content: string;
    file_name: string;
    media_type: string;
    source: {
        provider: 'ncbi' | 'addgene';
        source_id: string;
        source_url: string;
        artifact_sha256: string;
    };
}

export const fetchNcbiSequenceArtifact = (accession: string) =>
    api.get<MolBioAnnotationSourceArtifact>(`/api/molbio/annotation-sources/ncbi/${encodeURIComponent(accession)}`);

export const previewMolBioSequenceImport = (payload: MolBioSequenceImportPayload) =>
    api.post<MolBioSequenceImportPreviewResponse>('/api/molbio/sequences/import/preview', payload);

export const commitMolBioSequenceImport = (payload: MolBioSequenceImportPayload) =>
    api.post<MolBioSequenceImportCommitResponse>('/api/molbio/sequences/import/commit', payload);

export const fetchSavedGibsonWorkups = () =>
    api.get<SavedGibsonWorkupListItem[]>('/api/sequences/assembly-workups');

export const fetchNucleotideSequence = (id: string) =>
    api.get<NucleotideSequence>(`/api/sequences/${id}`);

export const createNucleotideSequence = (data: NucleotideSequenceCreate) =>
    api.post<NucleotideSequence>('/api/sequences/', data);

export const updateNucleotideSequence = (id: string, data: Partial<NucleotideSequenceCreate>) =>
    api.put<NucleotideSequence>(`/api/sequences/${id}`, data);

export const deleteNucleotideSequence = (id: string) =>
    api.delete(`/api/sequences/${id}`);

export const addSequenceFeature = (sequenceId: string, feature: Omit<SequenceFeature, 'id'>) =>
    api.post<NucleotideSequence>(`/api/sequences/${sequenceId}/features`, feature);

export const deleteSequenceFeature = (sequenceId: string, featureId: string) =>
    api.delete(`/api/sequences/${sequenceId}/features/${featureId}`);

export interface RnaStructureSettings {
    temperature_c: number;
    no_lonely_pairs: boolean;
    dangles: number;
    circular?: boolean | null;
    max_bp_span?: number | null;
    gamma: number;
    probability_cutoff: number;
    max_pairs: number;
    shape_method?: string | null;
    shape_slope?: number;
    shape_intercept?: number;
    shape_reactivities?: Array<number | null> | null;
    hard_constraints?: string | null;
}

export interface RnaStructurePrediction {
    dot_bracket: string;
    energy_kcal_mol?: number | null;
    score?: number | null;
    distance?: number | null;
    paired_count: number;
}

export interface RnaPartitionSummary {
    dot_bracket: string;
    ensemble_free_energy_kcal_mol: number;
    mean_bp_distance: number;
    probability_cutoff: number;
    pair_count: number;
    truncated: boolean;
}

export interface RnaPairProbability {
    i: number;
    j: number;
    probability: number;
}

export interface RnaBaseProbability {
    index: number;
    base: string;
    paired_probability: number;
    unpaired_probability: number;
    positional_entropy?: number | null;
}

export interface RnaStructureResult {
    source_sequence_id?: string | null;
    name?: string | null;
    sequence: string;
    length: number;
    circular: boolean;
    settings: RnaStructureSettings;
    mfe: RnaStructurePrediction;
    centroid?: RnaStructurePrediction | null;
    mea?: RnaStructurePrediction | null;
    partition?: RnaPartitionSummary | null;
    pair_probabilities: RnaPairProbability[];
    bases: RnaBaseProbability[];
    warnings: string[];
}

export interface RnaStructureOptionsResponse {
    defaults: RnaStructureSettings;
    limits: {
        max_global_fold_length: number;
        max_partition_length: number;
        max_bounded_fold_length: number;
        max_bp_span: number;
    };
    shape_methods: string[];
}

export interface RnaStructureRequest {
    sequence_id?: string;
    name?: string;
    sequence?: string;
    is_circular?: boolean;
    settings?: Partial<RnaStructureSettings>;
}

export interface SequenceAlignmentSettings {
    mode: 'global' | 'local' | 'placement' | 'fragment';
    strand?: 'auto' | 'forward' | 'reverse';
    reference_is_circular?: boolean;
    match_score: number;
    mismatch_score: number;
    gap_open_score: number;
    gap_extend_score: number;
}

export interface SequenceAlignmentVariant {
    type: 'substitution' | 'insertion' | 'deletion' | string;
    start: number;
    end: number;
    reference_wraps_origin?: boolean;
    query_start: number;
    query_end: number;
    reference: string;
    query: string;
    label: string;
    length: number;
}

export interface SequenceAlignmentResult {
    reference_name?: string | null;
    query_name?: string | null;
    reference_sequence: string;
    query_sequence: string;
    reference_aligned: string;
    query_aligned: string;
    midline: string;
    score: number;
    mode: 'global' | 'local' | 'placement' | 'fragment' | string;
    strand?: 'forward' | 'reverse' | string;
    reference_start: number;
    reference_end: number;
    reference_wraps_origin?: boolean;
    query_start: number;
    query_end: number;
    query_soft_clip_left?: number;
    query_soft_clip_right?: number;
    reference_flank_left?: number;
    reference_flank_right?: number;
    alignment_length: number;
    matches: number;
    mismatches: number;
    gap_columns: number;
    aligned_columns?: number;
    reference_aligned_bases?: number;
    query_aligned_bases?: number;
    identity_pct?: number;
    ungapped_identity: number;
    reference_coverage: number;
    query_coverage: number;
    variants: SequenceAlignmentVariant[];
}

export interface PrimerDesignRequest {
    sequence_id?: string;
    name?: string;
    sequence?: string;
    sequence_type?: 'dna' | 'rna';
    is_circular?: boolean;
    target_start: number;
    target_end?: number;
    primer_min_length?: number;
    primer_max_length?: number;
    product_min_length?: number;
    product_max_length?: number;
    flank_search_span?: number;
    gc_min_percent?: number;
    gc_max_percent?: number;
    tm_target_c?: number;
    tm_max_delta_c?: number;
    gc_clamp_min?: number;
    max_poly_x?: number;
    max_pairs?: number;
    overhang_forward?: string;
    overhang_reverse?: string;
    tm_settings?: PrimerTmSettings;
}

export interface PrimerDesignCandidate {
    sequence: string;
    anneal_sequence: string;
    start: number;
    end: number;
    strand: 1 | -1;
    length: number;
    anneal_length: number;
    overhang_length: number;
    tm: number;
    gc_percent: number;
    gc_clamp: number;
    max_homopolymer: number;
    max_self_complement: number;
    three_prime_self_complement: number;
    max_hairpin_stem: number;
    hairpin_loop_size?: number | null;
    binding_site_count?: number | null;
    off_target_site_count?: number | null;
    warnings: string[];
}

export interface PrimerDesignPair {
    rank: number;
    penalty: number;
    tm_delta: number;
    product_start: number;
    product_end: number;
    product_length: number;
    heterodimer_complement: number;
    three_prime_heterodimer: number;
    warnings: string[];
    forward: PrimerDesignCandidate;
    reverse: PrimerDesignCandidate;
}

export interface PrimerDesignResponse {
    sequence_name?: string | null;
    sequence_type: 'dna' | 'rna';
    target_start: number;
    target_end: number;
    target_length: number;
    pair_count: number;
    pairs: PrimerDesignPair[];
    warnings: string[];
}

export interface PrimerQcBindingPosition {
    start: number;
    end: number;
    strand: number;
    anneal_length: number;
    overhang_length: number;
    reverse_primer_binding: boolean;
}

export interface PrimerQcMetrics {
    sequence: string;
    sequence_type: 'dna' | 'rna' | string;
    length: number;
    gc_percent: number;
    max_self_complement: number;
    three_prime_self_complement: number;
    max_hairpin_stem: number;
    hairpin_loop_size?: number | null;
    binding_site_count?: number | null;
    off_target_site_count?: number | null;
    binding_positions: PrimerQcBindingPosition[];
    warnings: string[];
}

export interface PrimerQcEntry {
    id?: string | null;
    name?: string | null;
    qc: PrimerQcMetrics;
}

export interface PrimerPairQcEntry {
    left_id?: string | null;
    left_name?: string | null;
    right_id?: string | null;
    right_name?: string | null;
    heterodimer_complement: number;
    three_prime_heterodimer: number;
    warnings: string[];
}

export interface PrimerQcResponse {
    primers: PrimerQcEntry[];
    pairwise: PrimerPairQcEntry[];
}

export const fetchRnaStructureOptions = () =>
    api.get<RnaStructureOptionsResponse>('/api/molbio/rna-structure/options');

export const foldRnaStructure = (data: RnaStructureRequest & { include_partition?: boolean }) =>
    api.post<RnaStructureResult>('/api/molbio/rna-structure/fold', data);

export const partitionRnaStructure = (data: RnaStructureRequest) =>
    api.post<RnaStructureResult>('/api/molbio/rna-structure/partition', data);

export const alignMolBioSequences = (data: {
    reference_name?: string;
    reference_sequence: string;
    query_name?: string;
    query_sequence: string;
    settings?: Partial<SequenceAlignmentSettings>;
}) => api.post<SequenceAlignmentResult>('/api/molbio/alignment', data);

export const simulateLigationAssembly = (data: LigationAssemblyRequest) =>
    api.post<AssemblyOperationResponse>('/api/molbio/assembly/ligation/simulate', data);

export const saveLigationAssembly = (data: LigationAssemblyRequest) =>
    api.post<AssemblyOperationResponse>('/api/molbio/assembly/ligation/save', data);

export const simulateGibsonAssembly = (data: GibsonAssemblyRequest) =>
    api.post<AssemblyOperationResponse>('/api/molbio/assembly/gibson/simulate', data);

export const saveGibsonAssembly = (data: GibsonAssemblyRequest) =>
    api.post<AssemblyOperationResponse>('/api/molbio/assembly/gibson/save', data);

export const planDnaWeaverGibsonAssembly = (data: DnaWeaverPlanRequest) =>
    api.post<DnaWeaverPlanResponse>('/api/molbio/assembly/gibson/dnaweaver/plan', data);

export const saveDnaWeaverGibsonAssembly = (data: DnaWeaverPlanSaveRequest) =>
    api.post<DnaWeaverPlanResponse>('/api/molbio/assembly/gibson/dnaweaver/save', data);

export const designGibsonAssembly = (data: GibsonDesignRequest) =>
    api.post<GibsonDesignResponse>('/api/molbio/assembly/gibson/design', data);

export const saveDesignedGibsonAssembly = (data: GibsonDesignRequest) =>
    api.post<GibsonDesignResponse>('/api/molbio/assembly/gibson/design/save', data);

export const fetchGoldenGateAssemblyOptions = () =>
    api.get<GoldenGateAssemblyOptionsResponse>('/api/molbio/assembly/golden-gate/options');

export const simulateGoldenGateAssembly = (data: GoldenGateAssemblyRequest) =>
    api.post<AssemblyOperationResponse>('/api/molbio/assembly/golden-gate/simulate', data);

export const saveGoldenGateAssembly = (data: GoldenGateAssemblyRequest) =>
    api.post<AssemblyOperationResponse>('/api/molbio/assembly/golden-gate/save', data);

// Antibody API
export interface AntibodyOverlaySelection {
    region: 'H1' | 'H2' | 'H3' | 'L1' | 'L2' | 'L3';
    chain_id: string;
    start_residue_number: number;
    end_residue_number: number;
}

export interface AntibodyData {
    design_id: string;
    cdrs: {
        H1?: string; H2?: string; H3?: string;
        L1?: string; L2?: string; L3?: string;
    };
    cdr_lengths?: {
        H1?: number | null; H2?: number | null; H3?: number | null;
        L1?: number | null; L2?: number | null; L3?: number | null;
    };
    binder_length?: number | null;
    antibody_type?: string | null;
    humanness_score?: number;
    stability_data?: Record<string, Record<string, number>>; // chain -> pos -> ddG
    imgt_pdb_url?: string;
    detected_antibody_chains?: string | null;
    framework_regions?: {
        fr2_contacts?: string | null;
        de_loop?: string | null;
        fr3_contacts?: string | null;
        fr4_contacts?: string | null;
    };
    binder_chains?: Record<string, string>;
    overlay_selections?: AntibodyOverlaySelection[];
}

export const fetchAntibodyData = (designId: string) =>
    api.get<AntibodyData>(`/api/designs/${designId}/antibody`);

export const fetchAntiFoldLogits = (designId: string) =>
    `/api/designs/${designId}/antifold-logits`;

// ============================================================
// ADVANCED ANALYTICS API
// ============================================================

export interface CorrelationMatrix {
    job_id: string;
    metrics: string[];
    matrix: number[][];  // NxN Pearson R values
    sample_sizes: number[][];
}

export interface AACount {
    aa: string;
    count: number;
    frequency: number;
}

export interface CDRComposition {
    cdr_name: string;
    total_residues: number;
    composition: AACount[];
}

export interface AACompositionResponse {
    job_id: string;
    overall: AACount[];
    by_cdr: CDRComposition[];
}

export interface PositionFrequency {
    position: number;
    frequencies: Record<string, number>;
}

export interface SequenceLogoData {
    cdr_name: string;
    length: number;
    positions: PositionFrequency[];
    consensus: string;
    sequence_count: number;
}

export interface CDRAnalysisResponse {
    job_id: string;
    logos: SequenceLogoData[];
}

export const fetchCorrelationMatrix = (jobId: string) =>
    api.get<CorrelationMatrix>(`/api/analytics/job/${jobId}/correlation-matrix`);

export const fetchAAComposition = (jobId: string) =>
    api.get<AACompositionResponse>(`/api/analytics/job/${jobId}/aa-composition`);

export const fetchCDRLogos = (jobId: string) =>
    api.get<CDRAnalysisResponse>(`/api/analytics/job/${jobId}/cdr-logos`);

// ============================================================
// PHASE 3a: PLOTLY ANALYTICS ENDPOINTS
// ============================================================

export interface ContactMapData {
    design_id: string;
    design_name: string;
    distance_matrix: number[][];  // 2D Cα-Cα distances
    residue_numbers: number[];
    chain_ids: string[];
    size: number;
}

export interface ChainPairIptmData {
    design_id: string;
    design_name: string;
    chain_ids: string[];
    iptm_matrix: (number | null)[][];  // NxN chain iPTM matrix
    size: number;
}

export interface PlotlyMetricPoint {
    id: string;
    name: string;
    metrics: Record<string, number>;
}

export interface PlotlyMetricMetadata {
    label: string;
    description?: string | null;
    unit?: string | null;
    source?: string | null;
    semantics?: string | null;
    higher_is_better?: boolean | null;
    color?: string | null;
}

export interface PlotlyChartSuggestion {
    id: string;
    label: string;
    type: string;
    xAxis?: string | null;
    yAxis?: string | null;
    zAxis?: string | null;
    colorBy?: string | null;
    description?: string | null;
}

export interface PlotlyMetricsResponse {
    job_id: string;
    metric_keys: string[];
    points: PlotlyMetricPoint[];
    total: number;
    metric_metadata?: Record<string, PlotlyMetricMetadata>;
    chart_suggestions?: PlotlyChartSuggestion[];
}

export const fetchContactMap = (designId: string, maxSize: number = 400) =>
    api.get<ContactMapData>(`/api/designs/${designId}/contact-map`, { params: { max_size: maxSize } });

export const fetchChainPairIptm = (designId: string) =>
    api.get<ChainPairIptmData>(`/api/designs/${designId}/chain-iptm`);

export const fetchDesignPlotlyMetrics = (
    jobId: string,
    params?: { include_children?: boolean; limit?: number; offset?: number; design_ids?: string[] }
) =>
    params?.design_ids?.length
        ? api.post<PlotlyMetricsResponse>(`/api/designs/by-job/${jobId}/plotly-metrics`, {
            include_children: params.include_children ?? true,
            limit: params.limit,
            offset: params.offset,
            design_ids: params.design_ids,
        })
        : api.get<PlotlyMetricsResponse>(`/api/designs/by-job/${jobId}/plotly-metrics`, {
            params: {
                include_children: params?.include_children,
                limit: params?.limit,
                offset: params?.offset,
            },
        });

// PAE (Predicted Aligned Error) data
export interface PAEData {
    design_id: string;
    design_name: string;
    pae_matrix: number[][];  // 2D PAE matrix
    size: number;
    source_mode?: string;
    confidence_file?: string | null;
}

export const fetchPAEData = (designId: string) =>
    api.get<PAEData>(`/api/designs/${designId}/pae`);

// ============================================================
// DEBUG ORCHESTRATOR OVERRIDES
// ============================================================

export interface ForceRunResponse {
    success: boolean;
    message: string;
    job_id: string;
    gpu_id: number;
}

export interface ConcurrencyLimitsResponse {
    concurrency_limits: Record<string, number | null>;
    description: string;
}

export const forceRunJob = (jobId: string, gpuId?: number) =>
    api.post<ForceRunResponse>(`/api/system/force-run/${jobId}`, { gpu_id: gpuId ?? null });

export const getConcurrencyLimits = () =>
    api.get<ConcurrencyLimitsResponse>('/api/system/concurrency-limits');

export const setConcurrencyLimit = (modelType: string, limit: number | null) =>
    api.put('/api/system/concurrency-limits', { model_type: modelType, limit });

export const deleteConcurrencyLimit = (modelType: string) =>
    api.delete(`/api/system/concurrency-limits/${modelType}`);

// ============================================================
// FRAMEWORK LIBRARY API (SAbDab Integration)
// ============================================================

export interface SAbDabSearchResult {
    pdb_code: string;
    h_chain: string;
    model: number;
    resolution: number | null;
    method: string | null;
    species: string | null;  // heavy_species
    germline: string | null;  // heavy_subclass
    cdr_h3_length: number | null;
    cdr_h3_sequence: string | null;
    antigen_type: string | null;
    antigen_name: string | null;
    affinity: number | null;
    date: string | null;
    engineered: boolean;
    has_antigen: boolean;
}

export interface SAbDabSearchResponse {
    results: SAbDabSearchResult[];
    total: number;
    limit: number;
    offset: number;
}

export interface FrameworkDownloadResponse {
    pdb_code: string;
    scheme: string;
    cached: boolean;
    file_path: string | null;
    pdb_content: string | null;
    // Chain metadata from SAbDab DB for post-download selection
    h_chain?: string | null;      // Antibody heavy chain ID
    l_chain?: string | null;      // Antibody light chain ID (None for VHH)
    antigen_chain?: string | null;  // Antigen chain ID(s) if bound
    antigen_name?: string | null;   // Antigen name for display
}

export interface CachedFramework {
    pdb_code: string;
    scheme: string;
    file_path: string;
    size_bytes: number;
    cached_at: string;
    last_used_at?: string | null;
}

export interface CachedRcsbEntry {
    pdb_id: string;
    path: string;
    url: string;
    size_bytes: number;
    cached_at: string;
    last_used_at?: string | null;
}

export interface RcsbCacheResponse {
    cached: CachedRcsbEntry[];
    count: number;
    cache_dir: string;
}

export interface FrameworkLibraryResponse {
    frameworks: CachedFramework[];
    total: number;
    cache_dir: string;
}

export interface SAbDabAttribution {
    source: string;
    citation: string;
    license: string;
    license_url: string;
    website: string;
    local_mirror?: string;
}

export interface SAbDabDatabaseStats {
    total_entries: number;
    entries_with_cdr_h3: number;
    last_sync: string | null;
    species_distribution: Record<string, number>;
    db_path: string;
    db_size_mb: number;
}

export interface SAbDabFilterOptions {
    species: string[];
    methods: string[];
    antigen_types: string[];
    germlines: string[];
    cdr_h3_length_range: [number, number];
}

export interface SAbDabSearchParams {
    species?: string;
    resolution_min?: number;
    resolution_max?: number;
    cdr_h3_min?: number;
    cdr_h3_max?: number;
    antigen_type?: string;
    has_antigen?: boolean;
    methods?: string;  // comma-separated
    germlines?: string;  // comma-separated
    has_affinity?: boolean;
    include_scfv?: boolean;
    sort_by?: 'resolution' | 'cdr_h3_length' | 'pdb_code' | 'date';
    sort_desc?: boolean;
    limit?: number;
    offset?: number;
}

export const searchSabdabFrameworks = (params: SAbDabSearchParams) =>
    api.get<SAbDabSearchResponse>('/api/frameworks/sabdab/search', { params });

export const getSabdabDatabaseStats = () =>
    api.get<SAbDabDatabaseStats>('/api/frameworks/sabdab/stats');

export const getSabdabFilterOptions = () =>
    api.get<SAbDabFilterOptions>('/api/frameworks/sabdab/filters');

export const downloadSabdabFramework = (pdbCode: string, params?: {
    scheme?: string;
    include_content?: boolean;
    convert_hlt?: boolean;
}) => api.get<FrameworkDownloadResponse>(`/api/frameworks/sabdab/${pdbCode}/download`, { params });

export const getFrameworkSummary = (pdbCode: string) =>
    api.get<Record<string, string>>(`/api/frameworks/sabdab/${pdbCode}/summary`);

export const listCachedFrameworks = () =>
    api.get<FrameworkLibraryResponse>('/api/frameworks/library');

export const touchCachedFramework = (pdbCode: string, scheme: string) =>
    api.post<CachedFramework>(`/api/frameworks/library/${pdbCode}/touch`, null, { params: { scheme } });

export const removeCachedFramework = (pdbCode: string, scheme?: string) =>
    api.delete(`/api/frameworks/library/${pdbCode}`, { params: { scheme } });

export const listCachedRcsbPdbs = () =>
    api.get<RcsbCacheResponse>('/api/rcsb');

export const getSabdabAttribution = () =>
    api.get<SAbDabAttribution>('/api/frameworks/attribution');

// CDR Annotation via ANARCII
export interface CDRAnnotationResponse {
    pdb_code: string;
    antibody_type: string;
    cdr_h1?: string | null;
    cdr_h2?: string | null;
    cdr_h3?: string | null;
    cdr_l1?: string | null;
    cdr_l2?: string | null;
    cdr_l3?: string | null;
    cdr_h1_range?: [number, number] | null;
    cdr_h2_range?: [number, number] | null;
    cdr_h3_range?: [number, number] | null;
    cdr_l1_range?: [number, number] | null;
    cdr_l2_range?: [number, number] | null;
    cdr_l3_range?: [number, number] | null;
    // Sequential 0-indexed string ranges mapped to PDB arrays
    cdr_h1_seq_range?: [number, number] | null;
    cdr_h2_seq_range?: [number, number] | null;
    cdr_h3_seq_range?: [number, number] | null;
    cdr_l1_seq_range?: [number, number] | null;
    cdr_l2_seq_range?: [number, number] | null;
    cdr_l3_seq_range?: [number, number] | null;
}

export const annotateFrameworkCdrs = (pdbCode: string, scheme: string = 'imgt') =>
    api.post<CDRAnnotationResponse>(`/api/frameworks/sabdab/${pdbCode}/annotate-cdrs`, null, { params: { scheme } });

// ============================================================
// PRIMER LIBRARY API (MolBio Toolkit)
// ============================================================

export interface PrimerTmSettings {
    algorithm: string;
    salt_correction: string;
    primer_concentration_nM: number;
    template_concentration_nM: number;
    na_mM: number;
    k_mM: number;
    tris_mM: number;
    mg_mM: number;
    dntps_mM: number;
    dmso_percent: number;
    formamide_percent: number;
    self_complementary: boolean;
}

export interface PrimerTmOption {
    id: string;
    label: string;
    description: string;
    sequence_types: Array<'dna' | 'rna'>;
    polymer_pairing?: string | null;
}

export interface PrimerTmSaltCorrectionOption {
    id: string;
    label: string;
    description: string;
}

export interface PrimerTmOptionsResponse {
    algorithms: PrimerTmOption[];
    salt_corrections: PrimerTmSaltCorrectionOption[];
    defaults: {
        dna: PrimerTmSettings;
        rna: PrimerTmSettings;
    };
}

export interface PrimerTmInput {
    id?: string;
    name?: string;
    sequence: string;
    sequence_type?: 'dna' | 'rna';
    complement_sequence?: string;
    shift?: number;
}

export interface PrimerTmResult {
    id?: string | null;
    name?: string | null;
    sequence: string;
    sequence_type: 'dna' | 'rna';
    length: number;
    gc_percent: number;
    tm: number | null;
    algorithm: string;
    algorithm_label: string;
    salt_correction: string;
    salt_correction_label: string;
    polymer_pairing: string;
    warnings: string[];
}

export interface Primer {
    id: string;
    name: string;
    sequence: string;
    sequence_type: 'dna' | 'rna';
    length: number;
    tm: number | null;
    gc_percent: number | null;
    tm_algorithm: string | null;
    tm_salt_correction: string | null;
    tm_settings: PrimerTmSettings | null;
    primer_type: string;
    description: string | null;
    target_sequence_id: string | null;
    binding_start: number | null;
    binding_end: number | null;
    binding_strand: number;
    tags: string[] | null;
    is_favorite: boolean;
    created_at: string;
    updated_at: string | null;
}

export interface PrimerCreate {
    name: string;
    sequence: string;
    sequence_type?: 'dna' | 'rna';
    primer_type?: string;
    description?: string;
    target_sequence_id?: string;
    binding_start?: number;
    binding_end?: number;
    binding_strand?: number;
    tags?: string[];
    tm_settings?: PrimerTmSettings;
}

export interface PrimerUpdate {
    name?: string;
    sequence?: string;
    sequence_type?: 'dna' | 'rna';
    primer_type?: string;
    description?: string;
    target_sequence_id?: string;
    binding_start?: number;
    binding_end?: number;
    binding_strand?: number;
    tags?: string[];
    is_favorite?: boolean;
    tm_settings?: PrimerTmSettings;
}

export const fetchPrimerTmOptions = () =>
    api.get<PrimerTmOptionsResponse>('/api/molbio/primer-tm/options');

export const calculatePrimerTm = (data: {
    primers: PrimerTmInput[];
    settings?: PrimerTmSettings;
}) => api.post<PrimerTmResult[]>('/api/molbio/primer-tm/calculate', data);

export const calculatePrimerQc = (data: {
    primers: Array<{
        id?: string;
        name?: string;
        sequence: string;
        sequence_type?: 'dna' | 'rna';
    }>;
    template_sequence?: string;
    template_sequence_type?: 'dna' | 'rna';
    template_is_circular?: boolean;
    include_pairwise?: boolean;
}) => api.post<PrimerQcResponse>('/api/molbio/primer-qc', data);

export const designPrimers = (data: PrimerDesignRequest) =>
    api.post<PrimerDesignResponse>('/api/molbio/primer-design', data);

export const fetchPrimers = (params?: {
    search?: string;
    primer_type?: string;
    favorites_only?: boolean;
    target_sequence_id?: string;
}) => api.get<Primer[]>('/api/molbio/primers', { params });

export const fetchPrimer = (id: string) =>
    api.get<Primer>(`/api/molbio/primers/${id}`);

export const createPrimer = (data: PrimerCreate) =>
    api.post<Primer>('/api/molbio/primers', data);

export const updatePrimer = (id: string, data: PrimerUpdate) =>
    api.patch<Primer>(`/api/molbio/primers/${id}`, data);

export const deletePrimer = (id: string) =>
    api.delete(`/api/molbio/primers/${id}`);

export const togglePrimerFavorite = (id: string) =>
    api.post<Primer>(`/api/molbio/primers/${id}/toggle-favorite`);


// ============================================================
// ONT INSTRUMENT CONTROL API
// ============================================================
export interface OntFlowCellInfo {
    present: boolean;
}

export interface OntLiveDevice {
    position: string;
    device_type: 'mk1d';
    state?: string | null;
    running: boolean;
    available_for_run: boolean;
    flow_cell: OntFlowCellInfo;
    fake_or_demo_device: false;
}

export interface OntDeviceStatus {
    implementation_status: string;
    live_devices: OntLiveDevice[];
    fake_or_demo_devices: false;
    message?: string;
}

export interface OntRunEvent {
    id: string;
    event_type: string;
    status: string;
    observed_at: string;
    observed_generation: number;
}

export interface OntInstrumentRun {
    id: string;
    position: string;
    status: string;
    observed_generation: number;
    sample_id?: string | null;
    experiment_group?: string | null;
    handoff_ready: boolean;
    output_summary: Record<'fastq' | 'pod5' | 'bam', number>;
    events: OntRunEvent[];
    fake_or_demo_devices: false;
}

export interface OntProtocolOption {
    option_id: string;
    option_receipt_id: string;
    expires_at: string;
    protocol_label: string;
    basecalling_enabled: boolean;
    output_policy_id: string;
    output_policy_label: string;
}

export interface OntProtocolOptions {
    position: string;
    can_start: boolean;
    blockers: string[];
    flow_cell_present: boolean;
    options: OntProtocolOption[];
    fake_or_demo_devices: false;
}

export const fetchOntDeviceStatus = () =>
    api.get<OntDeviceStatus>('/api/ont/devices/status');

export interface OntMk1dReconnectResponse {
    action: 'manual_mk1d_reconnect';
    receipt: {
        receipt_id: string;
        status: 'completed' | 'failed' | 'blocked' | 'busy';
        minknow: string;
        host_agent_recreate: string;
        host_agent_health: string;
    };
    device_status_observed: boolean;
    connected: boolean;
}

export const requestMk1dReconnect = () =>
    api.post<OntMk1dReconnectResponse>('/api/ont/devices/reconnect', { confirm_reconnect: true });

export const fetchOntProtocolOptions = (position: string) =>
    api.get<OntProtocolOptions>(`/api/ont/positions/${encodeURIComponent(position)}/protocol-options`);

export const createOntRunIntent = (position: string, payload: {
    option_id: string;
    option_receipt_id: string;
    sample_id?: string;
    experiment_group?: string;
}) => api.post<OntInstrumentRun>(`/api/ont/positions/${encodeURIComponent(position)}/run-intents`, payload);

export const startOntRunIntent = (runId: string, payload: { confirm_start: boolean; intent_generation: number }) =>
    api.post<OntInstrumentRun>(`/api/ont/runs/${encodeURIComponent(runId)}/start`, payload);

export const fetchOntInstrumentRun = (runId: string) =>
    api.get<OntInstrumentRun>(`/api/ont/runs/${encodeURIComponent(runId)}`);

export interface ViewerVolumeInventoryV1 {
    readonly schema: 'bms.viewer.volume-list.v1';
    readonly jobId: string;
    readonly volumes: readonly SpatialVolumeDescriptorV1[];
    readonly segmentations: readonly VolumeSegmentationV1[];
    readonly registrations: readonly VolumeRegistrationV1[];
}

export interface ViewerSnapshotRecordV2 {
    readonly schema: 'bms.viewer.snapshot-record.v2';
    readonly snapshotId: string;
    readonly jobId: string;
    readonly label: string;
    readonly createdBy: string;
    readonly createdAt: string;
    readonly schemaVersion: 2;
    readonly snapshotSha256: string;
    readonly snapshot?: ViewerSnapshotV2;
}

export const fetchViewerVolumes = (jobId: string) =>
    api.get<ViewerVolumeInventoryV1>(`/api/jobs/${encodeURIComponent(jobId)}/viewer/volumes`);

export const viewerArtifactContentUrl = (jobId: string, artifactId: string): string =>
    `/api/jobs/${encodeURIComponent(jobId)}/viewer/artifacts/${encodeURIComponent(artifactId)}/content`;

export const fetchViewerSnapshots = (jobId: string) =>
    api.get<{ schema: 'bms.viewer.snapshot-list.v2'; jobId: string; snapshots: ViewerSnapshotRecordV2[]; nextCursor: string | null }>(
        `/api/jobs/${encodeURIComponent(jobId)}/viewer/snapshots`,
    );

export const fetchViewerSnapshot = (jobId: string, snapshotId: string) =>
    api.get<ViewerSnapshotRecordV2>(
        `/api/jobs/${encodeURIComponent(jobId)}/viewer/snapshots/${encodeURIComponent(snapshotId)}`,
    );

export const createViewerSnapshot = (
    jobId: string,
    label: string,
    snapshot: ViewerSnapshotV2,
    snapshotSha256: string,
) => api.post<ViewerSnapshotRecordV2>(`/api/jobs/${encodeURIComponent(jobId)}/viewer/snapshots`, {
    schema: 'bms.viewer.snapshot-create.v2', label, snapshot, snapshotSha256,
});

export const deleteViewerSnapshot = (jobId: string, snapshotId: string) =>
    api.delete(`/api/jobs/${encodeURIComponent(jobId)}/viewer/snapshots/${encodeURIComponent(snapshotId)}`);

// Global Project / Experiment and MolBio/NGS Domain Experiment contracts.
// These clients return response data directly and preserve the server's detail
// so unavailable adapters, integrity failures, and revision conflicts are visible.
export class VisibleApiError extends Error {
    readonly status: number | null;
    readonly detail: string;

    constructor(message: string, status: number | null, detail: string) {
        super(message);
        this.name = 'VisibleApiError';
        this.status = status;
        this.detail = detail;
    }
}

async function apiData<T>(request: Promise<{ data: T }>): Promise<T> {
    try {
        return (await request).data;
    } catch (error) {
        if (axios.isAxiosError(error)) {
            const status = error.response?.status ?? null;
            const body = error.response?.data as { detail?: unknown; message?: unknown } | string | undefined;
            const detail = typeof body === 'string'
                ? body
                : typeof body?.detail === 'string'
                    ? body.detail
                    : typeof body?.message === 'string'
                        ? body.message
                        : error.message;
            throw new VisibleApiError(
                status === null ? detail : `${detail} (HTTP ${status})`,
                status,
                detail,
            );
        }
        const detail = error instanceof Error ? error.message : String(error);
        throw new VisibleApiError(detail, null, detail);
    }
}

export interface GlobalAggregateHead {
    id: string;
    kind: 'workspace' | 'experiment' | 'workflow' | 'dataset' | string;
    workspace_id: string;
    parent_id: string | null;
    current_revision_id: string | null;
    head_generation: number;
    lifecycle_state: string;
    name: string;
    description: string;
    created_at: string;
    updated_at: string;
}

export const fetchGlobalWorkspaces = () =>
    apiData(api.get<GlobalAggregateHead[]>('/api/experiment-workspaces'));
export const fetchGlobalWorkspace = (workspaceId: string) =>
    apiData(api.get<GlobalAggregateHead>(`/api/experiment-workspaces/${encodeURIComponent(workspaceId)}`));
export const fetchGlobalExperiments = (workspaceId: string) =>
    apiData(api.get<GlobalAggregateHead[]>(`/api/experiment-workspaces/${encodeURIComponent(workspaceId)}/experiments`));
export const fetchGlobalExperiment = (workspaceId: string, experimentId: string) =>
    apiData(api.get<GlobalAggregateHead>(
        `/api/experiment-workspaces/${encodeURIComponent(workspaceId)}/experiments/${encodeURIComponent(experimentId)}`,
    ));

export interface LocalDomainCounts {
    samples: number;
    references: number;
    evidence_assessments: number;
}

export interface DomainExperimentView {
    project_id: string;
    global_experiment_id: string;
    domain_experiment_id: string;
    global_domain_experiment_revision_id: string;
    local_state_revision_id: string | null;
    local_state_head_generation: number;
    local_counts: LocalDomainCounts;
    availability: {
        local_state: 'available';
        persisted_global_binding: 'acknowledged';
        global_adapter: 'unavailable';
    };
    created_at: string;
    updated_at: string;
    reopen_destination: {
        surface: 'molbio-ngs-domain-experiment';
        params: { domain_experiment_id: string };
    };
}

export interface ProjectDomainSummary {
    project_id: string;
    domain_experiment_count: number;
    local_totals: LocalDomainCounts;
    availability: {
        persisted_global_bindings: 'acknowledged_only';
        global_adapter: 'unavailable';
    };
    reopen_destination: {
        surface: 'molbio-ngs-project-summary';
        params: { project_id: string };
    };
}

export const fetchMolBioNgsProjectSummary = (workspaceId: string) =>
    apiData(api.get<ProjectDomainSummary>(
        `/api/molbio-ngs/projects/${encodeURIComponent(workspaceId)}/summary`,
    ));
export const fetchMolBioNgsProjectExperiments = (workspaceId: string) =>
    apiData(api.get<DomainExperimentView[]>(
        `/api/molbio-ngs/projects/${encodeURIComponent(workspaceId)}/experiments`,
    ));
export const fetchMolBioNgsDomainExperiment = (domainExperimentId: string) =>
    apiData(api.get<DomainExperimentView>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}`,
    ));

export interface DomainState {
    global_domain_experiment_id: string;
    current_state_revision_id: string | null;
    head_generation: number;
    created_at: string;
    updated_at: string;
}

export type DomainStateMemberRole =
    | 'molecular_expected_construct'
    | 'molecular_input_fragment'
    | 'molecular_assembly_product'
    | 'molecular_pcr_template'
    | 'molecular_pcr_product'
    | 'molecular_primer_forward'
    | 'molecular_primer_reverse'
    | 'molecular_operation'
    | 'molecular_pcr_experiment'
    | 'ngs_reference'
    | 'ngs_comparison_panel'
    | 'ngs_instrument_run'
    | 'ngs_analysis_job'
    | 'ngs_analysis_result_manifest'
    | 'ngs_verification_assessment';

export interface DomainStateRevisionPayload {
    schema: 'bms.molbio-ngs.domain-state-revision.v1';
    design: {
        sample_revision_ids: string[];
        conditions: Record<string, unknown>[];
        replicates: Record<string, unknown>[];
        expected_molecule_roles: DomainStateMemberRole[];
    };
    reference_policy: {
        required_roles: DomainStateMemberRole[];
        coordinate_policy: 'exact_revision';
    };
    acquisition_policy: {
        platform: 'ont' | 'external' | 'none';
        required_terminal_manifest: boolean;
    };
    analysis_policy: {
        allowed_workflow_ids: string[];
        required_manifest_schemas: string[];
    };
    assessment_policy: {
        rule_id: string;
        completion_is_scientific_pass: false;
    };
    notes: string;
}

export interface DomainStateMember {
    receipt_id: string;
    role: DomainStateMemberRole;
    ordinal: number;
    sample_revision_id: string | null;
    source_store_id: string;
    entity_kind: string;
    entity_id: string;
    source_generation_or_revision: string;
    content_digest: string;
    source_schema: string;
    availability: string;
    reopen_destination: Record<string, unknown>;
    receipt_sha256: string;
}

export interface DomainStateRevision {
    id: string;
    global_domain_experiment_id: string;
    global_domain_experiment_revision_id: string;
    revision_number: number;
    parent_revision_id: string | null;
    schema_name: string;
    schema_version: string;
    payload: DomainStateRevisionPayload;
    payload_sha256: string;
    membership_graph_sha256: string;
    members: DomainStateMember[];
    created_at: string;
    created_by: string | null;
}

export interface InitializeDomainStateRequest {
    global_domain_experiment_revision_id: string;
    idempotency_key: string;
}

export interface SaveDomainStateRevisionRequest {
    global_domain_experiment_revision_id: string;
    expected_head_generation: number;
    parent_revision_id?: string | null;
    idempotency_key: string;
    payload: DomainStateRevisionPayload;
    members: Array<{
        receipt_id: string;
        role: DomainStateMemberRole;
        ordinal: number;
        sample_revision_id?: string | null;
    }>;
}

export const initializeMolBioNgsDomainState = (domainExperimentId: string, payload: InitializeDomainStateRequest) =>
    apiData(api.post<DomainState>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/state`,
        payload,
    ));
export const fetchMolBioNgsDomainState = (domainExperimentId: string) =>
    apiData(api.get<DomainState>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/state`,
    ));
export const fetchMolBioNgsStateRevisions = (domainExperimentId: string) =>
    apiData(api.get<DomainStateRevision[]>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/state/revisions`,
    ));
export const saveMolBioNgsStateRevision = (domainExperimentId: string, payload: SaveDomainStateRevisionRequest) =>
    apiData(api.post<DomainStateRevision>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/state/revisions`,
        payload,
    ));
export const fetchMolBioNgsStateRevision = (domainExperimentId: string, revisionId: string) =>
    apiData(api.get<DomainStateRevision>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/state/revisions/${encodeURIComponent(revisionId)}`,
    ));

export interface SampleRevisionPayload {
    schema: 'bms.molbio-ngs.sample-revision.v1';
    name: string;
    description: string;
    sample_kind: string;
    source: { organism: string | null; strain: string | null; external_ids: string[] };
    preparation: { method: string; batch_id: string | null; prepared_at: string | null };
    labels: { container_label: string | null; barcode: string | null; minknow_sample_id: string | null };
    notes: string;
}

export interface DomainSample {
    id: string;
    global_domain_experiment_id: string;
    current_revision_id: string | null;
    head_generation: number;
    archived_at: string | null;
    created_at: string;
    updated_at: string;
    reopen_destination: {
        surface: 'molbio-ngs-sample';
        params: { global_domain_experiment_id: string; sample_id: string };
    };
}

export interface DomainSampleRevision {
    id: string;
    sample_id: string;
    global_domain_experiment_id: string;
    revision_number: number;
    parent_revision_id: string | null;
    schema_name: string;
    schema_version: string;
    payload: SampleRevisionPayload;
    payload_sha256: string;
    created_at: string;
    created_by: string | null;
    reopen_destination: {
        surface: 'molbio-ngs-sample-revision';
        params: { global_domain_experiment_id: string; sample_id: string; revision_id: string };
    };
}

export interface CreateDomainSampleRequest {
    payload: SampleRevisionPayload;
    idempotency_key: string;
}

export interface CreateDomainSampleRevisionRequest extends CreateDomainSampleRequest {
    expected_head_generation: number;
    parent_revision_id: string;
}

export const createMolBioNgsSample = (domainExperimentId: string, payload: CreateDomainSampleRequest) =>
    apiData(api.post<DomainSampleRevision>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/samples`, payload,
    ));
export const fetchMolBioNgsSamples = (domainExperimentId: string) =>
    apiData(api.get<DomainSample[]>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/samples`,
    ));
export const fetchMolBioNgsSample = (domainExperimentId: string, sampleId: string) =>
    apiData(api.get<DomainSample>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/samples/${encodeURIComponent(sampleId)}`,
    ));
export const createMolBioNgsSampleRevision = (
    domainExperimentId: string,
    sampleId: string,
    payload: CreateDomainSampleRevisionRequest,
) => apiData(api.post<DomainSampleRevision>(
    `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/samples/${encodeURIComponent(sampleId)}/revisions`,
    payload,
));
export const fetchMolBioNgsSampleRevisions = (domainExperimentId: string, sampleId: string) =>
    apiData(api.get<DomainSampleRevision[]>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/samples/${encodeURIComponent(sampleId)}/revisions`,
    ));
export const fetchMolBioNgsSampleRevision = (domainExperimentId: string, sampleId: string, revisionId: string) =>
    apiData(api.get<DomainSampleRevision>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/samples/${encodeURIComponent(sampleId)}/revisions/${encodeURIComponent(revisionId)}`,
    ));

export type ReferenceMoleculeType = 'dna' | 'rna';
export type ReferenceTopology = 'linear' | 'circular' | 'mixed' | 'unknown';

export interface DomainReference {
    id: string;
    global_domain_experiment_id: string;
    name: string;
    current_revision_id: string | null;
    head_generation: number;
    archived_at: string | null;
    created_at: string;
    updated_at: string;
    reopen_destination: {
        surface: 'molbio-ngs-reference';
        params: { reference_id: string };
    };
}

export interface DomainReferenceRevision {
    id: string;
    reference_id: string;
    global_domain_experiment_id: string;
    revision_number: number;
    parent_revision_id: string | null;
    schema_name: string;
    schema_version: string;
    payload: {
        schema: 'bms.molbio-ngs.reference-revision.v1';
        reference_id: string;
        revision_number: number;
        parent_revision_id: string | null;
        head_generation: number;
        canonical_fasta: { sha256: string; size_bytes: number; media_type: 'text/x-fasta; charset=us-ascii' };
        contigs: Array<{ name: string; length: number; sequence_sha256: string }>;
        contig_manifest_sha256: string;
        normalized_sequence_sha256: string | null;
        molecule_type: ReferenceMoleculeType;
        topology: ReferenceTopology;
        coordinate_contract: string;
        source_provenance: Record<string, unknown>;
    };
    payload_sha256: string;
    canonical_fasta_sha256: string;
    canonical_fasta_size_bytes: number;
    contig_manifest_sha256: string;
    normalized_sequence_sha256: string | null;
    molecule_type: ReferenceMoleculeType;
    topology: ReferenceTopology;
    coordinate_contract: string;
    created_at: string;
    created_by: string | null;
    reopen_destination: {
        surface: 'molbio-ngs-reference-revision';
        params: { reference_id: string; revision_id: string };
    };
}

export interface CreateDomainReferenceRequest {
    global_domain_experiment_id: string;
    name: string;
    fasta: string;
    molecule_type: ReferenceMoleculeType;
    topology: ReferenceTopology;
    coordinate_contract: string;
    source_provenance: Record<string, unknown>;
    idempotency_key: string;
}

export interface CreateDomainReferenceRevisionRequest {
    fasta: string;
    molecule_type: ReferenceMoleculeType;
    topology: ReferenceTopology;
    coordinate_contract: string;
    source_provenance: Record<string, unknown>;
    expected_head_generation: number;
    parent_revision_id: string;
    idempotency_key: string;
}

export interface ImportMolBioReferenceRequest {
    global_domain_experiment_id: string;
    sequence_id: string;
    molecular_revision_id: string;
    name: string;
    molecule_type: ReferenceMoleculeType;
    topology: ReferenceTopology;
    coordinate_contract: string;
    idempotency_key: string;
}

export interface ImportBrowserReferenceRequest {
    global_domain_experiment_id: string;
    entry: {
        id: string;
        name: string;
        source: 'fasta' | 'path';
        fasta?: string | null;
        path?: string | null;
        createdAt: string;
        updatedAt: string;
    };
    name: string;
    molecule_type: ReferenceMoleculeType;
    topology: ReferenceTopology;
    coordinate_contract: string;
    idempotency_key: string;
}

export const createMolBioNgsReference = (payload: CreateDomainReferenceRequest) =>
    apiData(api.post<DomainReferenceRevision>('/api/molbio-ngs/references', payload));
export const fetchMolBioNgsReferences = (domainExperimentId: string) =>
    apiData(api.get<DomainReference[]>('/api/molbio-ngs/references', {
        params: { global_domain_experiment_id: domainExperimentId },
    }));
export const fetchMolBioNgsReference = (referenceId: string) =>
    apiData(api.get<DomainReference>(`/api/molbio-ngs/references/${encodeURIComponent(referenceId)}`));
export const createMolBioNgsReferenceRevision = (referenceId: string, payload: CreateDomainReferenceRevisionRequest) =>
    apiData(api.post<DomainReferenceRevision>(
        `/api/molbio-ngs/references/${encodeURIComponent(referenceId)}/revisions`, payload,
    ));
export const fetchMolBioNgsReferenceRevisions = (referenceId: string) =>
    apiData(api.get<DomainReferenceRevision[]>(
        `/api/molbio-ngs/references/${encodeURIComponent(referenceId)}/revisions`,
    ));
export const fetchMolBioNgsReferenceRevision = (referenceId: string, revisionId: string) =>
    apiData(api.get<DomainReferenceRevision>(
        `/api/molbio-ngs/references/${encodeURIComponent(referenceId)}/revisions/${encodeURIComponent(revisionId)}`,
    ));
export const importMolBioNgsReferenceRevision = (payload: ImportMolBioReferenceRequest) =>
    apiData(api.post<DomainReferenceRevision>('/api/molbio-ngs/references/from-molbio-revision', payload));
export const importMolBioNgsBrowserReference = (payload: ImportBrowserReferenceRequest) =>
    apiData(api.post<DomainReferenceRevision>('/api/molbio-ngs/references/import-browser-entry', payload));
export const archiveMolBioNgsReference = (
    referenceId: string,
    payload: { expected_head_generation: number; idempotency_key: string },
) => apiData(api.post<DomainReference>(
    `/api/molbio-ngs/references/${encodeURIComponent(referenceId)}/archive`, payload,
));

export interface ExternalMemberReceipt {
    schema: 'bms.molbio-ngs.external-member-receipt.v1';
    receipt_id: string;
    source_store_id: string;
    entity_kind: string;
    entity_id: string;
    source_generation_or_revision: string;
    content_digest: string;
    source_schema: string;
    availability: 'available' | 'archived' | 'unavailable';
    reopen_destination: Record<string, unknown>;
    created_at: string;
}

const issueMemberReceipt = <T extends Record<string, unknown>>(path: string, payload: T) =>
    apiData(api.post<ExternalMemberReceipt>(`/api/molbio-ngs/member-receipts/${path}`, payload));
const issueDomainMemberReceipt = <T extends Record<string, unknown>>(
    domainExperimentId: string,
    path: string,
    payload: T,
) => apiData(api.post<ExternalMemberReceipt>(
    `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/member-receipts/${path}`,
    payload,
));

export const issueReferenceRevisionMemberReceipt = (
    domainExperimentId: string,
    payload: { reference_id: string; revision_id: string },
) => issueDomainMemberReceipt(domainExperimentId, 'ngs-reference-revisions', payload);
export const issueMolecularRevisionMemberReceipt = (payload: { sequence_id: string; revision_id: string }) =>
    issueMemberReceipt('molecular-revisions', payload);
export const issuePrimerRevisionMemberReceipt = (payload: { primer_id: string; revision_id: string }) =>
    issueMemberReceipt('primer-revisions', payload);
export const issuePcrRevisionMemberReceipt = (payload: { experiment_id: string; revision_id: string }) =>
    issueMemberReceipt('pcr-experiment-revisions', payload);
export const issueMolecularOperationMemberReceipt = (payload: { operation_id: string }) =>
    issueMemberReceipt('molecular-operations', payload);
export const issueComparisonPanelMemberReceipt = (payload: { panel_id: string; panel_version: number }) =>
    issueMemberReceipt('ngs-comparison-panels', payload);
export const issueEvidenceAssessmentMemberReceipt = (
    domainExperimentId: string,
    payload: { evidence_id: string },
) => issueDomainMemberReceipt(domainExperimentId, 'ngs-evidence-assessments', payload);

export interface EvidenceAssessment {
    evidence_id: string;
    global_domain_experiment_id: string;
    state_revision_id: string;
    sample_revision_id: string | null;
    receipt_ids: {
        ngs_job: string;
        ngs_result_manifest: string;
        ngs_reference_revision: string;
        ont_instrument_run: string | null;
        molecular_revision: string | null;
        ngs_comparison_panel: string | null;
    };
    assessment_rule_id: string;
    scientific_assessment: 'PASS' | 'FAIL' | 'REVIEW';
    job_lifecycle_state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
    manifest_integrity: 'valid' | 'invalid' | 'unavailable';
    raw_manifest_sha256: string;
    notes: string | null;
    wrapper_sha256: string;
    created_at: string;
    created_by: string | null;
    reopen_destination: {
        surface: 'molbio-ngs-evidence-assessment';
        params: { global_domain_experiment_id: string; evidence_id: string };
    };
}

export interface EvidenceAssessmentRequest {
    state_revision_id: string;
    sample_revision_id?: string | null;
    ngs_job_receipt_id: string;
    ngs_result_manifest_receipt_id: string;
    ngs_reference_revision_receipt_id: string;
    ont_instrument_run_receipt_id?: string | null;
    molecular_revision_receipt_id?: string | null;
    ngs_comparison_panel_receipt_id?: string | null;
    assessment_rule_id: string;
    notes?: string | null;
    idempotency_key: string;
}

export const attachMolBioNgsJobEvidence = (
    domainExperimentId: string,
    payload: { job_id: string; idempotency_key: string },
) => apiData(api.post<{ ngs_job: ExternalMemberReceipt; ngs_result_manifest: ExternalMemberReceipt }>(
    `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/evidence/attach-job`, payload,
));
export const attachMolBioNgsInstrumentRunEvidence = (
    domainExperimentId: string,
    payload: {
        state_revision_id: string;
        run_id: string;
        observed_generation: number;
        idempotency_key: string;
    },
) => apiData(api.post<ExternalMemberReceipt>(
    `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/evidence/attach-instrument-run`, payload,
));
export const assessMolBioNgsEvidence = (domainExperimentId: string, payload: EvidenceAssessmentRequest) =>
    apiData(api.post<EvidenceAssessment>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/evidence/assess`, payload,
    ));
export const fetchMolBioNgsEvidence = (domainExperimentId: string) =>
    apiData(api.get<EvidenceAssessment[]>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/evidence`,
    ));
export const fetchMolBioNgsEvidenceAssessment = (domainExperimentId: string, evidenceId: string) =>
    apiData(api.get<EvidenceAssessment>(
        `/api/molbio-ngs/experiments/${encodeURIComponent(domainExperimentId)}/evidence/${encodeURIComponent(evidenceId)}`,
    ));

export interface MolecularRevision {
    document_id: string;
    sequence_id: string;
    document_kind: string;
    document_name: string;
    revision_id: string;
    revision_number: number;
    parent_revision_id: string | null;
    change_kind: string;
    relation: 'current' | 'historical';
    snapshot: Record<string, unknown>;
    content_sha256: string;
    content_length: number;
    operation_id: string | null;
    provenance: Record<string, unknown>;
    created_at: string;
    created_by: string | null;
    reopen_destination: {
        surface: 'molbio-sequence-revision';
        params: { sequence_id: string; revision_id: string };
    };
}

export const fetchMolecularRevisions = (sequenceId: string, limit = 100) =>
    apiData(api.get<MolecularRevision[]>(
        `/api/sequences/${encodeURIComponent(sequenceId)}/revisions`, { params: { limit } },
    ));
export const fetchMolecularRevision = (sequenceId: string, revisionId: string) =>
    apiData(api.get<MolecularRevision>(
        `/api/sequences/${encodeURIComponent(sequenceId)}/revisions/${encodeURIComponent(revisionId)}`,
    ));

export interface PcrExperimentRevision {
    id: string;
    experiment_id: string;
    revision_number: number;
    payload_sha256: string;
    parent_revision_id: string | null;
    relation: 'current' | 'historical';
    operation_id: string;
    template_document_id: string | null;
    template_revision_id: string | null;
    template_sha256: string;
    template_snapshot: Record<string, unknown>;
    forward_primer_snapshot: Record<string, unknown>;
    reverse_primer_snapshot: Record<string, unknown>;
    tm_model_revision_id: string;
    tm_snapshot: Record<string, unknown>;
    polymerase_preset_revision_id: string | null;
    polymerase_snapshot: Record<string, unknown> | null;
    reaction_settings: Record<string, unknown>;
    cycling_assumptions: Record<string, unknown>;
    product_document_id: string | null;
    product_revision_id: string | null;
    product_snapshot: Record<string, unknown>;
    warnings: string[];
    notes: string | null;
    review_state: 'draft' | 'in_review' | 'approved' | 'rejected';
    provenance: Record<string, unknown>;
    created_by: string | null;
    created_at: string;
    reopen_destination: {
        surface: 'molbio-pcr-experiment-revision';
        params: { experiment_id: string; revision_id: string };
    };
}

export interface PcrExperimentListItem {
    id: string;
    name: string;
    review_state: string;
    current_revision_id: string | null;
    created_at: string;
    updated_at: string;
    current_revision: Omit<PcrExperimentRevision, 'parent_revision_id' | 'relation' | 'reopen_destination'> | null;
}

export interface PcrOperationRequest {
    sequence_id?: string;
    name?: string;
    sequence?: string;
    sequence_type?: 'dna' | 'rna';
    is_circular: boolean;
    primer_fwd: string;
    primer_rev: string;
    new_name: string;
    save: boolean;
    persist_experiment: boolean;
    tm_settings: PrimerTmSettings;
}

export interface PcrOperationResponse {
    sequence: NucleotideSequence | null;
    product: {
        sequence: string;
        start: number;
        end: number;
        length: number;
        wraps_origin: boolean;
    } | null;
    message: string;
    experiment_id: string | null;
    experiment_revision_id: string | null;
    operation_id: string | null;
    reused: boolean;
}

export const runPcrOperation = (payload: PcrOperationRequest) =>
    apiData(api.post<PcrOperationResponse>('/api/molbio/pcr', payload));

export const fetchPcrExperiments = (limit = 100) =>
    apiData(api.get<{ items: PcrExperimentListItem[]; count: number; limit: number }>(
        '/api/molbio/pcr-experiments', { params: { limit } },
    ));
export const fetchPcrExperimentRevisions = (experimentId: string, limit = 100) =>
    apiData(api.get<PcrExperimentRevision[]>(
        `/api/molbio/pcr-experiments/${encodeURIComponent(experimentId)}/revisions`, { params: { limit } },
    ));
export const fetchPcrExperimentRevision = (experimentId: string, revisionId: string) =>
    apiData(api.get<PcrExperimentRevision>(
        `/api/molbio/pcr-experiments/${encodeURIComponent(experimentId)}/revisions/${encodeURIComponent(revisionId)}`,
    ));
export const updatePcrExperimentReviewState = (
    experimentId: string,
    payload: {
        review_state: 'draft' | 'in_review' | 'approved' | 'rejected';
        notes?: string | null;
        provenance?: Record<string, unknown>;
    },
) => apiData(api.patch<Record<string, unknown>>(
    `/api/molbio/pcr-experiments/${encodeURIComponent(experimentId)}/review-state`, payload,
));

export interface OntRunSummary {
    run_id: string;
    position_id: string;
    status: 'armed' | 'starting' | 'running' | 'stopping' | 'stopped' | 'completed' | 'failed' | 'unknown';
    observed_at: string;
    observed_generation: number;
    created_at: string;
    sample_id: string | null;
    experiment_group: string | null;
    output_counts: { fastq: number; pod5: number; bam: number };
    terminal_manifest_sha256: string | null;
    reopen_destination: {
        surface: 'ont-instrument-run-generation';
        params: { run_id: string; observed_generation: number };
    };
}

export interface OntRunGeneration extends OntRunSummary {
    event_id: string;
    event_type: string;
}

export type OntRawSignalPreference = 'auto' | 'pod5' | 'blow5';
export type OntRawSignalReadiness = 'ready' | 'preparable' | 'unavailable';
export interface OntRawSignalMode {
    state: OntRawSignalReadiness;
    reason_code: string;
    representation_id: string | null;
}
export interface OntRawSignalRepresentation {
    representation_id: string;
    run_id: string;
    observed_generation: number;
    role: 'source' | 'derived';
    source_kind: string;
    format: 'pod5' | 'slow5' | 'blow5';
    source_fidelity: string;
    state: OntRawSignalReadiness;
    reason_code: string;
    manifest_sha256: string;
    artifact_count: number;
    artifacts: Array<{
        artifact_id?: string;
        kind?: string;
        bytes?: number;
        sha256?: string;
        partition_fingerprint?: string;
        read_count?: number;
    }>;
    read_count: number | null;
    profile_id: string | null;
    compression: Record<string, unknown>;
    parent_representation_ids: string[];
    parent_manifest_sha256s: string[];
    runtime_identity: Record<string, unknown>;
    validation_receipts: {
        adjacent_index?: boolean;
        semantic?: {
            status?: string;
            mapping_contract?: string;
            signal_samples?: string;
            read_count?: number;
            partition_count?: number;
            indexed_lookup_count?: number;
            duplicate_read_ids?: number;
            total_signal_samples_compared?: number;
            routing_sha256?: string;
            partition_counts?: Record<string, number>;
        };
        [key: string]: unknown;
    };
    published_at: string | null;
    created_at: string;
    validation: {
        source_identity_closed: boolean;
        adjacent_index_validated: boolean;
        semantic_contract_validated: boolean;
    };
}
export interface OntRawSignalCapabilities {
    run_id: string;
    observed_generation: number;
    representation_preference: OntRawSignalPreference;
    selected_representation_id: string | null;
    selected_format: 'pod5' | 'blow5' | null;
    selection_reason_code: string;
    representations: OntRawSignalRepresentation[];
    modes: Record<'pod5_direct' | 'blow5_indexed' | 'raw_waveform' | 'signal_to_read' | 'signal_to_reference' | 'signal_pileup' | 'igv', OntRawSignalMode>;
}

export interface OntRawSignalWaveform {
    lookup_id: string;
    run_id: string;
    observed_generation: number;
    representation_id: string;
    read_id: string;
    state: 'requested' | 'running' | 'ready' | 'failed';
    reason_code: string;
    sample_count: number | null;
    samples: number[] | null;
}

export interface OntExternalPod5Candidate {
    candidate_id: string;
    display_name: string;
    size_bytes: number;
    modified_at_ns: number;
}

export interface OntExternalPod5Registration {
    run_id: string;
    observed_generation: number;
    representation: OntRawSignalRepresentation;
    already_registered: boolean;
}

export const fetchOntInstrumentRuns = (limit = 100) =>
    apiData(api.get<OntRunSummary[]>('/api/ont/runs', { params: { limit } }));
export const fetchOntInstrumentRunGeneration = (runId: string, observedGeneration: number) =>
    apiData(api.get<OntRunGeneration>(
        `/api/ont/runs/${encodeURIComponent(runId)}/generations/${encodeURIComponent(String(observedGeneration))}`,
    ));
export const fetchOntExternalPod5Candidates = () =>
    apiData(api.get<{ candidates: OntExternalPod5Candidate[] }>(
        '/api/ont/raw-signal/external-pod5-candidates',
    ));
export const registerOntExternalPod5Candidate = (
    candidateId: string,
    experimentGroup: string,
    sampleId?: string,
) => apiData(api.post<OntExternalPod5Registration>(
    '/api/ont/raw-signal/external-pod5-candidates/register',
    {
        candidate_id: candidateId,
        experiment_group: experimentGroup,
        sample_id: sampleId || null,
    },
));
export const fetchOntRawSignalCapabilities = (
    runId: string,
    observedGeneration: number,
    representationPreference: OntRawSignalPreference,
) => apiData(api.get<OntRawSignalCapabilities>(
    `/api/ont/runs/${encodeURIComponent(runId)}/generations/${encodeURIComponent(String(observedGeneration))}/raw-signal`,
    { params: { representation_preference: representationPreference } },
));
export const requestOntRawSignalWaveform = (
    runId: string,
    observedGeneration: number,
    representationId: string,
    readId: string,
) => apiData(api.post<OntRawSignalWaveform>(
    `/api/ont/runs/${encodeURIComponent(runId)}/generations/${encodeURIComponent(String(observedGeneration))}/raw-signal/waveforms`,
    { representation_id: representationId, read_id: readId },
));
export const fetchOntRawSignalWaveform = (lookupId: string) =>
    apiData(api.get<OntRawSignalWaveform>(`/api/ont/raw-signal/waveforms/${encodeURIComponent(lookupId)}`));
export const requestOntBlow5Preparation = (
    runId: string,
    observedGeneration: number,
    sourceRepresentationId: string,
    representationPreference: 'auto' | 'blow5' = 'auto',
) => apiData(api.post<{
    job_id: string;
    state: string;
    reason_code: string;
    profile_id: string;
}>(`/api/ont/runs/${encodeURIComponent(runId)}/generations/${encodeURIComponent(String(observedGeneration))}/raw-signal/derive-blow5`, {
    source_representation_id: sourceRepresentationId,
    consumer_id: 'ont-instrument-panel',
    representation_preference: representationPreference,
}));

export const fetchFullJob = (jobId: string) =>
    apiData(api.get<Job>(`/api/jobs/${encodeURIComponent(jobId)}`));

export type OntSignalCapabilityState = 'ready' | 'preparable' | 'unavailable' | 'independent';
export type OntSignalMappingMode = 'signal_to_read' | 'signal_to_reference';
export type OntSignalViewMode = 'read' | 'reference' | 'pileup';
export type OntSignalJobState = 'requested' | 'running' | 'ready' | 'failed' | 'cancelled' | string;

export interface OntSignalCapabilityMode {
    state: OntSignalCapabilityState;
    reason_code: string;
}

// ============================================================
// ONT SIGNAL WORKBENCH API
// ============================================================
export interface OntSignalWorkbenchCapabilities {
    run_id: string;
    observed_generation: number;
    resolved: {
        raw_representation_id: string | null;
        move_source_id: string | null;
        mapping_profile_id: string | null;
        calibration_job_id: string | null;
        calibration_artifact_id: string | null;
        signal_to_read_mapping_job_id: string | null;
        signal_to_reference_mapping_job_id: string | null;
    };
    modes: Record<'igv' | 'raw_waveform' | 'signal_to_read' | 'signal_to_reference' | 'signal_pileup', OntSignalCapabilityMode>;
}

export interface OntSignalCapabilityAuthority {
    alignment_job_id: string;
    alignment_session_id: string;
    reference_revision_id: string;
}

export interface OntExternalMoveBamCandidate {
    candidate_id: string;
    display_name: string;
    size_bytes: number;
    modified_at_ns: number;
}

export interface OntMoveTableSource {
    move_source_id: string;
    attempt_number: number;
    predecessor_move_source_id: string | null;
    run_id: string;
    observed_generation: number;
    raw_representation_id: string;
    artifact_id: string;
    artifact_sha256: string;
    artifact_size_bytes: number;
    bam_header_sha256: string | null;
    record_count: number | null;
    unique_read_count: number | null;
    tag_counts: { mv: number | null; ts: number | null; ns: number | null };
    basecall_model_id: string | null;
    molecule_type: 'dna' | 'rna';
    source_job_id: string | null;
    external_registration_receipt_id: string | null;
    source_runtime_identity: Record<string, unknown>;
    read_inventory_sha256: string | null;
    state: OntSignalJobState;
    reason_code: string;
    validation_receipt: Record<string, unknown>;
    created_at: string;
    validated_at: string | null;
}

export interface OntSignalCalibrationArtifact {
    calibration_artifact_id: string;
    raw_representation_id: string;
    move_source_id: string;
    basecall_model_id: string;
    sample_selection: { method: string; requested_count: number; selected_count: number; intersection_count: number; read_ids: string[]; selection_sha256: string };
    recommended_kmer_length: number;
    recommended_signal_move_offset: number;
    score_evidence: Array<Record<string, unknown>>;
    runtime_identity: Record<string, unknown>;
    parent_sha256s: Record<string, unknown>;
    artifact_sha256: string;
    created_at: string;
}

export interface OntSignalCalibrationJob {
    calibration_job_id: string;
    run_id: string;
    observed_generation: number;
    raw_representation_id: string;
    move_source_id: string;
    sample_count: number;
    request_fingerprint: string;
    state: OntSignalJobState;
    reason_code: string;
    attempt: number;
    resource_snapshot: Record<string, unknown>;
    stage_receipts: Record<string, unknown>;
    failure_code: string | null;
    failure_message: string | null;
    artifact: OntSignalCalibrationArtifact | null;
    created_at: string;
    updated_at: string;
    completed_at: string | null;
}

export interface OntSignalMappingProfile {
    mapping_profile_id: string;
    name: string;
    molecule_type: 'dna' | 'rna';
    basecall_model_id: string;
    kmer_length: number;
    signal_move_offset: number;
    base_shift_value: number;
    parameter_source: 'approved_calibration';
    calibration_artifact_id: string;
    primary_alignment_policy: 'primary_only';
    minimum_mapq: 0;
    include_supplementary: false;
    read_set_selection: 'immutable_full_set';
    approval_receipt: Record<string, unknown>;
    approved_at: string;
    approved_by: string | null;
}

export interface OntSignalMappingArtifact {
    mapping_artifact_id: string;
    mapping_job_id: string;
    kind: 'reform_paf' | 'realign_paf';
    sha256: string;
    size_bytes: number;
    media_type: string;
    parent_identities: Record<string, unknown>;
    runtime_identity: Record<string, unknown>;
    validation_receipt: Record<string, unknown>;
    created_at: string;
}

export interface OntSignalMappingJob {
    mapping_job_id: string;
    mode: OntSignalMappingMode;
    run_id: string;
    observed_generation: number;
    raw_representation_id: string;
    move_source_id: string;
    mapping_profile_id: string;
    reference_revision_id: string | null;
    alignment_job_id: string | null;
    alignment_session_id: string | null;
    parent_mapping_job_id: string | null;
    request_fingerprint: string;
    state: OntSignalJobState;
    reason_code: string;
    attempt: number;
    resource_snapshot: Record<string, unknown>;
    stage_receipts: Record<string, unknown>;
    failure_code: string | null;
    failure_message: string | null;
    artifacts: OntSignalMappingArtifact[];
    created_at: string;
    updated_at: string;
    completed_at: string | null;
}

export interface OntSignalViewArtifact {
    artifact_id: string;
    sha256: string;
    size_bytes: number;
    media_type: string;
    url?: string | null;
}

export interface OntSignalViewJob {
    view_job_id: string;
    mapping_artifact_id: string;
    mode: OntSignalViewMode;
    read_id: string | null;
    reference_region: { contig: string; start: number; end: number } | null;
    render_params: OntSignalRenderParamsResponse;
    request_fingerprint: string;
    state: OntSignalJobState;
    reason_code: string;
    output_manifest: {
        schema?: string | null;
        artifacts: OntSignalViewArtifact[];
        command?: Record<string, unknown> | null;
        network?: string | null;
    };
    render_receipt: Record<string, unknown>;
    failure_code: string | null;
    failure_message: string | null;
    created_at: string;
    updated_at: string;
    completed_at: string | null;
}

export interface OntSignalRenderParams {
    strand: 'forward' | 'reverse';
    signal_units: 'pA' | 'raw_adc';
    scale: 'none' | 'medmad' | 'znorm' | 'scaledpA';
    base_shift_source: 'profile' | 'explicit';
    base_shift_value: number;
    fixed_width: boolean;
    base_width: number;
    point_size: number;
    base_limit: number;
    signal_sample_limit: number;
    pileup_read_limit: number;
    loose_bound: boolean;
    show_samples: boolean;
    show_base_colours: boolean;
    remove_signal_outliers: boolean;
    managed_bed_artifact_id: string | null;
}

export const DEFAULT_ONT_SIGNAL_RENDER_PARAMS: OntSignalRenderParams = {
    strand: 'forward',
    signal_units: 'pA',
    scale: 'none',
    base_shift_source: 'profile',
    base_shift_value: 0,
    fixed_width: false,
    base_width: 10,
    point_size: 0.5,
    base_limit: 1000,
    signal_sample_limit: 100_000,
    pileup_read_limit: 20,
    loose_bound: false,
    show_samples: true,
    show_base_colours: true,
    remove_signal_outliers: false,
    managed_bed_artifact_id: null,
};

export interface OntSignalRenderParamsResponse extends OntSignalRenderParams {
    managed_bed_source_job_id?: string | null;
    managed_bed_sha256?: string | null;
    managed_bed_size_bytes?: number | null;
}

export type OntSignalViewerAlignmentDisplayMode = 'EXPANDED' | 'SQUISHED' | 'FULL';
export type OntSignalViewerAlignmentColorBy = 'none' | 'strand' | 'firstOfPairStrand' | 'pairOrientation' | 'tlen' | 'unexpectedPair' | 'basemod' | 'basemod2';
export type OntSignalViewerAlignmentGroupBy = 'none' | 'strand' | 'firstOfPairStrand' | 'pairOrientation' | 'mateChr' | 'chimeric' | 'supplementary' | 'readOrder';

export interface OntSignalViewerIgvUpdateState {
    alignment_display_mode: OntSignalViewerAlignmentDisplayMode;
    alignment_color_by: OntSignalViewerAlignmentColorBy;
    alignment_group_by: OntSignalViewerAlignmentGroupBy;
    reads_track_loaded: boolean;
}

export type OntSignalComparisonProfileId = 'dna-r9-min' | 'dna-r9-prom' | 'rna-r9-min' | 'rna-r9-prom' | 'dna-r10-min' | 'dna-r10-prom' | 'rna004-min' | 'rna004-prom';
export type OntSignalComparisonCompatibility = 'matched_profile' | 'approximate_profile' | 'legacy_unknown' | 'incompatible';

export interface OntSignalComparisonSimulationSettings {
    profile_id: OntSignalComparisonProfileId;
    seed: number;
}

export interface OntSignalComparisonProfileFixed {
    molecule_type: 'dna' | 'rna';
    flow_cell_generation: string;
    device_class: 'MinION' | 'PromethION';
    pore_model_identity: string;
    kmer_length: number;
    digitisation: number;
    sample_rate: number;
    translocation_speed: number;
    range: number;
    offset_mean: number;
    offset_standard_deviation: number;
    median_before_mean: number;
    median_before_standard_deviation: number;
    dwell_mean: number;
    dwell_standard_deviation: number;
    model_quality_warning: string | null;
    compatibility_floor: Exclude<OntSignalComparisonCompatibility, 'legacy_unknown' | 'incompatible'>;
}

export interface OntSignalComparisonWorkflowFixed {
    simulation_mode: 'ideal'; full_contigs: true; amplitude_noise_factor: 0; dwell_noise: 0;
    prefix: false; input_sequence_count: 1; simulated_signal_record_count: 1;
    signal_units: 'pA'; real_read_count: 1; reference_hypothesis_count: 1;
    sequence_basis: 'managed_reference'; threads: 1; batch_size: 1;
}
export interface OntSignalComparisonCompatibilityEvidence {
    mapping_profile_molecule_type: string | null; mapping_profile_basecall_model_id: string | null;
    mapping_profile_kmer_length: number | null; move_source_molecule_type: string | null;
    move_source_basecall_model_id: string | null; move_source_runtime_authority: string | null;
    raw_sample_rate: string | number | null; raw_digitisation: string | number | null;
    raw_range: string | number | null; run_flow_cell_generation: string | null; run_device_class: string | null;
}
export interface OntSignalComparisonCompatibilityReceipt {
    disposition: OntSignalComparisonCompatibility; evidence: OntSignalComparisonCompatibilityEvidence;
    missing_authorities: string[]; mismatches: string[];
}
export interface OntSignalComparisonEffectiveSettings {
    schema: 'bms.ont-squigulator-ideal-comparison-effective.v1';
    operator_owned: OntSignalComparisonSimulationSettings & OntSignalComparisonRenderParams;
    profile_id: OntSignalComparisonProfileId;
    profile: OntSignalComparisonProfileFixed;
    workflow_fixed: OntSignalComparisonWorkflowFixed;
    compatibility_floor: Exclude<OntSignalComparisonCompatibility, 'legacy_unknown' | 'incompatible'>;
    warnings: string[];
    upstream: { name: string; version: string; commit: string; release_source_asset: string; release_source_asset_sha256: string };
    compatibility_disposition: OntSignalComparisonCompatibility;
    compatibility_evidence: OntSignalComparisonCompatibilityReceipt;
}

export type OntSignalComparisonPointSize = 0.5 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10;

export interface OntSignalComparisonRenderParams {
    scale: 'none' | 'medmad' | 'znorm';
    point_size: OntSignalComparisonPointSize;
    fixed_width: boolean;
    base_width: number;
    base_limit: number;
    signal_sample_limit: number;
    show_samples: boolean;
    show_base_colours: boolean;
    remove_signal_outliers: boolean;
}

export interface OntSignalComparisonRequest {
    viewer_session_id: string;
    expected_viewer_revision: number;
    mapping_artifact_id: string;
    selected_read_id: string;
    reference_contig: string;
    reference_start: number;
    reference_end: number;
    simulation_settings: OntSignalComparisonSimulationSettings;
    render_params: OntSignalComparisonRenderParams;
}
export interface OntSignalComparisonCreateRequest extends OntSignalComparisonRequest { preview_digest: string }

export interface OntSignalComparisonPreview {
    viewer_session_id: string;
    viewer_session_revision: number;
    run_id: string;
    observed_generation: number;
    raw_representation_id: string;
    raw_manifest_sha256: string;
    mapping_artifact_id: string;
    mapping_artifact_sha256: string;
    mapping_job_id: string;
    mapping_profile_id: string;
    reference_revision_id: string;
    reference_artifact_id: string;
    reference_fasta_sha256: string;
    reference_topology: string;
    coordinate_contract: string;
    selected_read_id: string;
    selected_read_span: { contig: string; start: number; end: number; strand: 'forward' | 'reverse' | '+' | '-' };
    simulation_orientation: 'forward' | 'reverse';
    derived_window: { contig: string; start: number; end: number };
    compatibility_disposition: OntSignalComparisonCompatibility;
    warnings: string[];
    effective_request: {
        authority: {
            viewer_session_id: string; viewer_session_revision: number; run_id: string; observed_generation: number;
            raw_representation_id: string; raw_manifest_sha256: string; mapping_artifact_id: string;
            mapping_artifact_sha256: string; mapping_job_id: string; mapping_profile_id: string;
            move_source_id: string; move_source_artifact_sha256: string; reference_revision_id: string;
            reference_artifact_id: string; reference_fasta_sha256: string; reference_topology: string;
            coordinate_contract: string; selected_read_id: string;
            selected_read_span: OntSignalComparisonPreview['selected_read_span'];
            simulation_orientation: 'forward' | 'reverse'; derived_window: { contig: string; start: number; end: number };
        };
        effective_settings: OntSignalComparisonEffectiveSettings;
        reference_interval: { contig: string; start: number; end: number };
    };
    preview_digest: string;
}

export interface OntSignalComparisonRawParentIdentity {
    sha256: string;
    index_sha256: string;
}
export interface OntSignalComparisonParentIdentities {
    reference_fasta_sha256: string;
    mapping_sha256: string;
    mapping_index_sha256: string;
    real_blow5: {
        routing_sha256: string | null;
        blow5: OntSignalComparisonRawParentIdentity[];
    };
    real_moves_sha256: string;
    raw_manifest_sha256: string;
    run_id: string;
    observed_generation: number;
    selected_read_id: string;
}
export interface OntSignalComparisonReceiptAuthority {
    schema: string | null;
    content_sha256: string;
}

export interface OntSignalComparisonArtifact {
    artifact_id: string;
    kind: 'simulation_input_fasta' | 'simulation_coordinate_map' | 'simulated_blow5'
        | 'simulated_blow5_index' | 'simulated_read_fasta' | 'simulated_read_id_map'
        | 'simulated_source_paf' | 'simulated_normalized_paf' | 'simulated_source_sam'
        | 'simulated_normalized_sam' | 'comparison_html' | 'comparison_manifest';
    authority_class: 'simulated_derived' | 'comparison_derived';
    sha256: string;
    size_bytes: number;
    media_type: string;
    parent_identities: OntSignalComparisonParentIdentities;
    squigulator_runtime_identity: OntSignalComparisonRuntimeIdentity | null;
    squigualiser_runtime_identity: OntSignalComparisonRuntimeIdentity | null;
    validation_receipt: OntSignalComparisonReceiptAuthority;
    created_at: string;
}
export interface OntSignalComparisonRuntimeIdentity {
    stage: 'squigulator_producer' | 'squigualiser_comparison_renderer';
    image: string; image_digest: string; policy_sha256: string; wrapper_sha256: string;
}
export interface OntSignalComparisonExecutionReceipt {
    argv_sha256: string; returncode: 0; stdout_sha256: string; stdout_size_bytes: number;
    stderr_sha256: string; stderr_size_bytes: number; stderr_tail: string;
    container_name_sha256: string; runtime_identity: OntSignalComparisonRuntimeIdentity;
}
export interface OntSignalComparisonStageReceipts {
    squigulator_producer?: OntSignalComparisonExecutionReceipt | null;
    squigualiser_comparison_renderer?: OntSignalComparisonExecutionReceipt | null;
    lease_recoveries?: Array<{ recovered_at: string; expired_attempt: number; max_attempts: number }> | null;
}
export interface OntSignalComparisonManifestArtifact {
    kind: OntSignalComparisonArtifact['kind'];
    media_type: string;
    sha256: string;
    size_bytes: number;
    validation_receipt: OntSignalComparisonReceiptAuthority;
}
export interface OntSignalComparisonRuntimeIdentities {
    squigulator_producer?: OntSignalComparisonRuntimeIdentity;
    squigualiser_comparison_renderer?: OntSignalComparisonRuntimeIdentity;
}
export interface OntSignalComparisonOutputManifest {
    schema?: 'bms.ont-signal-comparison-manifest.v1' | null;
    parents?: OntSignalComparisonParentIdentities | null;
    runtime_identities?: OntSignalComparisonRuntimeIdentities | null;
    stage_receipts?: OntSignalComparisonStageReceipts | null;
    artifacts?: OntSignalComparisonManifestArtifact[] | null;
    producer?: OntSignalComparisonReceiptAuthority | null;
    renderer?: OntSignalComparisonReceiptAuthority | null;
}
export interface OntSignalComparisonResourceSnapshot {
    parents?: OntSignalComparisonParentIdentities | null;
}
export interface OntSignalComparisonReview {
    review_id: string;
    comparison_job_id: string;
    predecessor_review_id: string | null;
    review_question: string;
    required_outcome: 'approve' | 'reject' | 'record_only';
    note: string;
    reviewed_start: number;
    reviewed_end: number;
    comparison_html_artifact_id: string;
    comparison_html_sha256: string;
    comparison_request_fingerprint: string;
    reviewer_identity: string;
    created_at: string;
}
export interface OntSignalComparisonJob {
    comparison_job_id: string;
    predecessor_job_id: string | null;
    attempt_number: number;
    viewer_session_id: string;
    viewer_session_revision: number;
    run_id: string;
    observed_generation: number;
    raw_representation_id: string;
    mapping_artifact_id: string;
    reference_revision_id: string;
    selected_read_id: string;
    reference_contig: string;
    reference_start: number;
    reference_end: number;
    simulation_orientation: 'forward' | 'reverse';
    simulation_settings: OntSignalComparisonEffectiveSettings;
    sequence_basis: 'managed_reference';
    generated_read_id: string | null;
    render_params: OntSignalComparisonRenderParams;
    preview_digest: string;
    request_fingerprint: string;
    state: OntSignalJobState;
    reason_code: string;
    resource_snapshot: OntSignalComparisonResourceSnapshot;
    stage_receipts: OntSignalComparisonStageReceipts;
    output_manifest: OntSignalComparisonOutputManifest;
    failure_code: string | null;
    failure_message: string | null;
    artifacts: OntSignalComparisonArtifact[];
    created_at: string;
    updated_at: string;
    completed_at: string | null;
}

export interface OntSignalComparisonViewerSettings {
    simulation_settings: OntSignalComparisonSimulationSettings;
    render_params: OntSignalComparisonRenderParams;
}

export interface OntSignalViewerSignalUpdateState {
    mode: OntSignalViewMode | 'raw_waveform' | 'ideal_comparison';
    render_params: OntSignalRenderParams;
    view_job_id: string | null;
    read_mapping_job_id: string | null;
    reference_mapping_job_id: string | null;
    comparison_job_id?: string | null;
    comparison_preview_digest?: string | null;
    comparison_settings?: OntSignalComparisonViewerSettings | null;
    comparison_review_id?: string | null;
}

export interface OntSignalViewerIgvState extends Partial<OntSignalViewerIgvUpdateState> {
    alignment_job_id?: string | null;
    alignment_session_id?: string | null;
    reference_revision_id?: string | null;
    locus?: string | null;
}

export interface OntSignalViewerSignalState extends Partial<OntSignalViewerSignalUpdateState> {
    selected_read_id?: string | null;
    capabilities?: OntSignalWorkbenchCapabilities['modes'];
}

export interface OntSignalViewerSession {
    viewer_session_id: string;
    dataset_id: string;
    run_id: string;
    observed_generation: number;
    alignment_job_id: string | null;
    alignment_session_id: string | null;
    reference_revision_id: string | null;
    raw_representation_id: string | null;
    move_source_id: string | null;
    mapping_profile_id: string | null;
    contig: string | null;
    locus_start: number | null;
    locus_end: number | null;
    selected_read_id: string | null;
    igv_state: OntSignalViewerIgvState;
    signal_state: OntSignalViewerSignalState;
    revision: number;
    created_at: string;
    updated_at: string;
    reopen_url: string;
}

export interface OntSignalViewerSessionCreate {
    dataset_id: string;
    run_id: string;
    observed_generation: number;
    alignment_job_id: string | null;
    alignment_session_id: string | null;
    reference_revision_id: string | null;
    contig: string | null;
    locus_start: number | null;
    locus_end: number | null;
    selected_read_id: string | null;
    igv_state: OntSignalViewerIgvUpdateState;
    signal_state: OntSignalViewerSignalUpdateState;
}

export interface OntSignalViewerSessionUpdate {
    expected_revision: number;
    contig: string | null;
    locus_start: number | null;
    locus_end: number | null;
    selected_read_id: string | null;
    igv_state: OntSignalViewerIgvUpdateState;
    signal_state: OntSignalViewerSignalUpdateState;
}

const signalWorkbenchRoot = '/api/ont/signal-workbench';
export const fetchOntSignalWorkbenchCapabilities = (
    runId: string,
    observedGeneration: number,
    authority: OntSignalCapabilityAuthority | null,
) => apiData(api.get<OntSignalWorkbenchCapabilities>(
    `${signalWorkbenchRoot}/runs/${encodeURIComponent(runId)}/generations/${observedGeneration}/capabilities`,
    { params: authority || undefined },
));
export const fetchOntMoveSources = (runId: string, observedGeneration: number, signal?: AbortSignal) =>
    apiData(api.get<{ items: OntMoveTableSource[] }>(`${signalWorkbenchRoot}/runs/${encodeURIComponent(runId)}/generations/${observedGeneration}/move-sources`, { signal }));
export const fetchOntExternalMoveBamCandidates = () =>
    apiData(api.get<{ items: OntExternalMoveBamCandidate[] }>(`${signalWorkbenchRoot}/external-move-bam-candidates`));
export const registerOntExternalMoveBamCandidate = (
    runId: string,
    observedGeneration: number,
    request: {
        candidate_id: string;
        raw_representation_id: string;
        molecule_type: 'dna' | 'rna';
    },
) => apiData(api.post<OntMoveTableSource>(
    `${signalWorkbenchRoot}/runs/${encodeURIComponent(runId)}/generations/${observedGeneration}/external-move-bam-candidates/register`,
    request,
));
export const registerOntMoveSource = (runId: string, observedGeneration: number, request: {
    raw_representation_id: string;
    input_file_id: string;
    molecule_type: 'dna' | 'rna';
    source_job_id: string;
}) => apiData(api.post<OntMoveTableSource>(`${signalWorkbenchRoot}/runs/${encodeURIComponent(runId)}/generations/${observedGeneration}/move-sources`, request));
export const createOntFreshMoveSourceAttempt = (predecessorMoveSourceId: string) =>
    apiData(api.post<OntMoveTableSource>(
        `${signalWorkbenchRoot}/move-sources/${encodeURIComponent(predecessorMoveSourceId)}/fresh-attempt`,
        {},
    ));
export const fetchOntSignalMappingProfiles = () =>
    apiData(api.get<{ items: OntSignalMappingProfile[] }>(`${signalWorkbenchRoot}/mapping-profiles`));
export const createOntSignalMappingProfile = (request: {
    name: string;
    molecule_type: 'dna' | 'rna';
    basecall_model_id: string;
    kmer_length: number;
    signal_move_offset: number;
    base_shift_value: number;
    parameter_source: 'approved_calibration';
    calibration_artifact_id: string;
    primary_alignment_policy: 'primary_only';
    minimum_mapq: 0;
    include_supplementary: false;
    read_set_selection: 'immutable_full_set';
    approval_receipt: Record<string, unknown>;
    approved_by: string | null;
}) => apiData(api.post<OntSignalMappingProfile>(`${signalWorkbenchRoot}/mapping-profiles`, request));
export const createOntSignalCalibration = (runId: string, observedGeneration: number, request: {
    raw_representation_id: string;
    move_source_id: string;
    sample_count: number;
}) => apiData(api.post<OntSignalCalibrationJob>(`${signalWorkbenchRoot}/runs/${encodeURIComponent(runId)}/generations/${observedGeneration}/calibrations`, request));
export const fetchOntSignalCalibration = (calibrationJobId: string) =>
    apiData(api.get<OntSignalCalibrationJob>(`${signalWorkbenchRoot}/calibrations/${encodeURIComponent(calibrationJobId)}`));
export const cancelOntSignalCalibration = (calibrationJobId: string) =>
    apiData(api.post<OntSignalCalibrationJob>(`${signalWorkbenchRoot}/calibrations/${encodeURIComponent(calibrationJobId)}/cancel`));
export const createOntSignalMapping = (runId: string, observedGeneration: number, request: {
    mode: OntSignalMappingMode;
    raw_representation_id: string;
    move_source_id: string;
    mapping_profile_id: string;
    reference_revision_id: string | null;
    alignment_job_id: string | null;
    alignment_session_id: string | null;
}) => apiData(api.post<OntSignalMappingJob>(`${signalWorkbenchRoot}/runs/${encodeURIComponent(runId)}/generations/${observedGeneration}/mappings`, request));
export const fetchOntSignalMapping = (mappingJobId: string) =>
    apiData(api.get<OntSignalMappingJob>(`${signalWorkbenchRoot}/mappings/${encodeURIComponent(mappingJobId)}`));
export const cancelOntSignalMapping = (mappingJobId: string) =>
    apiData(api.post<OntSignalMappingJob>(`${signalWorkbenchRoot}/mappings/${encodeURIComponent(mappingJobId)}/cancel`));
export const createOntSignalView = (request: {
    mapping_artifact_id: string;
    mode: OntSignalViewMode;
    read_id: string | null;
    reference_contig: string | null;
    reference_start: number | null;
    reference_end: number | null;
    render_params: OntSignalRenderParams;
}) => apiData(api.post<OntSignalViewJob>(`${signalWorkbenchRoot}/views`, request));
export const fetchOntSignalView = (viewJobId: string) =>
    apiData(api.get<OntSignalViewJob>(`${signalWorkbenchRoot}/views/${encodeURIComponent(viewJobId)}`));
export const cancelOntSignalView = (viewJobId: string) =>
    apiData(api.post<OntSignalViewJob>(`${signalWorkbenchRoot}/views/${encodeURIComponent(viewJobId)}/cancel`));
export const fetchOntSignalViewArtifact = (viewJobId: string, artifactId: string) =>
    apiData(api.get<Blob>(`${signalWorkbenchRoot}/views/${encodeURIComponent(viewJobId)}/artifacts/${encodeURIComponent(artifactId)}`, {
        responseType: 'blob',
        withCredentials: false,
    }));
export const previewOntSignalIdealComparison = (request: OntSignalComparisonRequest) =>
    apiData(api.post<OntSignalComparisonPreview>(`${signalWorkbenchRoot}/comparisons/preview`, request));
export const createOntSignalIdealComparison = (request: OntSignalComparisonCreateRequest) =>
    apiData(api.post<OntSignalComparisonJob>(`${signalWorkbenchRoot}/comparisons`, request));
export const fetchOntSignalIdealComparison = (comparisonJobId: string, signal?: AbortSignal) =>
    apiData(api.get<OntSignalComparisonJob>(`${signalWorkbenchRoot}/comparisons/${encodeURIComponent(comparisonJobId)}`, { signal }));
export const cancelOntSignalIdealComparison = (comparisonJobId: string) =>
    apiData(api.post<OntSignalComparisonJob>(`${signalWorkbenchRoot}/comparisons/${encodeURIComponent(comparisonJobId)}/cancel`));
export const createFreshOntSignalIdealComparisonAttempt = (comparisonJobId: string) =>
    apiData(api.post<OntSignalComparisonJob>(`${signalWorkbenchRoot}/comparisons/${encodeURIComponent(comparisonJobId)}/fresh-attempt`));
export const fetchOntSignalComparisonArtifact = (comparisonJobId: string, artifactId: string) =>
    apiData(api.get<Blob>(`${signalWorkbenchRoot}/comparisons/${encodeURIComponent(comparisonJobId)}/artifacts/${encodeURIComponent(artifactId)}`, {
        responseType: 'blob', withCredentials: false,
    }));
export const createOntSignalComparisonReview = (comparisonJobId: string, request: {
    predecessor_review_id: string | null; review_question: string;
    required_outcome: OntSignalComparisonReview['required_outcome']; note: string;
    reviewed_start: number; reviewed_end: number;
}) => apiData(api.post<OntSignalComparisonReview>(`${signalWorkbenchRoot}/comparisons/${encodeURIComponent(comparisonJobId)}/reviews`, request));
export const fetchOntSignalComparisonReviews = (comparisonJobId: string) =>
    apiData(api.get<{ items: OntSignalComparisonReview[] }>(`${signalWorkbenchRoot}/comparisons/${encodeURIComponent(comparisonJobId)}/reviews`))
        .then((response) => response.items);

export const createOntSignalViewerSession = (request: OntSignalViewerSessionCreate) =>
    apiData(api.post<OntSignalViewerSession>(`${signalWorkbenchRoot}/viewer-sessions`, request));
export const fetchOntSignalViewerSession = (viewerSessionId: string) =>
    apiData(api.get<OntSignalViewerSession>(`${signalWorkbenchRoot}/viewer-sessions/${encodeURIComponent(viewerSessionId)}`));
export const updateOntSignalViewerSession = (
    viewerSessionId: string,
    request: OntSignalViewerSessionUpdate,
    signal?: AbortSignal,
) => apiData(api.patch<OntSignalViewerSession>(
    `${signalWorkbenchRoot}/viewer-sessions/${encodeURIComponent(viewerSessionId)}`,
    request,
    { signal },
));

export type ProjectHubSection = 'overview' | 'plasmids' | 'sequence-data' | 'experiments' | 'results' | 'activity';
export type ProjectHubExperimentKind = 'pcr' | 'restriction_digest' | 'alignment' | 'sequence_change' | 'ligation' | 'gibson' | 'golden_gate';
export type ProjectHubMapTone = 'accent' | 'success' | 'info' | 'warning' | 'secondary';

export interface ProjectHubMapSegment {
    start: number;
    end: number;
    tone: ProjectHubMapTone;
    label: string;
    feature_type: string;
    strand: 'forward' | 'reverse' | 'unknown';
}

export interface ProjectHubDNASequenceSummary {
    sequence_id: string;
    revision_id: string;
    receipt_id: string;
    receipt_sha256: string;
    content_digest: string;
    current_content_sha256?: string | null;
    source_store_id: string;
    schema_name: string;
    revision_number: number;
    name: string;
    description: string;
    availability: string;
    unavailable_reason: string | null;
    length_bp: number;
    gc_percent: number | null;
    feature_count: number;
    feature_labels: string[];
    cmv_promoter: boolean | null;
    neor_kanr: boolean | null;
    replication_origin_count: number | null;
    saved_experiment_count: number;
    molecule_type?: string;
    topology?: string;
    organism_host_context: string | null;
    project_tags: string[];
    project_notes: string;
    reopen_href: string;
    map_segments: ProjectHubMapSegment[];
}

export interface ProjectHubSequenceDataItem {
    id: string;
    plasmid_sequence_id: string;
    plasmid_name: string;
    kind: 'run' | 'read_set' | 'alignment' | 'clone_assessment' | 'viewer_evidence';
    title: string;
    summary: string;
    status: string;
    created_at: string;
    reopen_href: string;
}

export interface ProjectHubExperimentSummary {
    id: string;
    persistence: 'saved' | 'unsaved';
    kind: ProjectHubExperimentKind;
    plasmid_sequence_id: string;
    plasmid_sequence_ids: string[];
    plasmid_name: string;
    title: string;
    status: string;
    created_at: string;
    reopen_href: string | null;
    input_sequence_ids?: string[];
    output_sequence_ids?: string[];
}

export interface ProjectHubResultSummary {
    id: string;
    plasmid_sequence_id?: string;
    plasmid_name: string;
    type: string;
    status: string;
    owner: string;
    created_at: string;
    summary: string | null;
    reopen_href: string;
}

export interface ProjectHubActivitySummary {
    id: string;
    summary: string;
    occurred_at: string;
    technical_event_type: string;
    receipt_id: string;
    envelope_sha256: string;
}

export type ProjectHubPlasmidSummary = ProjectHubDNASequenceSummary;

export interface ProjectHubReadModel {
    schema: 'bms.project-hub.v1';
    project: {
        id: string;
        name: string;
        objective: string;
        lifecycle_state: string;
        created_at: string;
        plasmid_count: number;
        settings_href: string;
        add_plasmid_href: string;
    };
    identity: {
        workspace_id: string;
        global_experiment_id: string;
        domain_experiment_id: string;
        selected_state_revision_id: string;
        current_state_revision_id: string;
        state_head_generation: number;
        global_domain_revision_id: string;
        membership_graph_sha256: string;
        binding_status: string;
        adapter_status: string;
    };
    /** Compatibility field name. Items are DNA sequences; plasmid is one possible sequence role. */
    plasmids: ProjectHubDNASequenceSummary[];
    sequence_data: {
        items: ProjectHubSequenceDataItem[];
        import_href: string;
        launcher_href: string;
    };
    experiments: ProjectHubExperimentSummary[];
    results: ProjectHubResultSummary[];
    activity: ProjectHubActivitySummary[];
}

export interface ProjectHubPlasmidInfoDraft {
    name: string;
    molecule_type: string;
    topology: string;
    description: string;
    organism_host_context: string | null;
    project_tags: string[];
    project_notes: string;
}

export interface UpdateProjectHubPlasmidInfoRequest {
    expected_molecular_revision_id: string;
    expected_state_revision_id: string;
    expected_state_head_generation: number;
    idempotency_key: string;
    molecular_fields: Pick<ProjectHubPlasmidInfoDraft, 'name' | 'molecule_type' | 'topology' | 'description' | 'organism_host_context'>;
    project_metadata: Pick<ProjectHubPlasmidInfoDraft, 'project_tags' | 'project_notes'>;
}

const projectHubRoot = (projectId: string, experimentId: string, domainId: string) =>
    `/api/projects/${encodeURIComponent(projectId)}/experiments/${encodeURIComponent(experimentId)}/domains/${encodeURIComponent(domainId)}/project-hub`;

export const fetchProjectHub = (
    projectId: string,
    experimentId: string,
    domainId: string,
    stateRevisionId: string,
    signal?: AbortSignal,
) => apiData(api.get<ProjectHubReadModel>(projectHubRoot(projectId, experimentId, domainId), {
    params: { state_revision_id: stateRevisionId },
    signal,
}));

export const updateProjectHubPlasmidInfo = (
    projectId: string,
    experimentId: string,
    domainId: string,
    sequenceId: string,
    request: UpdateProjectHubPlasmidInfoRequest,
) => apiData(api.post<ProjectHubReadModel>(
    `${projectHubRoot(projectId, experimentId, domainId)}/plasmids/${encodeURIComponent(sequenceId)}/info`,
    request,
));
