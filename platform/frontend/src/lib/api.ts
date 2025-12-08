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
    mode: string;
    created_at: string;
    design_count: number;
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

// API functions
export const fetchJobs = () => api.get<{ jobs: Job[]; total: number }>('/api/jobs');
export const fetchSystemStatus = () => api.get<SystemStatus>('/api/gpu/status');
export const fetchJobById = (id: string) => api.get<Job>(`/api/jobs/${id}`);
export const cancelJob = (id: string) => api.post(`/api/jobs/${id}/cancel`);

