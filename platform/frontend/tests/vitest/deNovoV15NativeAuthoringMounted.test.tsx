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
import '../../src/components/ProteinModificationTemplate'; // Resolve the real lazy editor before opening an act scope.
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
let root: Root | undefined, client: QueryClient;
let previous: typeof api.defaults.adapter;
let requests: Array<{ url: string; method: string; body: any }>;
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
        requests.push({ url, method: config.method ?? 'get', body });
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
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, `native-wire-${expect.getState().currentTestName!.replace(/[^A-Za-z0-9]/g, '_')}.json`), JSON.stringify(requests, null, 2));
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
it.each([
    ['rfd4_proteina', nativeRequest, 'rfd4_request'],
    ['complexa', { task: 'protein_binder', operation: 'generate', seed: 0, reward: null }, 'complexa_request'],
])('switches Simple and Advanced without changing the %s native Job request', async (model, request, field) => {
    await mount(model, 'generate', request);
    await click('Advanced'); await click('Simple'); await click('Advanced');
    expect(requests.filter(request => request.method !== 'get')).toEqual([]);
    await click('Generate candidates');
    const jobs = requests.filter(request => request.url === '/api/jobs');
    expect(jobs).toHaveLength(1);
    expect(jobs[0].body.params[field]).toEqual(request);
    expect(jobs[0].body.params).not.toHaveProperty('suite_view');
});
async function disclose(name: string) {
    await click('Native settings');
    const summary = [...document.querySelectorAll('summary')].find(node => node.textContent?.trim().toLowerCase() === name.toLowerCase());
    expect(summary, name).toBeTruthy();
    await act(async () => { const details = summary!.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}
it('native tuple controls retain order and typed numbers through the actual parent request', async () => {
    const request = { task: 'protein_binder', task_name: 'tuple-fixture', source: { document_id: 'exact-document', binder_center: [1, 0, -2] } };
    await mount('complexa', 'generate', request);
    const summary = [...document.querySelectorAll('summary')].find(node => node.textContent === 'Advanced source settings')!;
    await act(async () => { const details = summary.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
    const control = document.querySelector<HTMLInputElement>('[aria-label="source.source.binder_center[1]"]');
    expect(control?.type).toBe('number');
    await edit('source.source.binder_center[1]', '2.5');
    await click('Generate candidates');
    const wire = requests.find(r => r.url === '/api/jobs')!.body.params.complexa_request;
    expect(wire).toEqual({ ...request, source: { ...request.source, binder_center: [1, 2.5, -2] } });
});
it('native metric name/cutoff tuples use their per-position schemas instead of an untyped fallback', async () => {
    const choice = inventories.rfd4_proteina.generate.parameter_schema.properties.metric_extensions.items.oneOf.find((candidate: any) => candidate.properties.kwargs.properties.term_cutoffs);
    const metric = { name: 'explicit-cutoff', section: 'inline_metrics', implementation: choice.properties.implementation.const, kwargs: { term_cutoffs: { contact: ['ljatr', 0] } } };
    await mount('rfd4_proteina', 'generate', { ...nativeRequest, metric_extensions: [metric] });
    await disclose('Metric providers, constructors and native oracles');
    const label = 'metric_extensions[0].kwargs.term_cutoffs.contact';
    expect(document.querySelector<HTMLInputElement>(`[aria-label="${label}[0]"]`)?.type).toBe('text');
    expect(document.querySelector<HTMLInputElement>(`[aria-label="${label}[1]"]`)?.type).toBe('number');
    await edit(`${label}[1]`, '-0.25');
    await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs')!.body.params.rfd4_request.metric_extensions[0].kwargs.term_cutoffs.contact).toEqual(['ljatr', -0.25]);
});
it('Complexa task drafts survive template storage and cold load without entering the scientific request', async () => {
    const request = { task: 'protein_binder', task_name: '', seed: 0, reward: null, source: { document_id: 'selected-native-model', ligand_only: false } };
    await mount('complexa', 'generate', request);
    await edit('Complexa task', 'ligand_binder');
    await click('Template Manager');
    const name = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Independent native tasks'); name.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template');
    await vi.waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].params.native_job_request.params.complexa_request).toEqual({ task: 'ligand_binder' });
    expect(Object.values(saved[0].params.editor_state.native_task_drafts)).toContainEqual(request);
    await unmount(); await mount('complexa');
    await click('Template Manager'); await click('← Back to list'); await click('Load');
    await edit('Complexa task', 'protein_binder');
    await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs')!.body.params).toEqual({ complexa_request: request });
});
it('real JobSubmission clone submits the exact RFD4 request through Axios, with explicit Local and no inactive drafts', async () => {
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'unrelated-remote');
    await mount('rfd4_proteina', 'generate', nativeRequest);
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
    expect(requests.filter(r => /generation-settings$/.test(r.url)).map(r => r.url)).toEqual(['/api/models/rfd4_proteina/generation-settings']);
    await click('Generate candidates');
    const submitted = requests.find(r => r.url === '/api/jobs' && r.method === 'post')!.body;
    expect(submitted).toMatchObject({ model_id: 'rfd4_proteina', mode: 'generate', execution_target_id: null, params: { rfd4_request: nativeRequest } });
    expect(Object.keys(submitted.params)).toEqual(['rfd4_request']);
    expect(submitted.params.rfd4_request.settings.search).toEqual({ checkpoints: null, save_states: false });
});
it('native mode drafts survive Generate → Evaluate → Generate and typed explicit clears', async () => {
    await mount('rfd4_proteina', 'generate', nativeRequest);
    await edit('Input name', '');
    await edit('Native mode', 'evaluate'); await settle();
    expect(document.querySelector('[aria-label="Input name"]')).toBeNull();
    await edit('Native mode', 'generate'); await settle();
    expect((document.querySelector('[aria-label="Input name"]') as HTMLInputElement).value).toBe('');
    await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs')!.body.params.rfd4_request.input.name).toBe('');
});

