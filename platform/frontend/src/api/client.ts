/**
 * Analysis Module API Client
 * Typed client for FastAPI backend and VLM server endpoints
 */

import { API_URL, VLM_URL } from './config';

const ASSAY_UPLOAD_TIMEOUT_MS = 300_000;

async function throwAssayHttpError(response: Response, context: string): Promise<never> {
    let detail = '';
    try {
        const contentType = response.headers.get('content-type') ?? '';
        if (contentType.includes('application/json')) {
            const payload = await response.json();
            const rawDetail = payload?.detail ?? payload?.message ?? payload;
            detail = typeof rawDetail === 'string' ? rawDetail : JSON.stringify(rawDetail);
        } else {
            detail = await response.text();
        }
    } catch {
        detail = '';
    }
    const suffix = detail ? `: ${detail}` : '';
    throw new Error(`${context} failed (HTTP ${response.status})${suffix}`);
}

// ============================================================================
// Health & Status
// ============================================================================

export async function checkBackendHealth() {
    const response = await fetch(`${API_URL}/health`, { signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function getModelsStatus() {
    const response = await fetch(`${VLM_URL}/models`, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// Model Management
// ============================================================================

export async function loadModel(modelKey: string, device = 'cuda:0') {
    const response = await fetch(`${VLM_URL}/models/${modelKey}/load?device=${device}`, {
        method: 'POST',
        signal: AbortSignal.timeout(120000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function unloadModel(modelKey: string) {
    const response = await fetch(`${VLM_URL}/models/${modelKey}/unload`, {
        method: 'POST',
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function unloadAllModels() {
    const response = await fetch(`${VLM_URL}/models/unload-all`, {
        method: 'POST',
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// Smart OCR
// ============================================================================

export async function smartOcrParse(imageBase64: string, confidenceThreshold = 0.85, maxTokens = 2048) {
    const response = await fetch(`${VLM_URL}/ocr/smart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            image: imageBase64,
            confidence_threshold: confidenceThreshold,
            auto_route: true,
            max_tokens: maxTokens,
        }),
        signal: AbortSignal.timeout(300000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// Ensemble OCR
// ============================================================================

export async function ensembleParallel(
    models: string[],
    imageBase64: string,
    prompt: string,
    confidenceThreshold = 0.85
) {
    const response = await fetch(`${VLM_URL}/ensemble/parallel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            models,
            image: imageBase64,
            prompt: prompt || 'Extract all text from this document.',
            confidence_threshold: confidenceThreshold,
        }),
        signal: AbortSignal.timeout(300000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function ensembleCascade(
    models: string[],
    imageBase64: string,
    prompt: string,
    confidenceThreshold = 0.85
) {
    const response = await fetch(`${VLM_URL}/ensemble/cascade`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            models,
            image: imageBase64,
            prompt: prompt || 'Extract all text from this document.',
            confidence_threshold: confidenceThreshold,
        }),
        signal: AbortSignal.timeout(300000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// Region-Based Dispatch
// ============================================================================

export async function regionDispatch(imageBase64: string, mergeDistance = 0.02) {
    const response = await fetch(`${VLM_URL}/ocr/region-dispatch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            image: imageBase64,
            merge_distance: mergeDistance,
        }),
        signal: AbortSignal.timeout(300000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// VLM Chat
// ============================================================================

export interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
}

export async function chatWithVlm(
    model: string,
    message: string,
    imageBase64?: string
) {
    const content: Array<{ type: string; text?: string; image_url?: { url: string } }> = [];

    if (imageBase64) {
        content.push({
            type: 'image_url',
            image_url: { url: `data:image/png;base64,${imageBase64}` }
        });
    }

    content.push({ type: 'text', text: message });

    const response = await fetch(`${VLM_URL}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            model,
            messages: [{ role: 'user', content }]
        }),
        signal: AbortSignal.timeout(120000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// Document Library
// ============================================================================

export interface DocumentInfo {
    id: string;
    filename: string;
    original_filename?: string;
    file_size?: number;
    upload_date?: string;
    status?: string;
    doc_type?: string;
}

export async function listDocuments(): Promise<DocumentInfo[]> {
    const response = await fetch(`${API_URL}/documents/`, {
        signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function uploadDocument(file: File): Promise<DocumentInfo> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_URL}/documents/upload`, {
        method: 'POST',
        body: formData,
        signal: AbortSignal.timeout(60000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function parseDocument(
    documentId: string,
    model: string,
    docType = 'general',
    maxTokens?: number
) {
    const params = new URLSearchParams({ model, doc_type: docType });
    if (maxTokens) params.append('max_tokens', maxTokens.toString());

    const response = await fetch(`${API_URL}/documents/${documentId}/parse?${params}`, {
        method: 'POST',
        signal: AbortSignal.timeout(600000), // 10 minutes for large docs
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function getDocument(documentId: string): Promise<DocumentInfo> {
    const response = await fetch(`${API_URL}/documents/${documentId}`, {
        signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// Statistical Analysis
// ============================================================================

export async function runControlChart(data: number[], subgroupSize: number) {
    const response = await fetch(`${API_URL}/analysis/control-chart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data, subgroup_size: subgroupSize }),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function runCapability(
    data: number[],
    usl: number,
    lsl: number,
    target: number | null,
    subgroupSize: number
) {
    const response = await fetch(`${API_URL}/analysis/capability`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data, usl, lsl, target, subgroup_size: subgroupSize }),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function generateDoeDesign(designType: string, nFactors: number, centerPoints: number) {
    const response = await fetch(`${API_URL}/analysis/doe/design`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design_type: designType, n_factors: nFactors, center_points: centerPoints }),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function analyzeRsm(designMatrix: Record<string, number>[], response_values: number[]) {
    const response = await fetch(`${API_URL}/analysis/doe/rsm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design_matrix: designMatrix, response: response_values }),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function runHypothesisTest(
    testType: string,
    group1: number[],
    group2: number[] | number,
    alpha: number
) {
    let endpoint: string;
    let body: Record<string, unknown>;

    switch (testType) {
        case 'one_sample':
            endpoint = `${API_URL}/analysis/hypothesis/t-test/one-sample`;
            body = { data: group1, pop_mean: group2 as number, alpha };
            break;
        case 'two_sample':
            endpoint = `${API_URL}/analysis/hypothesis/t-test/two-sample`;
            body = { group1, group2: group2 as number[], alpha };
            break;
        case 'paired':
            endpoint = `${API_URL}/analysis/hypothesis/t-test/paired`;
            body = { before: group1, after: group2 as number[], alpha };
            break;
        case 'anova':
            endpoint = `${API_URL}/analysis/hypothesis/anova`;
            body = { groups: [group1, group2 as number[]], alpha };
            break;
        default:
            throw new Error(`Unsupported test type: ${testType}`);
    }

    const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function runRegression(x: number[], y: number[]) {
    const response = await fetch(`${API_URL}/analysis/regression/simple`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x, y, x_name: 'X', y_name: 'Y' }),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// qPCR Analysis
// ============================================================================

export async function uploadQpcrFile(file: File, options: { persist?: boolean } = {}) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('persist', String(options.persist ?? true));

    const ext = file.name.toLowerCase().split('.').pop();
    let endpoint: string;
    if (ext === 'eds') {
        endpoint = `${API_URL}/analysis/qpcr/upload-eds`;
    } else if (ext === 'xlsx' || ext === 'xls') {
        endpoint = `${API_URL}/analysis/qpcr/upload-excel`;
    } else if (ext === 'csv') {
        endpoint = `${API_URL}/analysis/qpcr/upload-csv`;
    } else {
        throw new Error(`Unsupported file type: .${ext}`);
    }

    const response = await fetch(endpoint, {
        method: 'POST',
        body: formData,
        signal: AbortSignal.timeout(ASSAY_UPLOAD_TIMEOUT_MS),
    });
    if (!response.ok) await throwAssayHttpError(response, 'qPCR instrument upload');
    return response.json();
}

export async function listQpcrImports(limit = 25) {
    const response = await fetch(`${API_URL}/analysis/qpcr/imports?limit=${encodeURIComponent(String(limit))}`, {
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) await throwAssayHttpError(response, 'qPCR analytical import list');
    return response.json();
}

export async function loadQpcrImport(analyticalImportId: string) {
    const response = await fetch(`${API_URL}/analysis/qpcr/imports/${encodeURIComponent(analyticalImportId)}`, {
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) await throwAssayHttpError(response, 'qPCR analytical import load');
    return response.json();
}

export async function runDeltaCq(
    data: { sample: string; gene: string; cq: number; group: string }[],
    referenceGenes: string[],
    targetGenes: string[]
) {
    const response = await fetch(`${API_URL}/analysis/qpcr/delta-cq`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data, reference_genes: referenceGenes, target_genes: targetGenes }),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function runDeltaDeltaCq(
    data: { sample: string; gene: string; cq: number; group: string }[],
    referenceGenes: string[],
    targetGenes: string[],
    controlGroup: string
) {
    const response = await fetch(`${API_URL}/analysis/qpcr/delta-delta-cq`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            data,
            reference_genes: referenceGenes,
            target_genes: targetGenes,
            control_group: controlGroup,
        }),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// HPLC Analysis
// ============================================================================

export async function runHplcAnalysis(
    time: number[],
    signal: number[],
    baselineMethod: string,
    peakProminence: number,
    fitModel: string
) {
    const response = await fetch(`${API_URL}/analysis/hplc/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            time,
            signal,
            baseline_method: baselineMethod,
            peak_prominence: peakProminence,
            fit_model: fitModel,
        }),
        signal: AbortSignal.timeout(60000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function runHplcCalibration(
    points: { concentration: number; area: number }[],
    analyteName: string,
    unit: string,
    forceThroughOrigin: boolean
) {
    const response = await fetch(`${API_URL}/analysis/hplc/calibration-curve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            points,
            analyte_name: analyteName,
            unit,
            force_through_origin: forceThroughOrigin,
        }),
        signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// Datasets
// ============================================================================

export async function getDatasets(category?: string) {
    const url = category ? `${API_URL}/datasets?category=${category}` : `${API_URL}/datasets`;
    const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function getDataset(id: number) {
    const response = await fetch(`${API_URL}/datasets/${id}`, { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

// ============================================================================
// Empower HPLC Imports
// ============================================================================

type EmpowerImportOptions = {
    persist?: boolean;
    baselineMethod?: string;
    peakProminence?: number;
};

export async function importEmpowerFiles(files: File[], options: EmpowerImportOptions = {}) {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    if (options.persist !== undefined) formData.append('persist', String(options.persist));
    if (options.baselineMethod) formData.append('baseline_method', options.baselineMethod);
    if (options.peakProminence !== undefined) formData.append('peak_prominence', String(options.peakProminence));

    const response = await fetch(`${API_URL}/analysis/hplc/empower/import`, {
        method: 'POST',
        body: formData,
        signal: AbortSignal.timeout(ASSAY_UPLOAD_TIMEOUT_MS),
    });
    if (!response.ok) await throwAssayHttpError(response, 'Empower chromatography import');
    return response.json();
}

export async function listEmpowerSst(importId: number) {
    const params = new URLSearchParams({ import_id: String(importId) });
    const response = await fetch(`${API_URL}/analysis/hplc/empower/sst?${params.toString()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function updateEmpowerInjection(injectionId: number, payload: Record<string, unknown>) {
    const response = await fetch(`${API_URL}/analysis/hplc/empower/injections/${injectionId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

export async function exportEmpowerSstMaster(importId: number) {
    const params = new URLSearchParams({ import_id: String(importId) });
    const response = await fetch(`${API_URL}/analysis/hplc/empower/exports/sst-master?${params.toString()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.blob();
}

export async function exportEmpowerPlasmidTracking(importId: number) {
    const params = new URLSearchParams({ import_id: String(importId) });
    const response = await fetch(`${API_URL}/analysis/hplc/empower/exports/plasmid-tracking?${params.toString()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.blob();
}
