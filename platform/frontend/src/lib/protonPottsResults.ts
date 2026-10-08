import { api, type Job } from './api';
import { submitPreparedSelectedMutation } from './binderContinuation';
import type { ProtonPottsParams } from './protonPottsSettings';
export interface PHDesignOutput {
    binder_chain: string;
    canonical_sequence: string;
    extended_tokens: string[];
    extended_vocab: boolean;
    scheme: string;
    sample: number;
    final_potts_energy: number;
    seed_idx: number | null;
    protonation_type: string | null;
    site_rank: number | null;
    center_res_id: number | null;
    n_neighbours: number | null;
    selective_energy: number | null;
    n_centers: number | null;
    center_res_ids: number[] | null;
    center_protonation_types: string[] | null;
    selective_energies: number[] | null;
    global_protonation_dH: number | null;
    placement_prob: number | null;
    placement_entropy: number | null;
    sequence_decoded_prob_score: number | null;
    sequence_entropy: number | null;
    method: string | null;
    backend: string | null;
    selective_source: string | null;
    placement_label: string | null;
    placement_region: string | null;
    placement_by: string | null;
    combined_lambda: number | null;
    repetitive_window_weight: number | null;
    block_size: number | null;
    sweep_order: string | null;
    neighbour_k: number | null;
    max_mutations: number | null;
    energy_trajectory: PHDesignTrajectory[] | null;
}
export interface PHDesignTrajectory { step: number; selective_energy: number | null; global_protonation_dH: number | null; potts_energy: number; canonical_sequence: string; extended_tokens: string | string[] }
export interface ProtonPottsResults { contract: 'protonpottsmpnn_design.v1'; source: Record<string, unknown>; request: { contract: 'protonpottsmpnn_design.v1'; options: Omit<ProtonPottsParams, 'target_pdb'>; source: { requested_path: string; sha256: string } }; designs: { design_id: string; criteria_index: number; native_design_id: string; native: PHDesignOutput }[]; runtime: Record<string, unknown>; artifacts: string[] }
export function parseProtonPottsResults(value: unknown): ProtonPottsResults {
    const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
    if (!object(value) || value.contract !== 'protonpottsmpnn_design.v1' || !object(value.request) || !object(value.source) || !object(value.runtime) || !Array.isArray(value.artifacts) || !value.artifacts.every(v => typeof v === 'string') || !Array.isArray(value.designs)) throw new Error('Invalid ProtonPottsMPNN result contract');
    const request = value.request;
    if (request.contract !== 'protonpottsmpnn_design.v1' || !object(request.options) || !object(request.source) || typeof request.source.requested_path !== 'string' || typeof request.source.sha256 !== 'string' || !Array.isArray(request.options.criteria) || typeof request.options.binder_chain !== 'string' || typeof request.options.seed !== 'number' || !object(request.options.engine_options)) throw new Error('Invalid ProtonPottsMPNN native request');
    const ids = new Set<string>();
    for (const row of value.designs) {
        if (!object(row) || typeof row.design_id !== 'string' || !row.design_id || ids.has(row.design_id) || !Number.isInteger(row.criteria_index) || typeof row.native_design_id !== 'string' || !object(row.native)) throw new Error('Invalid ProtonPottsMPNN design identity');
        ids.add(row.design_id);
        const native = row.native;
        if (typeof native.canonical_sequence !== 'string' || !Array.isArray(native.extended_tokens) || !native.extended_tokens.every(v => typeof v === 'string') || typeof native.final_potts_energy !== 'number' || typeof native.binder_chain !== 'string' || typeof native.scheme !== 'string' || typeof native.extended_vocab !== 'boolean' || !Number.isInteger(native.sample)) throw new Error('Invalid native PHDesignOutput');
        const nullableNumbers = ['seed_idx', 'site_rank', 'center_res_id', 'n_neighbours', 'selective_energy', 'n_centers', 'global_protonation_dH', 'placement_prob', 'placement_entropy', 'sequence_decoded_prob_score', 'sequence_entropy', 'combined_lambda', 'repetitive_window_weight', 'block_size', 'neighbour_k', 'max_mutations'];
        const nullableStrings = ['protonation_type', 'method', 'backend', 'selective_source', 'placement_label', 'placement_region', 'placement_by', 'sweep_order'];
        if (nullableNumbers.some(key => native[key] !== null && typeof native[key] !== 'number') || nullableStrings.some(key => native[key] !== null && typeof native[key] !== 'string')) throw new Error('Invalid native PHDesignOutput measurements');
        for (const [key, type] of [['center_res_ids', 'number'], ['center_protonation_types', 'string'], ['selective_energies', 'number']] as const) {
            const values = native[key];
            if (values !== null && (!Array.isArray(values) || !values.every(item => typeof item === type))) throw new Error(`Invalid native PHDesignOutput ${key}`);
        }
        const trajectory = native.energy_trajectory;
        if (trajectory !== null && (!Array.isArray(trajectory) || trajectory.some(point => !object(point) || typeof point.step !== 'number' || typeof point.potts_energy !== 'number' || typeof point.canonical_sequence !== 'string' || !(typeof point.extended_tokens === 'string' || Array.isArray(point.extended_tokens) && point.extended_tokens.every(token => typeof token === 'string'))))) throw new Error('Invalid native PHDesignOutput trajectory');
    }
    return value as unknown as ProtonPottsResults;
}
export async function fetchProtonPottsResults(jobId: string, signal?: AbortSignal): Promise<ProtonPottsResults> {
    return parseProtonPottsResults((await api.get<unknown>(`/api/jobs/${encodeURIComponent(jobId)}/protonpottsmpnn/results`, { signal })).data);
}
export interface ProtonPottsPredictionRequest {
    design_ids: string[]; model_id: 'protenix' | 'boltz2'; params: Record<string, unknown>;
    execution_target_id: string | null; launch_context_id?: string; idempotency_key?: string;
}
export interface ProtonPottsPredictionResponse {
    source_job_id: string; root_job_id: string; operation: string;
    design_ids: string[]; selected_design_count: number; launched_jobs: Job[];
}
export async function predictProtonPottsSelected(jobId: string, request: ProtonPottsPredictionRequest): Promise<ProtonPottsPredictionResponse> {
    return submitPreparedSelectedMutation(() => api.post<ProtonPottsPredictionResponse>(`/api/jobs/${encodeURIComponent(jobId)}/protonpottsmpnn/predict`, request).then(response => response.data));
}