it('RFD4 Evaluate clone emits its native mode and exact annotated slot request without generation defaults', async () => {
    const request = { input: { kind: 'annotated', slots: ['alternate-model-document'] }, settings: { delete_failing_pre_oracle: false }, evaluation_tasks: [{ slot: 'alternate-model-document', task: 'selected' }] };
    await mount('rfd4_proteina', 'evaluate', request);
    await click('Evaluate candidates');
    const wire = requests.find(r => r.url === '/api/jobs')!.body;
    expect(wire).toMatchObject({ model_id: 'rfd4_proteina', mode: 'evaluate', params: { rfd4_request: request } });
    const normalized = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c', "import sys,json;from scripts.lib.rfd4_proteina_contract import normalize_request;print(json.dumps(normalize_request(json.loads(sys.argv[1]),'evaluate')))", JSON.stringify(wire.params.rfd4_request)], { cwd: resolve(process.cwd(), '../..'), encoding: 'utf8' }));
    expect(normalized).toEqual({ ...request, version: 'rfd4_request_v1', mode: 'evaluate' });
});
it.each(['rfd4_proteina', 'complexa'])('fresh %s route uses its real typed inventory and no other native inventory', async model => {
    await mount(model);
    expect(document.querySelector('[aria-label="RFD4 task"]') ?? document.querySelector('[aria-label="Complexa task"]')).not.toBeNull();
    expect(requests.filter(r => /generation-settings$/.test(r.url))).toHaveLength(1);
    expect(document.querySelector('textarea')).toBeNull();
});

it('Template Manager saves, reads back and cold loads exact native intent despite ambient remote placement', async () => {
    await mount('rfd4_proteina', 'generate', nativeRequest);
    await click('Template Manager');
    const name = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Native saved fixture'); name.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template');
    await vi.waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].params).toMatchObject({ editor_state: { execution_target_id: null, rfd4_request: nativeRequest }, native_job_request: { execution_target_id: null, params: { rfd4_request: nativeRequest } } });
    expect(saved[0].params.native_job_request.params.de_novo_drafts).toBeUndefined();
    await unmount();
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'unrelated-remote');
    await mount('rfd4_proteina');
    await click('Template Manager'); await click('← Back to list'); await click('Load');
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
    expect((document.querySelector('[aria-label="Input name"]') as HTMLInputElement).value).toBe('n');
    await click('Generate candidates');
    expect(requests.filter(r => r.url === '/api/jobs').at(-1)!.body.params).toEqual({ rfd4_request: nativeRequest });
});

