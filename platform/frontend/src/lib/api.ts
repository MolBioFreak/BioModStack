import axios from 'axios';

// Use relative path - Vite's proxy handles /api -> localhost:8000
const API_BASE = '';

export const api = axios.create({
    baseURL: API_BASE,
});

// Types
export interface Job {
    id: string;
    name: string;
    status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
    model_id: string;
    mode: string;
    params: Record<string, any>;
    created_at: string;
    design_count: number;
    output_dir: string | null;
    error_message?: string | null;
    // Batch grouping for job sets
    batch_id?: string | null;
    batch_name?: string | null;
    // Parent-child relationship for exploration mode
    parent_job_id?: string | null;
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
}

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

// API functions
// API functions
export const fetchJobs = (params?: {
    status?: string;
    q?: string;
    limit?: number;
    offset?: number;
    include_children?: boolean;
}) => api.get<{ jobs: Job[]; total: number }>('/api/jobs', { params });
export const fetchSystemStatus = () => api.get<SystemStatus>('/api/gpu/status');
export const fetchJobById = (id: string) => api.get<Job>(`/api/jobs/${id}`);
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
export const uploadFile = async (path: string, file: File) => {
    const formData = new FormData();
    formData.append('path', path);
    formData.append('file', file);
    return api.post('/api/files/upload', formData, {
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
    renameTo?: string
): Promise<{ data: ExtractChainResult }> => {
    const formData = new FormData();
    formData.append('input_path', inputPath);
    formData.append('chain_id', chainId);
    if (renameTo) {
        formData.append('rename_to', renameTo);
    }
    return api.post<ExtractChainResult>('/api/files/extract-chain', formData, {
        headers: {
            'Content-Type': 'multipart/form-data',
        },
    });
};

// Start a job
export const submitJob = (jobData: Partial<Job>) => {
    return api.post('/api/jobs', jobData);
};

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
export const resumeJob = (jobId: string, fromStage?: string) => {
    return api.post<{
        message: string;
        original_job_id: string;
        new_job_id: string;
        new_job_name: string;
        resume_from_stage: string;
        preserved_stages: string[];
    }>(`/api/jobs/${jobId}/resume`, null, { params: { from_stage: fromStage } });
};

// Models API
export const fetchModels = (category?: string) =>
    api.get<any[]>('/api/models', { params: { category } });

export const fetchModelById = (id: string) =>
    api.get<any>(`/api/models/${id}`);

// Files API
export const fetchFiles = (path: string = '/') =>
    api.get<any>('/api/files/browse', { params: { path } });

// Templates API
export const fetchTemplates = () =>
    api.get<any[]>('/api/templates');

export const fetchTemplateById = (id: string) =>
    api.get<any>(`/api/templates/${id}`);

// Inputs Presets API
export const fetchInputPresets = (type: string) =>
    api.get<any[]>('/api/inputs/presets', { params: { type } });

export const fetchStandardPaths = () =>
    api.get<any>('/api/inputs/paths');

// Directory presets for batch processing
export const fetchPresetDirectories = () =>
    api.get<any[]>('/api/inputs/preset-directories');

// Designs API
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
    plddt_overall: number | null;
    plddt_binder: number | null;
    pae_overall: number | null;
    pae_interaction: number | null;
    ptm: number | null;
    conf_score: number | null;
    rmsd_binder: number | null;
    ligand_iptm: number | null;
    affinity_score: number | null;
    binder_probability: number | null;
    // Interface metrics (complexes)
    iptm: number | null;
    protein_iptm: number | null;
    complex_iplddt: number | null;
    complex_ipde: number | null;
    chains_ptm: Record<string, number> | null;
    pair_chains_iptm: Record<string, Record<string, number>> | null;
    // Backbone grouping & epitope analysis
    backbone_id: number | null;
    epitope_contact_count: number | null;
    epitope_min_distance: number | null;
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
    is_favorite: boolean;
    notes: string | null;
    created_at: string;
}

export interface DesignListResponse {
    designs: Design[];
    total: number;
}

export interface DesignFilters {
    job_id?: string;
    include_children?: boolean;
    backbone_id?: number;
    plddt_min?: number;
    pae_max?: number;
    iptm_min?: number;
    rog_min?: number;
    rog_max?: number;
    rfd_rog_min?: number;
    rfd_rog_max?: number;
    favorites_only?: boolean;
    sort_by?: 'plddt' | 'iptm' | 'ptm' | 'pae' | 'conf_score' | 'rog' | 'rfd_rog' | 'backbone';
    sort_desc?: boolean;
    limit?: number;
    offset?: number;
}

export interface BackboneSummary {
    job_id: string;
    total: number;
    backbones: Record<number, {
        count: number;
        avg_plddt: number | null;
        avg_iptm: number | null;
        avg_ptm: number | null;
        min_pae: number | null;
    }>;
}

export const fetchDesigns = (filters: DesignFilters = {}) =>
    api.get<DesignListResponse>('/api/designs', { params: filters });

export const fetchBackboneSummary = (jobId: string) =>
    api.get<BackboneSummary>(`/api/designs/by-job/${jobId}/backbone-summary`);

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
    type: 'protein' | 'dna' | 'rna' | 'ligand';
    length: number;
    avg_plddt: number | null;
    plddt: number[];
    residue_numbers: number[];
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
    pipeline_summary: Record<string, any>;
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

export const fetchStructureAnalysis = (designId: string) =>
    api.get<StructureAnalysis>(`/api/designs/${designId}/structure-analysis`);

export const fetchStructureComparison = (id1: string, id2: string) =>
    api.get<StructureComparison>(`/api/designs/${id1}/compare/${id2}`);

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
    params: Record<string, any>;
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
    params: Record<string, any>;
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
    global: { busy_threshold: number; cooldown_ms: number; enabled: boolean };
    overrides: Record<string, { disabled?: boolean; force_available?: boolean }>;
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
    priority: number;
    vram_estimate_mb: number | null;
    sequence_length: number | null;
    batch_id: string | null;
    batch_name: string | null;
    retry_count: number;
    max_retries: number;
    created_at: string;
    started_at: string | null;
    current_stage: string | null;  // Current workflow step (e.g., 'rfantibody', 'fampnn', 'boltz2')
    stage_progress: string | null;  // Granular progress (e.g., '5/30', 'step 500/1000')
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
    api.delete('/api/queue/cancel-all');

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
    notes?: Record<string, any>;
}

