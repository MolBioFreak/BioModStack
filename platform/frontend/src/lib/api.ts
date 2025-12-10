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
    power_draw_w: number;
    power_limit_w: number;
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
}

export interface RAMStatus {
    total_gb: number;
    used_gb: number;
    available_gb: number;
    utilization: number;
}

export interface SystemStatus {
    gpus: GPUStatus[];
    cpu: CPUStatus;
    ram: RAMStatus;
    timestamp: string;
}

export interface PowerProfile {
    eco_mode: boolean;
    message: string;
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

// Power profile (eco mode)
export const fetchPowerProfile = () =>
    api.get<PowerProfile>('/api/gpu/power-profile');

export const setPowerProfile = (enableEco: boolean) =>
    api.post<PowerProfile>('/api/gpu/power-profile', null, {
        params: { enable_eco: enableEco }
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