it.each(['generate', 'evaluate'])('Complexa %s preserves typed recursive native values through real parent Axios transport', async mode => {
    const request = { task: 'protein_binder', task_name: 'test-native', operation: mode, seed: 0, reward: null, source: { document_id: 'retained-exact-doc', ligand_only: false, motif_atoms: [] }, evaluation: { compute_binder_metrics: false } };
    await mount('complexa', mode, request);
    await click(mode === 'evaluate' ? 'Evaluate candidates' : 'Generate candidates');
    const wire = requests.find(r => r.url === '/api/jobs')!.body;
    expect(wire).toMatchObject({ model_id: 'complexa', mode, execution_target_id: null, params: { complexa_request: request } });
    expect(Object.keys(wire.params)).toEqual(['complexa_request']);
    const normalized = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c', "import sys,json;sys.path.insert(0,'platform/api');from services.complexa import normalize_job_request;print(json.dumps(normalize_job_request(sys.argv[1],json.loads(sys.argv[2]))))", mode, JSON.stringify(wire.params.complexa_request)], { cwd: resolve(process.cwd(), '../..'), encoding: 'utf8' }));
    expect(normalized).toEqual(request);
});

it('actual Project parent saves twice and cold hydrates the saved native request and explicit Local envelope', async () => {
    project = { schema: 'bms.project-workflow-setup.detail.v1', project_id: 'project-fixture', setup_context_id: 'setup-fixture', global_experiment_id: 'global-fixture', domain_experiment_id: 'domain-fixture', relationship_kind: 'primary', capability_id: 'rfd4_proteina', state: 'open', validation_state: 'incomplete', setup_destination: '/submit', return_uri: '/projects/project-fixture', generation: 1, project_label: 'Fixture', experiment_label: 'Fixture', workflow_label: 'RFD4', diagnostics: {}, field_errors: {}, draft: { editor_state: { generator: 'rfd4_proteina', native_mode: 'generate', rfd4_request: nativeRequest, execution_target_id: null, execution_policy: { remote_result_policy: 'manual' } }, native_job_request: { model_id: 'rfd4_proteina', mode: 'generate', params: { rfd4_request: nativeRequest }, execution_target_id: null } } };
    const route = '&project_id=project-fixture&setup_context_id=setup-fixture';
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'ambient-remote');
    await mount('rfd4_proteina', 'generate', undefined, route);
    expect((document.querySelector('[aria-label="Input name"]') as HTMLInputElement).value).toBe('n');
    await edit('Input name', 'second-save'); await click('Save draft');
    await vi.waitFor(() => expect(project.generation).toBe(2));
    await click('Save draft'); await vi.waitFor(() => expect(project.generation).toBe(3));
    expect(project.draft).toMatchObject({ editor_state: { execution_target_id: null }, native_job_request: { execution_target_id: null, params: { rfd4_request: { input: { name: 'second-save' } } } } });
    await unmount(); sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'ambient-remote');
    await mount('rfd4_proteina', 'generate', undefined, route);
    expect((document.querySelector('[aria-label="Input name"]') as HTMLInputElement).value).toBe('second-save');
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
    expect(requests.some(r => r.url === '/api/jobs')).toBe(false);
});

for (const model of ['rfd4_proteina', 'complexa']) for (const mode of ['generate', 'evaluate']) {
    it(`exports the actual ${model}/${mode} Template Manager envelope for real HTTP persistence replay`, async () => {
        const request = model === 'complexa'
            ? { task: 'protein_binder', task_name: 'http-native', operation: mode, seed: 0, reward: null, evaluation: { compute_binder_metrics: false } }
            : mode === 'generate' ? nativeRequest
                : { input: { kind: 'annotated', slots: ['exact-retained'] }, settings: { verbose: false, metrics_tags: [] } };
        await mount(model, mode, request); await click('Template Manager');
        const name = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'HTTP envelope'); name.dispatchEvent(new Event('input', { bubbles: true })); });
        await click('Save Template'); await vi.waitFor(() => expect(saved).toHaveLength(1));
        const transport = requests.find(r => r.url === '/api/user-templates' && r.method === 'post')!.body;
        expect(transport.params).toEqual(saved[0].params);
        expect(transport.params.native_job_request.params).toEqual({ [model === 'complexa' ? 'complexa_request' : 'rfd4_request']: request });
        expect(transport.params.native_job_request.execution_target_id).toBeNull();
        if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, `native-envelope-${model}-${mode}.json`), JSON.stringify(transport.params, null, 2));
    });
}