export interface SequencePrimer {
    id: string;
    name: string;
    sequence: string;
    start: number;
    end: number;
    tm?: number;
    gc_percent?: number;
}

export interface NucleotideSequence {
    id: string;
    name: string;
    description: string | null;
    sequence: string;
    sequence_type: 'dna' | 'rna';
    is_circular: boolean;
    length: number;
    features: SequenceFeature[] | null;
    primers: SequencePrimer[] | null;
    organism: string | null;
    accession: string | null;
    source_file: string | null;
    gc_content: number | null;
    created_at: string;
    updated_at: string | null;
}

export interface NucleotideSequenceListItem {
    id: string;
    name: string;
    description: string | null;
    sequence_type: string;
    is_circular: boolean;
    length: number;
    gc_content: number | null;
    feature_count: number;
    created_at: string;
}

export interface NucleotideSequenceCreate {
    name: string;
    description?: string;
    sequence: string;
    sequence_type?: 'dna' | 'rna';
    is_circular?: boolean;
    features?: SequenceFeature[];
    primers?: SequencePrimer[];
    organism?: string;
    accession?: string;
    source_file?: string;
}

export const fetchNucleotideSequences = (limit: number = 100, offset: number = 0) =>
    api.get<NucleotideSequenceListItem[]>('/api/sequences/', { params: { limit, offset } });

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

// Antibody API
export interface AntibodyData {
    design_id: string;
    cdrs: {
        H1?: string; H2?: string; H3?: string;
        L1?: string; L2?: string; L3?: string;
    };
    humanness_score?: number;
    stability_data?: Record<string, Record<string, number>>; // chain -> pos -> ddG
    imgt_pdb_url?: string;
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

export const fetchContactMap = (designId: string, maxSize: number = 400) =>
    api.get<ContactMapData>(`/api/designs/${designId}/contact-map`, { params: { max_size: maxSize } });

export const fetchChainPairIptm = (designId: string) =>
    api.get<ChainPairIptmData>(`/api/designs/${designId}/chain-iptm`);

// PAE (Predicted Aligned Error) data
export interface PAEData {
    design_id: string;
    design_name: string;
    pae_matrix: number[][];  // 2D PAE matrix
    size: number;
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

export const removeCachedFramework = (pdbCode: string, scheme?: string) =>
    api.delete(`/api/frameworks/library/${pdbCode}`, { params: { scheme } });

export const getSabdabAttribution = () =>
    api.get<SAbDabAttribution>('/api/frameworks/attribution');

