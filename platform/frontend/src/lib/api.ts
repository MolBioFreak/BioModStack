import axios from 'axios';

const API_BASE = 'http://localhost:8000';

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
export const fetchJobs = () => api.get<{ jobs: Job[]; total: number }>('/api/jobs');
export const fetchSystemStatus = () => api.get<SystemStatus>('/api/gpu/status');
export const fetchJobById = (id: string) => api.get<Job>(`/api/jobs/${id}`);
export const cancelJob = (id: string) => api.delete(`/api/jobs/${id}`);

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

// Start a job
export const submitJob = (jobData: Partial<Job>) => {
    return api.post('/api/jobs', jobData);
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
    plddt_min?: number;
    pae_max?: number;
    favorites_only?: boolean;
    limit?: number;
    offset?: number;
}

export const fetchDesigns = (filters: DesignFilters = {}) =>
    api.get<DesignListResponse>('/api/designs', { params: filters });

export const toggleDesignFavorite = (designId: string, isFavorite: boolean) =>
    api.post(`/api/designs/${designId}/favorite`, { is_favorite: isFavorite });

export const downloadDesignPdb = (designId: string) =>
    `http://localhost:8000/api/designs/${designId}/pdb`;

// Per-residue metrics for charts
export interface ResidueMetrics {
    design_id: string;
    design_name: string;
    residue_numbers: number[];
    plddt: number[];
    length: number;
}

export const fetchDesignResidueMetrics = (designId: string) =>
    api.get<ResidueMetrics>(`/api/designs/${designId}/residue-metrics`);

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
