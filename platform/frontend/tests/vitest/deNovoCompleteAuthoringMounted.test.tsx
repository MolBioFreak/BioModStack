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
async function openGroup(title: string) {
    const summary = [...document.querySelectorAll('summary')].find(node => node.textContent?.trim() === title);
    expect(summary, title).toBeTruthy();
    await act(async () => { const details = summary!.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}
async function saveTemplate() {
    await click('Template Manager');
    const node = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    expect(node).not.toBeNull();
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, 'D20 retained controls'); node.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template'); await vi.waitFor(() => expect(saved).toHaveLength(1));
}
const d20 = { input: { kind: 'unconditional', name: 'd20', chains: [{ chain_id: 'AA', min_length: 73, max_length: 99 }] }, settings: {
    seed: 0, inference_backend: 'native', precision: 'bf16-mixed', compile_lengths: [],
    nn: { torch_compile_mode: null, torch_compile_fullgraph: false },
    bioir: { dtype: 'bfloat16', cuda_graph: false, max_graphs: null, match_reference_rng: false },
    generation: { args: { nsteps: 20, partial_t_backbone: 0, partial_t_latent: null, inverse_folding: false }, model: { bb_ca: { schedule: { mode: 'log', p: 2 } }, local_latents: { schedule: { mode: 'power', p: 3 } } } },
    search: { checkpoints: null, save_states: false }, delete_failing_pre_oracle: false, metrics_tags: [],
} };
it('D20 renders typed integration and runtime controls in Native settings without eager expert bodies', async () => {
    await mount('rfd4_proteina', 'generate', d20);
    expect(document.querySelector('[aria-label="inference_backend"]')).toBeNull();
    await click('Native settings');
    expect(document.querySelector('[data-rfd4-expert]')).not.toBeNull();
    expect(document.querySelector('[aria-label="inference_backend"]')).toBeNull();
    await openGroup('Runtime, output and retained assets');
    expect(document.querySelector('[aria-label="inference_backend"]')?.tagName).toBe('SELECT');
    expect(document.body.textContent).toContain('Native/BioIR incompatibilities remain native validation');
    expect(document.querySelector('[aria-label="Integration arguments.nsteps"]')).toBeNull();
    expect(document.querySelector('textarea')).toBeNull();
    const runtime = [...document.querySelectorAll('summary')].find(node => node.textContent === 'Runtime, output and retained assets')!;
    expect(runtime.closest('[data-rfd4-expert]')).not.toBeNull();
    await openGroup('Integration and partial / inverse sampling');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Integration arguments.nsteps"]')?.type).toBe('number');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Integration arguments.partial_t_backbone"]')?.value).toBe('0');
    await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs')!.body.params.rfd4_request).toEqual(d20);
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, 'D20-rendered.html'), document.body.innerHTML);
});
it('native to BioIR to native retains all requested expert values across serialized cold Load after populated navigation', async () => {
    await mount('rfd4_proteina', 'generate', d20); await click('Native settings'); await openGroup('Runtime, output and retained assets');
    await edit('inference_backend', '1');
    await edit('bioir.dtype', 'float32');
    await saveTemplate();
    const expected = structuredClone(d20); expected.settings.inference_backend = 'bioir'; expected.settings.bioir.dtype = 'float32';
    expect(saved[0].params.native_job_request.params.rfd4_request).toEqual(expected);
    expect(saved[0].params.editor_state.execution_target_id).toBeNull();
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, 'D20-envelope.json'), JSON.stringify(saved[0].params, null, 2));
    // Serialize through the actual Template Manager envelope, then cold mount
    // with a populated, different engine before explicit Load replaces history.
    saved = JSON.parse(JSON.stringify(saved));
    await unmount(); sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'ambient-remote');
    await mount('complexa', 'generate', { task: 'protein_binder', seed: 999, task_name: 'discard-populated' });
    const engine = [...document.querySelectorAll<HTMLSelectElement>('select')].find(node => [...node.options].some(option => option.value === 'rfd4_proteina'))!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(engine, 'rfd4_proteina'); engine.dispatchEvent(new Event('change', { bubbles: true })); });
    await settle();
    await click('Template Manager'); await click('← Back to list'); await click('Load'); await settle();
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[data-native-de-novo="rfd4_proteina"]')).not.toBeNull(); });
    expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
    await click('Native settings'); await openGroup('Runtime, output and retained assets');
    expect(document.querySelector<HTMLInputElement>('[aria-label="bioir.dtype"]')?.value).toBe('float32');
    await edit('inference_backend', '0');
    await openGroup('Sequence latent schedule and simulation'); await edit('local_latents.schedule.p', '4');
    expected.settings.inference_backend = 'native'; expected.settings.generation.model.local_latents.schedule.p = 4;
    await click('Generate candidates');
    const wire = requests.find(r => r.url === '/api/jobs')!.body;
    expect(wire.params).toEqual({ rfd4_request: expected }); expect(wire.execution_target_id).toBeNull();
    const normalized = JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c', "import sys,json;from scripts.lib.rfd4_proteina_contract import normalize_request;print(json.dumps(normalize_request(json.load(sys.stdin),'generate')))"] , { cwd: resolve(process.cwd(), '../..'), input: JSON.stringify(wire.params.rfd4_request), encoding: 'utf8' }));
    expect(normalized.settings).toEqual(expected.settings);
});
it('grouped edits preserve omitted settings and explicit false zero null empty without adding defaults or metadata', async () => {
    await mount('rfd4_proteina', 'generate', d20); await click('Native settings');
    await openGroup('Runtime, output and retained assets');
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Use profile value for precision"]')!.click());
    await openGroup('Integration and partial / inverse sampling'); await edit('Integration arguments.nsteps', '21');
    await click('Generate candidates');
    const expected: any = structuredClone(d20); delete expected.settings.precision; expected.settings.generation.args.nsteps = 21;
    const wire = requests.find(r => r.url === '/api/jobs')!.body;
    expect(wire.params).toEqual({ rfd4_request: expected });
    expect(JSON.stringify(wire)).not.toContain('x-bms-settings-group');
    expect(wire.sequence_design).toBeNull(); expect(wire).not.toHaveProperty('invocation');
});
it('each leaf of the selected native settings schema has a typed mounted control and exact Axios value', async () => {
    const schema = inventories.rfd4_proteina.generate.parameter_schema.properties.settings;
    const leaves: string[] = [];
    function populated(field: any, label: string): any {
        const alternatives = field.oneOf ?? field.anyOf ?? (Array.isArray(field.type) ? field.type.map((type: string) => ({ ...field, type })) : [field]);
        const selected = alternatives.find((choice: any) => choice.type !== 'null') ?? alternatives[0];
        if (selected.properties || selected.type === 'object') {
            const entries = Object.entries(selected.properties ?? {}).map(([key, child]) => [key, populated(child, `${label}.${key}`)]);
            if (selected.additionalProperties && typeof selected.additionalProperties === 'object') entries.push(['0.5', populated(selected.additionalProperties, `${label}.0.5`)]);
            return Object.fromEntries(entries);
        }
        if (selected.type === 'array' || selected.items || selected.prefixItems) return (selected.prefixItems ?? [selected.items ?? {}]).map((child: any, index: number) => populated(child, `${label}[${index}]`));
        if (selected.type === 'null') return null;
        leaves.push(label);
        if (Object.hasOwn(selected, 'const')) return selected.const;
        if (selected.enum?.length) return selected.enum[0];
        if (selected.default != null) return selected.default;
        return selected.type === 'boolean' ? false : ['integer', 'number'].includes(selected.type) ? 0 : '';
    }
    function controlLabel(path: string) {
        return path.replace(/^settings\.settings\.generation\.args/, 'Integration arguments')
            .replace(/^settings\.settings\.generation\.model\./, '')
            .replace(/^settings\.settings\.generation\.dataset/, 'Dataset transforms')
            .replace(/^settings\.settings\.generation\./, '')
            .replace(/^settings\.settings\.search\.checkpoints\.([^.]+(?:\.[^.]+)?)\./, 'Checkpoint $1.')
            .replace(/^settings\.settings\.search\./, '')
            .replace(/^settings\.settings\.(num_replicates|batch_size|seed)$/, 'Requested $1')
            .replace(/^settings\.settings\./, '');
    }
    const settings = populated(schema, 'settings.settings');
    await mount('rfd4_proteina', 'generate', { ...d20, settings }); await click('Native settings');
    for (const title of ['Profiles and recycling', 'Integration and partial / inverse sampling', 'Backbone schedule and simulation', 'Sequence latent schedule and simulation', 'Search checkpoint timeline', 'Guidance and dataset transforms', 'Runtime, output and retained assets', 'Primary amount overrides']) await openGroup(title);
    for (const label of leaves) {
        const control = [...document.querySelectorAll<HTMLInputElement | HTMLSelectElement>('[aria-label]')].find(node => node.getAttribute('aria-label') === controlLabel(label));
        expect(control, `${label} → ${controlLabel(label)}`).toBeTruthy(); expect(['INPUT', 'SELECT']).toContain(control!.tagName);
    }
    await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs')!.body.params.rfd4_request.settings).toEqual(settings);
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, 'D20-selected-native-leaf-inventory.json'), JSON.stringify({ scope: 'All settings leaves for the selected non-null schema variants; schema-rendering fixture, not a native execution recipe', leaves }, null, 2));
});
it('every current settings property has its mounted native override control without hydration', async () => {
    await mount('rfd4_proteina', 'generate', { ...d20, settings: {} }); await click('Native settings');
    for (const title of ['Profiles and recycling', 'Integration and partial / inverse sampling', 'Backbone schedule and simulation', 'Sequence latent schedule and simulation', 'Search checkpoint timeline', 'Guidance and dataset transforms', 'Runtime, output and retained assets', 'Primary amount overrides']) await openGroup(title);
    const settings = inventories.rfd4_proteina.generate.parameter_schema.properties.settings;
    const inventory: string[] = [];
    for (const key of Object.keys(settings.properties)) {
        const labels = key === 'generation' ? ['Set override for Integration arguments', 'Set override for bb_ca', 'Set override for local_latents', 'Set override for Dataset transforms']
            : key === 'search' ? ['Integration step']
            : ['num_replicates', 'batch_size', 'seed'].includes(key) ? [`Requested ${key}`]
            : [[settings.properties[key], ...(settings.properties[key].anyOf ?? []), ...(settings.properties[key].oneOf ?? [])].some((child: any) => ['object', 'array'].includes(child.type) || child.properties || child.items) ? `Set override for ${key}` : key];
        for (const label of labels) {
            expect([...document.querySelectorAll('[aria-label]')].filter(node => node.getAttribute('aria-label') === label), `${key}: ${label}`).toHaveLength(1);
            inventory.push(label);
        }
    }
    await click('Generate candidates');
    expect(requests.find(r => r.url === '/api/jobs')!.body.params.rfd4_request.settings).toEqual({});
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, 'D20-settings-mounted-inventory.json'), JSON.stringify(inventory, null, 2));
});
