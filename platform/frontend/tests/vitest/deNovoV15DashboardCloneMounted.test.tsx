import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { writeFileSync } from 'node:fs';

// Actual model-owner discovery, no handwritten scientific defaults. Only HTTP,
// unrelated editors and expensive graphics/provider leaves are inert.
const inventories = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c',
    "import sys,json;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings;from services.complexa import discovery;print(json.dumps({'rfd4_proteina':{m:generation_settings(m) for m in ('generate','evaluate')},'complexa':{m:discovery() for m in ('generate','evaluate')}}))",
], { cwd: resolve(process.cwd(), '../..'), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({ StructurePredictionTemplate: () => null }));
vi.mock('../../src/components/MolecularDynamicsTemplate', () => ({ MolecularDynamicsTemplate: () => null }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/dashboard/SystemResources', () => ({ SystemResources: () => null, GpuSchedulerControls: () => null }));
vi.mock('../../src/components/dashboard/DashboardTelemetry', () => ({ DashboardTelemetry: () => null }));
vi.mock('../../src/components/JobQueuePanel', () => ({ JobQueuePanel: () => null }));
import { Dashboard } from '../../src/components/Dashboard';
import { JobSubmission } from '../../src/components/JobSubmission';
import '../../src/components/ProteinModificationTemplate'; // Resolve the real lazy editor before opening an act scope.
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
let root: Root | undefined, client: QueryClient;
let previous: typeof api.defaults.adapter;
let requests: Array<{ url: string; method: string; body: any }>;
let unexpected: string[];
let saved: any[];
let project: any;
let job: any;
let storedClone: any;
let restoreXhr: ReturnType<typeof vi.spyOn>;
let restoreFetch: ReturnType<typeof vi.spyOn>;
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
beforeEach(() => {
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    storedClone = undefined;
    const setItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function(key, value) { if (key === 'clonedJobData') storedClone = JSON.parse(value); return setItem.call(this, key, value); });
    requests = []; unexpected = []; saved = []; project = null; previous = api.defaults.adapter;
    localStorage.clear(); sessionStorage.clear();
    restoreXhr = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('No network'); });
    restoreFetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw new Error('No network'); });
    api.defaults.adapter = async config => {
        const url = config.url ?? ''; const body = config.data ? JSON.parse(config.data) : undefined;
        requests.push({ url, method: config.method ?? 'get', body });
        let data: unknown;
        const native = url.match(/^\/api\/models\/(rfd4_proteina|complexa)\/generation-settings$/);
        if (url === '/api/jobs' && config.method === 'get') data = { jobs: [{ ...job, params: {} }], total: 1 };
        else if (url === '/api/jobs/clone-source') data = job;
        else if (native) data = inventories[native[1]][config.params.mode];
        else if (url === '/api/projects/project-fixture/workflow-setups/setup-fixture') data = project;
        else if (url === '/api/projects/project-fixture/workflow-setups/setup-fixture/draft') {
            expect(config.headers.get('Idempotency-Key')).toBeTruthy();
            expect(body.expected_generation).toBe(project.generation);
            project = { ...project, generation: project.generation + 1, draft: body.draft }; data = project;
        }
        else if (url === '/api/jobs' && config.method === 'post') data = { id: 'inert-job' };
        else if (url === '/api/user-templates' && config.method === 'post') { data = { ...body, id: 'saved-native', created_at: '2026-01-01', updated_at: '2026-01-01' }; saved = [data]; }
        else if (url === '/api/user-templates') data = saved;
        else if (['/api/models', '/api/templates', '/api/input-presets', '/api/execution-targets'].includes(url)) data = [];
        else if (url === '/api/system/status') data = { gpus: [], gpu_error: null };
        else if (url.startsWith('/api/models/')) data = { id: url.split('/').at(-1), params: [], modes: [{ id: 'design', params: [] }] };
        else { unexpected.push(`${config.method} ${url}`); throw new Error(`Unmocked HTTP ${url}`); }
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
});
afterEach(async () => {
    if (process.env.BMS_UI_EVIDENCE_DIR && requests.some(r => r.url === '/api/jobs')) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, `native-wire-${expect.getState().currentTestName!.replace(/[^A-Za-z0-9]/g, '_')}.json`), JSON.stringify(requests, null, 2));
    await unmount(); api.defaults.adapter = previous; restoreXhr.mockRestore(); restoreFetch.mockRestore(); vi.restoreAllMocks(); vi.unstubAllGlobals(); expect(unexpected).toEqual([]);
});
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
async function mountDashboard() {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/']}><Routes><Route path="/" element={<Dashboard />} /><Route path="/submit" element={<JobSubmission />} /></Routes></MemoryRouter></QueryClientProvider>));
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('button[title="Clone job parameters"]')).not.toBeNull(); });
    await act(async () => (document.querySelector('button[title="Clone job parameters"]') as HTMLButtonElement).click());
    await vi.waitFor(async () => { await settle(); expect(document.querySelector(`[data-native-de-novo="${job.model_id}"] fieldset`)).not.toBeNull(); }, { timeout: 5000 });
}
async function click(text: string) {
    const node = [...document.querySelectorAll<HTMLButtonElement>('button')].find(e => !e.closest('[hidden]') && e.textContent?.trim() === text);
    expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle();
}
async function edit(label: string, value: string) {
    const node = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`);
    expect(node, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node!.dispatchEvent(new Event(node!.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
const nativeRequest = { input: { kind: 'unconditional', name: 'n', chains: [{ chain_id: 'A', min_length: 73, max_length: 99 }] }, settings: { seed: 0, delete_failing_pre_oracle: false, search: { checkpoints: null, save_states: false } } };
async function disclose(name: string) {
    await click('Native settings');
    const summary = [...document.querySelectorAll('summary')].find(node => node.textContent?.trim().toLowerCase() === name.toLowerCase());
    expect(summary, name).toBeTruthy();
    await act(async () => { const details = summary!.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}

for (const model of ['rfd4_proteina', 'complexa']) for (const sequenceState of ['absent', 'null']) {
    it(`Dashboard hydrates and clones ${model} with ${sequenceState} sequence design through real Router and Axios`, async () => {
        const request = model === 'complexa' ? { task: 'protein_binder', task_name: 'clone', seed: 0, reward: null, source: { document_id: 'retained-document', binder_center: [1, 0, -2] } } : nativeRequest;
        const key = model === 'complexa' ? 'complexa_request' : 'rfd4_request';
        const sources = { 'retained-document': { path: 'inputs/retained.pdb', output_format: 'native' } };
        job = { id: 'clone-source', name: 'Native retained fixture', model_id: model, mode: 'generate', status: 'completed', created_at: '2026-01-01T00:00:00Z', params: { [key]: request, native_sources: sources }, source_structure: sources['retained-document'], execution_target_id: null, execution_policy: { remote_result_policy: 'automatic' }, pinned_gpu: 0, ...(sequenceState === 'null' ? { sequence_design: null } : {}) };
        sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'unrelated-remote');
        await mountDashboard();
        expect(requests.filter(r => r.url === '/api/jobs/clone-source')).toHaveLength(1);
        expect(storedClone.params).toEqual(job.params);
        expect(storedClone.source_structure).toEqual(job.source_structure);
        expect(Object.hasOwn(storedClone, 'sequence_design')).toBe(sequenceState === 'null');
        expect(localStorage.getItem('clonedJobData')).toBeNull();
        expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
        await click('Generate candidates');
        const wire = requests.find(r => r.url === '/api/jobs' && r.method === 'post')?.body;
        expect(wire).toBeDefined();
        expect.soft(wire.params).toEqual(job.params);
        expect.soft(wire).not.toHaveProperty('native_sources');
        expect.soft(wire.execution_target_id).toBeNull();
        expect.soft(wire.pinned_gpu).toBe(0);
        expect.soft(wire.execution_policy).toEqual(job.execution_policy);
        expect.soft(wire.source_structure).toEqual(job.source_structure);
        expect.soft(Object.hasOwn(wire, 'sequence_design')).toBe(sequenceState === 'null');
        if (sequenceState === 'null') expect.soft(wire.sequence_design).toBeNull();
    });
}
