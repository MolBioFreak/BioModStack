import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
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
import { JobSubmission } from '../../src/components/JobSubmission';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate'; // Resolve the real lazy editor before opening an act scope.
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
let root: Root | undefined, client: QueryClient;
let previous: typeof api.defaults.adapter;
let requests: Array<{ url: string; method: string; body: any; params?: any }>;
let unexpected: string[];
let saved: any[];
let project: any;
let restoreXhr: ReturnType<typeof vi.spyOn>;
let restoreFetch: ReturnType<typeof vi.spyOn>;
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
beforeEach(() => {
    requests = []; unexpected = []; saved = []; project = null; previous = api.defaults.adapter;
    localStorage.clear(); sessionStorage.clear();
    restoreXhr = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('No network'); });
    restoreFetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw new Error('No network'); });
    api.defaults.adapter = async config => {
        const url = config.url ?? ''; const body = config.data ? JSON.parse(config.data) : undefined;
        requests.push({ url, method: config.method ?? 'get', body, params: config.params });
        let data: unknown;
        const native = url.match(/^\/api\/models\/(rfd4_proteina|complexa)\/generation-settings$/);
        if (native) data = inventories[native[1]][config.params.mode];
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
    await unmount(); api.defaults.adapter = previous; restoreXhr.mockRestore(); restoreFetch.mockRestore(); expect(unexpected).toEqual([]);
});
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
async function mount(model: string, mode = 'generate', request?: object, projectRoute = '') {
    if (request) localStorage.setItem('clonedJobData', JSON.stringify({ name: 'Exact native run', model_id: model, mode, execution_target_id: null, sequence_design: null, params: { [`${model === 'complexa' ? 'complexa' : 'rfd4'}_request`]: request } }));
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/submit?model=${model}&mode=${mode}${projectRoute}`]}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await vi.waitFor(async () => { await settle(); expect(document.querySelector(`[data-native-de-novo="${model}"] fieldset`), document.body.textContent ?? '').not.toBeNull(); }, { timeout: 3500 });
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

// Identical mounted harness is run on immutable baseline snapshots. Counts are
// DOM inventory, not browser geometry or paint timing. No assumed reductions.
it.each(['rfd4_proteina', 'complexa'])('measures matched cold %s controls, queries and saved envelope', async model => {
    await mount(model);
    const cold = { controls: document.querySelectorAll('input,select,textarea,button').length, nativeControls: document.querySelectorAll('[data-native-de-novo] input,[data-native-de-novo] select,[data-native-de-novo] textarea,[data-native-de-novo] button').length, domElements: document.querySelectorAll('*').length, requests: structuredClone(requests) };
    await click('Template Manager');
    const name = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Matched cold fixture'); name.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template');
    await vi.waitFor(() => expect(saved).toHaveLength(1));
    const bytes = (value: unknown) => Buffer.byteLength(JSON.stringify(value), 'utf8');
    const report = { fixture: 'empty native generate route; default discovery; Template Manager save without scientific edits', model, revision: process.env.BMS_MEASUREMENT_REVISION, cold, envelopeBytes: bytes(saved[0].params), editorBytes: bytes(saved[0].params.editor_state), nativeRequestBytes: bytes(saved[0].params.native_job_request), envelope: saved[0].params };
    expect(cold.controls).toBeGreaterThan(0);
    expect(cold.requests.filter(r => /generation-settings$/.test(r.url))).toHaveLength(1);
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, `measurement-${process.env.BMS_MEASUREMENT_REVISION}-${model}.json`), JSON.stringify(report, null, 2));
});

it('measures legacy engine-switch draft duplication with the actual editor', async () => {
    let draft: any;
    const onDraftChange = (value: any) => { draft = value; };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter><ProteinModificationTemplate onBack={() => {}} onDraftChange={onDraftChange} /></MemoryRouter></QueryClientProvider>));
    await settle();
    const cold = { controls: document.querySelectorAll('input,select,textarea,button').length, domElements: document.querySelectorAll('*').length, requests: structuredClone(requests) };
    async function selectEngine(value: string) {
        const label = [...document.querySelectorAll('label')].find(node => node.textContent?.startsWith('Engine'));
        const control = label?.querySelector('select') ?? document.querySelector('[aria-label="Engine"]');
        expect(control).toBeTruthy();
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(control, value); control!.dispatchEvent(new Event('change', { bubbles: true })); });
        await settle();
    }
    await selectEngine('disco'); await selectEngine('rfd3');
    expect(draft).toBeDefined();
    const report = { fixture: 'default RFD3 generate; switch Engine disco then rfd3; no scientific edits', revision: process.env.BMS_MEASUREMENT_REVISION, cold, editorBytes: Buffer.byteLength(JSON.stringify(draft)), envelope: draft };
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, `measurement-${process.env.BMS_MEASUREMENT_REVISION}-legacy.json`), JSON.stringify(report, null, 2));
});
