import React, { act, useState } from 'react';
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
    "import sys,json;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings;print(json.dumps({'rfd4_proteina':{m:generation_settings(m) for m in ('generate','evaluate')}}))",
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
import { Rfd4ExpertSettings } from '../../src/components/Rfd4ExpertSettings';
import { BioXpSchemaControlContext } from '../../src/components/BioXpSchemaInput';
import { api } from '../../src/lib/api';
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
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, `rfd4-gap-wire-${expect.getState().currentTestName!.replace(/[^A-Za-z0-9]/g, '_')}.json`), JSON.stringify(requests, null, 2));
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
const groups = [
    ['Native profiles', ['groups'], 'Profiles and recycling'],
    ['Integration arguments', ['settings', 'generation', 'args'], 'Integration and partial / inverse sampling'],
    ['bb_ca', ['settings', 'generation', 'model', 'bb_ca'], 'Backbone schedule and simulation'],
    ['local_latents', ['settings', 'generation', 'model', 'local_latents'], 'Sequence latent schedule and simulation'],
    ['providers', ['providers'], 'Metric providers, constructors and native oracles'],
    ['metric_extensions', ['metric_extensions'], 'Metric providers, constructors and native oracles'],
    ['Guidance', ['guidance'], 'Guidance and dataset transforms'],
    ['Dataset transforms', ['settings', 'generation', 'dataset'], 'Guidance and dataset transforms'],
    ['nn', ['settings', 'nn'], 'Runtime, output and retained assets'],
    ['metrics_tags', ['settings', 'metrics_tags'], 'Runtime, output and retained assets'],
    ['bioir', ['settings', 'bioir'], 'Runtime, output and retained assets'],
] as const;
const current = () => JSON.parse(document.querySelector('[aria-label="Requested RFD4 values"]')!.textContent!);
const at = (value: any, path: readonly string[]) => path.reduce((v, key) => v?.[key], value);
async function action(label: string) {
    const node = document.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
    expect(node, label).toBeTruthy(); await act(async () => node!.click());
}
async function show(name: string) {
    const summary = [...document.querySelectorAll('summary')].find(node => node.textContent?.trim() === name);
    expect(summary, name).toBeTruthy();
    await act(async () => { const details = summary!.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}
async function explicit(label: string) { await edit(`${label} presence`, 'value'); }
async function editGroup(label: string) {
    switch (label) {
        case 'Native profiles': await explicit(`${label}.generation`); await edit(`${label}.generation`, '1'); return { generation: 'inverse_folding' };
        case 'Integration arguments': await explicit(`${label}.nsteps`); await edit(`${label}.nsteps`, '7'); return { nsteps: 7 };
        case 'bb_ca': case 'local_latents':
            await explicit(`${label}.schedule`); await explicit(`${label}.schedule.p`); await edit(`${label}.schedule.p`, '3'); return { schedule: { p: 3 } };
        case 'providers': await explicit('providers.refolding_oracles'); return { refolding_oracles: { profile: 'af2' } };
        case 'metric_extensions':
            await action('Add metric_extensions item'); await explicit('metric_extensions[0].name'); await edit('metric_extensions[0].name', 'gap_metric');
            return [{ implementation: inventories.rfd4_proteina.generate.parameter_schema.properties.metric_extensions.items.oneOf[0].properties.implementation.const, name: 'gap_metric' }];
        case 'Guidance': await explicit('Guidance.metrics'); await action('Add Guidance.metrics item'); return { metrics: ['contract_ca'] };
        case 'Dataset transforms':
            await explicit(`${label}.transform`); await explicit(`${label}.transform.path_length_search_cutoff`); await edit(`${label}.transform.path_length_search_cutoff variant`, '1'); return { transform: { path_length_search_cutoff: null } };
        case 'nn': await explicit('nn.torch_compile_mode'); return { torch_compile_mode: null };
        case 'metrics_tags': await action('Add metrics_tags item'); await edit('metrics_tags[0]', 'gap-tag'); return ['gap-tag'];
        case 'bioir': await explicit('bioir.cuda_graph'); await act(async () => document.querySelector<HTMLInputElement>('[aria-label="bioir.cuda_graph"]')!.click()); return { cuda_graph: false };
        default: throw new Error(label);
    }
}

it.each(groups)('activates, edits and removes omitted %s through the actual parent without residue', async (label, path, section) => {
    await mount('rfd4_proteina', 'generate', {});
    await disclose(section); await show('Requested and inherited identity');
    expect(current()).toEqual({});
    await action(`Set override for ${label}`);
    expect(document.querySelector(`[data-rfd4-override="${label}"]`)?.textContent).not.toContain('does not match');
    expect(at(current(), path)).toEqual(['metric_extensions', 'metrics_tags'].includes(label) ? [] : {});
    const edited = await editGroup(label);
    expect(at(current(), path)).toEqual(edited);
    await action(`Use profile value for ${label}`);
    expect(current()).toEqual({});
    expect(requests.some(row => row.url === '/api/jobs')).toBe(false);
});

it('viewing every group and profile reference leaves the fresh request omitted', async () => {
    await mount('rfd4_proteina', 'generate', {}); await click('Native settings');
    for (const section of new Set(groups.map(row => row[2]))) await show(section);
    await show('Requested and inherited identity'); await show('Profile reference');
    expect(current()).toEqual({});
    expect(document.querySelectorAll('[data-rfd4-override] button[aria-label^="Set override for "]').length).toBeGreaterThanOrEqual(11);
    expect(document.body.textContent).not.toContain('Retained value does not match');
    expect(requests.some(row => row.url.includes('/preview'))).toBe(false);
});

it.each([
    { settings: {} },
    { settings: { generation: {} } },
    { settings: { generation: { model: {} } } },
    { settings: { nn: {}, metrics_tags: [], generation: { args: {}, model: { local_latents: {} } } }, guidance: {} },
])('preserves preexisting explicit ancestors and empty siblings: %j', async initial => {
    await mount('rfd4_proteina', 'generate', initial);
    await disclose('Backbone schedule and simulation'); await show('Requested and inherited identity');
    await action('Set override for bb_ca'); await editGroup('bb_ca');
    await action('Use profile value for bb_ca'); expect(current()).toEqual(initial);
});

it('prunes shared introduced ancestors only after the last authored sibling is removed', async () => {
    await mount('rfd4_proteina', 'generate', {});
    await disclose('Backbone schedule and simulation'); await show('Sequence latent schedule and simulation'); await show('Requested and inherited identity');
    await action('Set override for bb_ca'); await action('Set override for local_latents');
    await action('Use profile value for bb_ca'); expect(current()).toEqual({ settings: { generation: { model: { local_latents: {} } } } });
    await action('Use profile value for local_latents'); expect(current()).toEqual({});
});

it('retains populated historical settings, false, zero, null and empty collections through unrelated edits and cold reopen', async () => {
    const retained = { ...nativeRequest, groups: {}, guidance: { metrics: [] }, providers: {}, metric_extensions: [], settings: { ...nativeRequest.settings,
        generation: { args: { nsteps: 7, guidance_w: 0, partial_t_backbone: null }, model: { bb_ca: { schedule: { p: 3 } }, local_latents: {} }, dataset: {} },
        nn: { torch_compile_mode: null }, metrics_tags: [], bioir: { cuda_graph: false } } };
    await mount('rfd4_proteina', 'generate', retained);
    await disclose('Integration and partial / inverse sampling'); await show('Requested and inherited identity');
    expect(current()).toEqual(retained); await edit('Integration arguments.nsteps', '9');
    const edited = { ...retained, settings: { ...retained.settings, generation: { ...retained.settings.generation, args: { ...retained.settings.generation.args, nsteps: 9 } } } };
    expect(current()).toEqual(edited);
    await unmount(); await mount('rfd4_proteina', 'generate', JSON.parse(JSON.stringify(edited)));
    await disclose('Requested and inherited identity'); expect(current()).toEqual(edited);
    await click('Generate candidates');
    expect(requests.find(row => row.url === '/api/jobs')!.body.params.rfd4_request).toEqual(edited);
});
async function disclose(name: string) {
    await click('Native settings');
    const summary = [...document.querySelectorAll('summary')].find(node => node.textContent?.trim().toLowerCase() === name.toLowerCase());
    expect(summary, name).toBeTruthy();
    await act(async () => { const details = summary!.parentElement as HTMLDetailsElement; details.open = true; details.dispatchEvent(new Event('toggle')); });
}

it('keeps root references, escaped definition names and native paths through object and array activation', async () => {
    const schema = structuredClone(inventories.rfd4_proteina.generate.parameter_schema);
    const settings = schema.properties.settings.properties;
    schema.$defs = { 'nn/native': settings.nn, tags: settings.metrics_tags, tag: settings.metrics_tags.items };
    schema.$defs.tags.items = { $ref: '#/$defs/tag' };
    settings.nn = { $ref: '#/$defs/nn~1native' }; settings.metrics_tags = { $ref: '#/$defs/tags' };
    const paths: Array<readonly (string | number)[]> = [];
    let latest: Record<string, any> = {};
    function Harness() {
        const [request, setRequest] = useState({}); latest = request;
        return <BioXpSchemaControlContext.Provider value={field => { paths.push(field.path); return undefined; }}>
            <Rfd4ExpertSettings request={request} schema={schema} onChange={setRequest} />
        </BioXpSchemaControlContext.Provider>;
    }
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<Harness />)); await show('Runtime, output and retained assets');
    expect(latest).toEqual({});
    await action('Set override for nn'); await explicit('nn.torch_compile_fullgraph');
    expect(latest).toEqual({ settings: { nn: { torch_compile_fullgraph: null } } });
    await action('Set override for metrics_tags'); await editGroup('metrics_tags');
    expect(latest).toEqual({ settings: { nn: { torch_compile_fullgraph: null }, metrics_tags: ['gap-tag'] } });
    expect(paths).toContainEqual(['settings', 'nn', 'torch_compile_fullgraph']);
    expect(paths).toContainEqual(['settings', 'metrics_tags', 0]);
    await action('Use profile value for nn'); await action('Use profile value for metrics_tags'); expect(latest).toEqual({});
});
it('distinguishes a retained incompatible collection from an omitted one without rewriting it', async () => {
    await mount('rfd4_proteina', 'generate', { guidance: 'historical-value' });
    await disclose('Guidance and dataset transforms'); await show('Requested and inherited identity');
    expect(document.querySelector('[data-rfd4-override="Guidance"]')?.textContent).toContain('Retained value does not match');
    expect(current()).toEqual({ guidance: 'historical-value' });
    await action('Use profile value for Guidance'); expect(current()).toEqual({});
});
async function editPersistenceValues() {
    await disclose('Integration and partial / inverse sampling');
    await action('Set override for Integration arguments'); await editGroup('Integration arguments');
    await explicit('Integration arguments.partial_t_backbone');
    await show('Runtime, output and retained assets'); await action('Set override for metrics_tags'); await editGroup('metrics_tags');
    await action('Set override for nn'); await show('Requested and inherited identity');
    const expected = { ...nativeRequest, settings: { ...nativeRequest.settings, generation: { args: { nsteps: 7, partial_t_backbone: null } }, metrics_tags: ['gap-tag'], nn: {} } };
    expect(current()).toEqual(expected); return expected;
}
function normalize(request: object) {
    return JSON.parse(execFileSync(process.env.BMS_SCHEMA_PYTHON ?? 'python', ['-c',
        "import sys,json;from scripts.lib.rfd4_proteina_contract import normalize_request;print(json.dumps(normalize_request(json.loads(sys.argv[1]),'generate')))", JSON.stringify(request)],
    { cwd: resolve(process.cwd(), '../..'), encoding: 'utf8' }));
}
it('saves edited object and array overrides through Template Manager, cold loads and emits the exact native request', async () => {
    await mount('rfd4_proteina', 'generate', nativeRequest);
    const expected = await editPersistenceValues();
    await click('Template Manager');
    const name = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'RFD4 gap overrides'); name.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template'); await vi.waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].params.editor_state.rfd4_request).toEqual(expected);
    expect(saved[0].params.native_job_request.params.rfd4_request).toEqual(expected);
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, 'template-envelope.json'), JSON.stringify(saved[0].params, null, 2));
    await unmount(); await mount('rfd4_proteina');
    await click('Template Manager'); await click('← Back to list'); await click('Load');
    await disclose('Requested and inherited identity'); expect(current()).toEqual(expected);
    await click('Generate candidates');
    const wire = requests.find(row => row.url === '/api/jobs')!.body.params.rfd4_request;
    expect(wire).toEqual(expected); expect(normalize(wire)).toEqual({ ...expected, version: 'rfd4_request_v1', mode: 'generate' });
});
it('saves twice and cold reopens edited object and array overrides through the actual Project parent', async () => {
    project = { schema: 'bms.project-workflow-setup.detail.v1', project_id: 'project-fixture', setup_context_id: 'setup-fixture', global_experiment_id: 'global-fixture', domain_experiment_id: 'domain-fixture', relationship_kind: 'primary', capability_id: 'rfd4_proteina', state: 'open', validation_state: 'incomplete', setup_destination: '/submit', return_uri: '/projects/project-fixture', generation: 1, project_label: 'Fixture', experiment_label: 'Fixture', workflow_label: 'RFD4', diagnostics: {}, field_errors: {}, draft: { editor_state: { generator: 'rfd4_proteina', native_mode: 'generate', rfd4_request: nativeRequest, execution_target_id: null, execution_policy: { remote_result_policy: 'manual' } }, native_job_request: { model_id: 'rfd4_proteina', mode: 'generate', params: { rfd4_request: nativeRequest }, execution_target_id: null } } };
    const route = '&project_id=project-fixture&setup_context_id=setup-fixture';
    await mount('rfd4_proteina', 'generate', undefined, route);
    const expected = await editPersistenceValues();
    await click('Save draft'); await vi.waitFor(() => expect(project.generation).toBe(2));
    await click('Save draft'); await vi.waitFor(() => expect(project.generation).toBe(3));
    expect(project.draft.editor_state.rfd4_request).toEqual(expected);
    expect(project.draft.native_job_request.params.rfd4_request).toEqual(expected);
    if (process.env.BMS_UI_EVIDENCE_DIR) writeFileSync(resolve(process.env.BMS_UI_EVIDENCE_DIR, 'project-envelope.json'), JSON.stringify(project.draft, null, 2));
    await unmount(); await mount('rfd4_proteina', 'generate', undefined, route);
    await disclose('Requested and inherited identity'); expect(current()).toEqual(expected);
    expect(normalize(project.draft.native_job_request.params.rfd4_request)).toEqual({ ...expected, version: 'rfd4_request_v1', mode: 'generate' });
    expect(requests.some(row => row.url === '/api/jobs')).toBe(false);
});
