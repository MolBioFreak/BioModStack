import React, { act } from 'react';
import { readFileSync, appendFileSync } from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AxiosError } from 'axios';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { JobSubmission } from '../../src/components/JobSubmission';
const project = vi.hoisted(() => ({ value: null as any, save: vi.fn() }));
vi.mock('../../src/lib/projectManager', async original => ({ ...await original<typeof import('../../src/lib/projectManager')>(), getProjectWorkflowSetup: vi.fn(async () => project.value), saveProjectWorkflowSetupDraft: project.save }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
// Unrelated workflow forms only; actual De Novo parent, native controls, template modal,
// placement picker, submission helper and Axios request transport remain mounted.
vi.mock('../../src/components/AntibodyDenovoTemplate', () => ({ AntibodyDenovoTemplate: () => null }));
vi.mock('../../src/components/StructurePredictionTemplate', () => ({ StructurePredictionTemplate: () => null }));
vi.mock('../../src/components/MolecularDynamicsTemplate', () => ({ MolecularDynamicsTemplate: () => null }));
vi.mock('../../src/components/OligoDesignerTemplate', () => ({ OligoDesignerTemplate: () => null }));
vi.mock('../../src/components/conformationalMapping/ConformationalMappingLauncher', () => ({ ConformationalMappingLauncher: () => null }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => null }));


// Inert transport fixtures; no application Job or scientific execution.
const geometry = { geometry_id: 'geom_fixture', source_id: 'cad_fixture', source_format: 'stl', source_parser: 'stl_ascii_v1', source_unit: 'angstrom', angstrom_per_unit: 1,
    dimensions_angstrom: [40, 35, 30], bounds_angstrom: [0, 0, 0, 40, 35, 30], vertex_count: 3, face_count: 1, point_count: 4096, sdf_grid_shape: [48, 48, 48], sdf_sign: 'positive_inside',
    ...Object.fromEntries(['geometry_sha256', 'manifest_sha256', 'source_sha256', 'preview_obj_sha256', 'point_pool_sha256', 'sdf_sha256'].map(key => [key, 'a'.repeat(64)])) };
const field = (name: string, type: string, value: unknown) => ({ name, type, default: value });
const definition = (params: any[]) => ({ model_version: 'fixture', schema_sha256: 'a'.repeat(64), params, initial_values: Object.fromEntries(params.map(p => [p.name, p.default])), contextual_defaults: {}, contextual_default_reason: '', json_schema: { type: 'object', properties: Object.fromEntries(params.map(p => [p.name, { type: p.type, default: p.default }])) } });
const inventories: Record<string, any> = {
    proteinmpnn: { engine: 'proteinmpnn', ...definition([field('mpnn_temperature', 'number', 0.1), field('mpnn_omitAAs', 'string', 'CX'), field('mpnn_relax_output', 'boolean', true)]) },
    fampnn: { engine: 'fampnn', ...definition([field('fampnn_temperature', 'number', 0.1), field('fampnn_batch_size', 'integer', 1), field('fampnn_repack_last', 'boolean', false)]) },
    caliby_experimental: { engine: 'caliby_experimental', ...definition([field('temperature', 'number', 0.1), field('verbose', 'boolean', true), field('num_workers', 'integer', 8)]), input_settings_schema: { type: 'object', properties: {
        fixed_pos_seq: { type: 'string', default: '' }, pos_restrict_aatype: { type: 'object', default: {}, additionalProperties: { type: 'array', items: { type: 'string', enum: ['A', 'C'] } } },
        gaussian: { anyOf: [{ type: 'object', properties: { count: { type: 'integer' } }, additionalProperties: false }, { type: 'null' }], default: null },
    } } },
};
const settings = { schema: 'bms_shape_settings_v1', rfd3: { ...definition([field('num_timesteps', 'integer', 200)]), model_id: 'rfdiffusion', mode: 'shape_blueprint' }, sequence_engines: Object.keys(inventories), validators: {
    esmfold2: definition([field('esmfold2_num_recycles', 'integer', 3)]), boltz2: definition([field('boltz2_diffusion_samples', 'integer', 1)]), protenix_v2: definition([field('protenix_use_msa', 'boolean', false), field('protenix_num_samples', 'integer', 1)]),
} };
const response = (data: unknown): any => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const originalAdapter = api.defaults.adapter;
let root: Root | undefined;
let client: QueryClient;
let posts: Array<{ url: string; body: any; header: unknown }>;
let saved: any[];
let failGeometry = false;
let failSettings = false;
let geometryRows: any[];
let review = false;
let nativeInventory: any = null;
const ready = { id: 'vast:fixture', name: 'Fixture worker', state: 'ready', active: true, provider: 'vast', capabilities: {}, pricing: {} };
beforeEach(() => {
    posts = []; saved = []; geometryRows = [geometry]; failGeometry = false; failSettings = false; review = false; nativeInventory = null; project.value = null;
    window.history.replaceState({}, '', '/submit');
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ setTransform() {}, clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() {}, stroke() {} } as any);
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => '# bms_shape_canonical_obj_v1\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n' })));
    api.defaults.adapter = async config => {
        const url = String(config.url);
        if (config.method === 'get') {
            if (url === '/api/shape-blueprint/geometries') { if (failGeometry) throw new Error('Fixture read failure'); return response({ geometries: geometryRows }); }
            if (url === '/api/shape-blueprint/settings') { if (failSettings) throw new Error('Fixture discovery failure'); return response(nativeInventory?.settings ?? settings); }
            if (url.includes('/sequence-settings/')) return response((nativeInventory?.sequences ?? inventories)[url.split('/').at(-1)!]);
            if (url.startsWith('/api/launch-contexts/')) return response({ schema: 'bms.launch-context.v1', launch_context_id: 'project-destination', project_id: 'project-fixture', global_experiment_id: 'experiment-fixture', domain_experiment_id: 'domain-fixture', workflow_id: null, workflow_revision_id: null, pinned_gpu: null, return_uri: '/projects/project-fixture', source_receipt_id: 'fixture-receipt', state: 'issued', issued_at: '2026-01-01', expires_at: '2099-01-01' });
            if (url === '/api/execution-targets') return response([ready]);
            if (url.includes('/system')) return response({ gpus: [], gpu_error: null });
            if (url.includes('user-templates')) return response(saved);
            if (url.endsWith('/runtime-inventory')) return response(null);
            return response([]);
        }
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        posts.push({ url, body, header: config.headers.get('X-BMS-Launch-Context-ID') });
        if (url === '/api/shape-blueprint/requests') {
            if (review && !body.execution_plan_approval) throw new AxiosError('Prepared review', 'ERR_BAD_REQUEST', config, undefined, { ...response({ detail: { code: 'remote_prepared_job_review_required', job_request: { name: body.name, model_id: 'protein_modification_experimental', mode: 'shape_blueprint', params: { shape_request_path: '/fixture/request.json' }, execution_target_id: body.execution_target_id, launch_context_id: body.launch_context_id }, response_context: { request_id: 'fixture', job_id: 'fixture' } } }), status: 409, config });
            return response({ job_id: 'fixture-no-live-job', request_id: 'fixture', reused: false });
        }
        if (url === '/api/jobs/execution-plan/preview') return response({ approval_digest: 'd'.repeat(64), admissible: true, request: body, plan: { requested_json: body.params, effective_json: body.params, source_identity: { revision: 'fixture', tree: 'fixture' }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] });
        if (url.includes('user-templates')) { const item = { ...body, id: 'saved-fixture', created_at: '2026-01-01', updated_at: '2026-01-01' }; saved.push(item); return response(item); }
        throw new Error(`Unexpected mutation ${url}`);
    };
});
afterEach(async () => { if (process.env.BMS_SHAPE_REQUEST_EVIDENCE) appendFileSync(process.env.BMS_SHAPE_REQUEST_EVIDENCE, JSON.stringify({ fixture: true, test: expect.getState().currentTestName, posts }) + '\n'); if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); api.defaults.adapter = originalAdapter; vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function mount(node: React.ReactNode, entry = '/submit?template=protein_modification_experimental') {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}>{node}</MemoryRouter></QueryClientProvider>)); await settle();
}
async function unmount() { await act(async () => root!.unmount()); root = undefined; client.clear(); document.body.replaceChildren(); }
function control(name: string): HTMLInputElement | HTMLSelectElement {
    const direct = document.querySelector<HTMLInputElement | HTMLSelectElement>(`input[aria-label="${name}"],select[aria-label="${name}"]`);
    const label = [...document.querySelectorAll('label')].find(el => [...el.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim() === name);
    const input = direct ?? label?.querySelector('input,select'); expect(input, name).toBeTruthy(); return input as ReturnType<typeof control>;
}
function reveal(input: Element) { let ancestor = input.parentElement; while (ancestor) { if (ancestor.tagName === 'DETAILS') (ancestor as HTMLDetailsElement).open = true; ancestor = ancestor.parentElement; } }
async function edit(name: string, value: string) { const input = control(name); await act(async () => { reveal(input); Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')!.set!.call(input, value); input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function toggle(name: string) { await act(async () => { reveal(control(name)); (control(name) as HTMLInputElement).click(); }); await settle(); }
async function click(text: string, within: ParentNode = document) { const button = [...within.querySelectorAll<HTMLButtonElement>('button')].find(e => e.textContent?.trim() === text && !e.closest('[hidden]')); expect(button, text).toBeTruthy(); await act(async () => button!.click()); await settle(); }

it('Project Save draft and reopen hydrate the real parent before its first child report', async () => {
    const draft = { modification_mode: 'shape_blueprint', shape_geometry_id: geometry.geometry_id, shape_sequence_policy: 'external', shape_sequence_engine: 'fampnn', shape_sequence_settings_by_engine: { proteinmpnn: { mpnn_omitAAs: '', mpnn_relax_output: false }, fampnn: { fampnn_temperature: 0 }, caliby_experimental: { num_workers: 0, verbose: false } }, shape_rfd3_settings: { num_timesteps: '' }, shape_validator_settings_by_engine: { protenix_v2: { protenix_use_msa: false, protenix_num_samples: 0 } } };
    project.value = { project_id: 'project-fixture', setup_context_id: 'setup-fixture', generation: 1, draft, project_label: 'Fixture', experiment_label: 'Fixture', workflow_label: 'De Novo Design', state: 'open', return_uri: '/projects/project-fixture', field_errors: {} };
    project.save.mockImplementation(async (_p: unknown, _s: unknown, request: any) => { project.value = { ...project.value, generation: 2, draft: JSON.parse(JSON.stringify(request.draft)) }; return project.value; });
    const entry = '/submit?template=protein_modification_experimental&project_id=project-fixture&setup_context_id=setup-fixture';
    await mount(<JobSubmission />, entry); await until('Geometry preview'); await click('Sequence design');
    await edit('Sequence engine', 'caliby_experimental'); await edit('temperature', ''); await click('Save draft');
    expect(project.value.draft.shape_sequence_settings_by_engine.caliby_experimental).toMatchObject({ num_workers: 0, verbose: false, temperature: '' });
    expect(project.value.draft.shape_sequence_settings_by_engine.proteinmpnn).toMatchObject(draft.shape_sequence_settings_by_engine.proteinmpnn);
    expect(project.value.draft.modification_mode).toBe('shape_blueprint');
    expect(project.value.draft.shape_submitted_request).toMatchObject({ geometry_id: geometry.geometry_id, sequence_engine: 'caliby_experimental', sequence_settings: { num_workers: 0, verbose: false, temperature: '' }, rfd3_settings: { num_timesteps: '' } });
    expect(project.value.draft.shape_submitted_request).not.toHaveProperty('launch_context_id');
    expect(project.value.draft.shape_submitted_request).not.toHaveProperty('shape_sequence_settings_by_engine');
    await unmount(); await mount(<JobSubmission />, entry); await until('Geometry preview'); await click('Sequence design');
    expect(control('temperature').value).toBe(''); expect(control('num_workers').value).toBe('0');
    await click('RFD3'); expect(control('num_timesteps').value).toBe(''); expect(posts.some(p => p.url === '/api/shape-blueprint/requests')).toBe(false);
    await edit('num_timesteps', '155'); await click('Sequence design'); await edit('temperature', '0.2'); await click('Save draft');
    const prepared = project.value.draft.shape_submitted_request;
    await click('Run'); await click('Launch Shape Blueprint');
    const { launch_context_id: _destination, execution_policy: _policy, execution_target_id: _target, ...submitted } = latest().body;
    expect(submitted).toEqual(prepared);
});

it.runIf(Boolean(process.env.BMS_SHAPE_INVENTORY))('the actual model-owned inventories render and submit through the parent without schema-only field accounting', async () => {
    nativeInventory = JSON.parse(readFileSync(process.env.BMS_SHAPE_INVENTORY!, 'utf8'));
    await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={{ modification_mode: 'shape_blueprint', shape_geometry_id: geometry.geometry_id }} />);
    await click('RFD3'); await edit('num_timesteps', '175');
    await click('Prediction');
    const expectedPredictors: Record<string, any> = {};
    for (const [id, definition] of Object.entries(nativeInventory.settings.validators) as Array<[string, any]>) {
        const panel = document.querySelector(`[aria-label="${id} prediction"]`)!;
        expect([...panel.querySelectorAll('[data-shape-native-field]')].map(el => el.getAttribute('data-shape-native-field')).sort()).toEqual(definition.params.map((p: any) => p.name).sort());
        const sample = definition.params.find((p: any) => /^(num_diffusion_samples|boltz_diffusion_samples|protenix_n_sample)$/.test(p.name)).name;
        await edit(sample, '2'); expectedPredictors[id] = { ...definition.initial_values, [sample]: 2 };
    }
    await click('Sequence design'); await edit('Sequence policy', 'external');
    for (const id of ['proteinmpnn', 'fampnn', 'caliby_experimental']) {
        await edit('Sequence engine', id);
        const definition = nativeInventory.sequences[id];
        const panel = control('Sequence engine').closest('div.space-y-4')!;
        const actual = [...panel.querySelectorAll('[data-shape-native-field]')].map(el => el.getAttribute('data-shape-native-field'));
        expect(actual.sort()).toEqual([...definition.params.map((p: any) => p.name), ...Object.keys(definition.input_settings_schema?.properties ?? {})].sort());
        expect(panel.textContent).not.toContain('No typed schema');
        const temperature = definition.params.find((p: any) => /temperature$/.test(p.name)).name;
        await edit(temperature, '0.25');
        await click('Run'); await click('Launch Shape Blueprint');
        expect(latest().body.sequence_settings).toEqual({ ...definition.initial_values, [temperature]: 0.25 });
        expect(latest().body.rfd3_settings).toEqual({ ...nativeInventory.settings.rfd3.initial_values, num_timesteps: 175 });
        expect(latest().body.validator_settings).toEqual(expectedPredictors);
        await click('Sequence design');
    }
});

const initial = { modification_mode: 'shape_blueprint', shape_geometry_id: geometry.geometry_id };
const latest = () => posts.filter(p => p.url === '/api/shape-blueprint/requests').at(-1)!;
async function until(text: string) { await vi.waitFor(async () => { await settle(); expect(document.body.textContent).toContain(text); }); }

it('inactive Shape validator fields wait for inspection while authoritative defaults and saved drafts hydrate unchanged', async () => {
    const draft = vi.fn();
    await mount(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={{ ...initial, shape_validator_suite: ['esmfold2'], shape_validator_settings_by_engine: { boltz2: { boltz2_diffusion_samples: 0 }, protenix_v2: { protenix_use_msa: false, protenix_num_samples: 0 } } }} />);
    await until('Geometry preview'); await click('Prediction');
    const panel = document.querySelector('[aria-label="boltz2 prediction"]')!;
    expect(panel.querySelector('[data-shape-native-field]')).toBeNull();
    expect(document.querySelector('[aria-label="esmfold2 prediction"] [data-shape-native-field]')).not.toBeNull();
    const before = structuredClone(draft.mock.calls.at(-1)![0]);
    await act(async () => (panel.querySelector('summary') as HTMLElement).click()); await settle();
    expect(control('boltz2_diffusion_samples').value).toBe('0');
    expect(draft.mock.calls.at(-1)![0]).toEqual(before);
    expect(posts).toHaveLength(0);
});

it('keeps the real mesh canvas and camera controls mounted across all freely accessible sections', async () => {
    await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={initial} />);
    await until('3 vertices · 1 faces'); const canvas = document.querySelector('canvas'); const fetchCount = vi.mocked(fetch).mock.calls.length;
    await act(async () => document.querySelector<HTMLButtonElement>('[aria-label="Rotate canonical mesh left"]')!.click());
    for (const tab of ['RFD3', 'Sequence design', 'Prediction', 'Run', 'Geometry']) { await click(tab, document.querySelector('[aria-label="Shape sections"]')!); expect(document.querySelector('canvas')).toBe(canvas); }
    expect(vi.mocked(fetch).mock.calls.length).toBe(fetchCount);
    expect(document.querySelector('a[download]')?.getAttribute('href')).toContain('geom_fixture/preview.obj');
    await click('Upload OBJ / STL'); expect(control('Geometry file').getAttribute('accept')).toBe('.obj,.stl'); await edit('Source units', 'nanometer');
    await click('RFD3'); await click('Geometry'); expect(control('Source units').value).toBe('nanometer');
});

it('edits all designers and nested typed input, saves, reopens and submits only active native settings', async () => {
    const manager = vi.fn();
    await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={initial} onOpenTemplateManager={manager} />);
    await click('RFD3'); await edit('num_timesteps', ''); await edit('Deterministic seed', '');
    await click('Sequence design'); await edit('Sequence policy', 'external'); await edit('mpnn_omitAAs', ''); await toggle('mpnn_relax_output');
    await edit('Sequence engine', 'fampnn'); await edit('fampnn_temperature', '0'); await edit('fampnn_batch_size', '');
    await edit('Sequence engine', 'caliby_experimental'); await edit('num_workers', '0'); await toggle('verbose'); await edit('fixed_pos_seq', 'A:1'); await edit('fixed_pos_seq', '');
    await edit('pos_restrict_aatype property name', 'A:1'); await click('Add pos_restrict_aatype property'); await click('Add pos_restrict_aatype.A:1 item');
    await edit('gaussian variant', '0'); await edit('gaussian variant', '1');
    await click('Prediction'); await edit('protenix_num_samples', '2'); await toggle('Include protenix_v2');
    await click('Generate', document.querySelector('[aria-label="Design task"]')!); await click('Shape', document.querySelector('[aria-label="Design task"]')!);
    await until('Geometry preview');
    await click('Template Manager'); const snapshot = JSON.parse(JSON.stringify(manager.mock.lastCall![0].currentParams));
    expect(snapshot.shape_rfd3_settings.num_timesteps).toBe(''); expect(snapshot.shape_numeric_drafts.seed).toBe('');
    expect(snapshot.shape_sequence_settings_by_engine.fampnn).toMatchObject({ fampnn_temperature: 0, fampnn_batch_size: '', fampnn_repack_last: false });
    expect(snapshot.shape_sequence_input_settings_by_engine.caliby_experimental).toMatchObject({ gaussian: null, fixed_pos_seq: '', pos_restrict_aatype: { 'A:1': ['A'] } });
    await unmount(); await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={snapshot} />);
    await click('Sequence design'); expect(control('num_workers').value).toBe('0'); expect((control('verbose') as HTMLInputElement).checked).toBe(false);
    await edit('Sequence engine', 'fampnn'); expect(control('fampnn_batch_size').value).toBe(''); await edit('Sequence engine', 'proteinmpnn'); expect(control('mpnn_omitAAs').value).toBe('');
    await edit('Sequence engine', 'caliby_experimental'); await click('RFD3'); expect(control('num_timesteps').value).toBe(''); expect(control('Deterministic seed').value).toBe('');
    await edit('num_timesteps', '150'); await edit('Deterministic seed', '0'); await click('Run'); await click('Launch Shape Blueprint');
    expect(latest().body).toMatchObject({ sequence_engine: 'caliby_experimental', sequence_settings: { num_workers: 0, verbose: false }, rfd3_settings: { num_timesteps: 150 }, sequence_input_settings: { gaussian: null }, execution_target_id: null });
    expect(latest().body.validator_settings).not.toHaveProperty('protenix_v2'); expect(latest().body.sequence_settings).not.toHaveProperty('fampnn_temperature'); expect(latest().body).not.toHaveProperty('shape_sequence_settings_by_engine');
});

it('uses the real JobSubmission clone and template save/load rather than a leaf draft spy', async () => {
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: 'protein_modification_experimental', mode: 'shape_blueprint', name: 'Clone fixture', params: { ...initial, shape_sequence_policy: 'external', shape_sequence_engine: 'caliby_experimental', shape_sequence_settings: { num_workers: 0, verbose: false }, shape_sequence_input_settings: { gaussian: null } } }));
    await mount(<JobSubmission />); await until('Geometry preview');
    await click('Sequence design'); expect(control('Sequence engine').value).toBe('caliby_experimental');
    await edit('temperature', ''); await click('Template Manager');
    const input = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Saved Shape fixture'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template'); expect(saved[0].params.shape_sequence_settings.temperature).toBe('');
    await click('Load'); await click('Sequence design'); expect(control('temperature').value).toBe(''); expect(control('num_workers').value).toBe('0');
    await edit('temperature', '0'); await click('Run'); await click('Launch Shape Blueprint'); expect(latest().body.sequence_settings.temperature).toBe(0);
});

it('distinguishes cold failure, successful emptiness and missing saved geometry, then recovers the mounted query', async () => {
    failGeometry = true;
    await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={initial} />);
    await until('Geometry list could not refresh'); expect(document.body.textContent).not.toContain('No saved geometry yet'); expect(document.body.textContent).not.toContain('is unavailable. Select');
    failGeometry = false; geometryRows = []; await click('Retry geometry list'); await until('No saved geometry yet'); expect(document.body.textContent).toContain('is unavailable. Select');
    geometryRows = [geometry]; await act(async () => { await client.refetchQueries({ queryKey: ['shape-geometries'] }); }); await until('3 vertices · 1 faces');
    const canvas = document.querySelector('canvas'); failGeometry = true; await act(async () => { await client.refetchQueries({ queryKey: ['shape-geometries'] }); });
    expect(document.querySelector('canvas')).toBe(canvas); expect(control('Geometry').value).toBe(geometry.geometry_id);
    await until('Geometry list could not refresh'); failGeometry = false; await click('Retry geometry list'); expect(document.querySelector('canvas')).toBe(canvas);
});

it('retries only the failed surface read and retains the first source across reordered inventory', async () => {
    geometryRows = [{ ...geometry, original_filename: 'fixture-shell.stl' }];
    vi.mocked(fetch).mockRejectedValueOnce(new Error('Fixture surface offline'));
    await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={{ modification_mode: 'shape_blueprint' }} />);
    await until('Fixture surface offline'); expect(control('Geometry').textContent).toContain('fixture-shell.stl');
    await click('Retry surface preview'); await until('3 vertices · 1 faces');
    const canvas = document.querySelector('canvas'); geometryRows = [{ ...geometry, geometry_id: 'geom_other' }, ...geometryRows];
    await act(async () => { await client.refetchQueries({ queryKey: ['shape-geometries'] }); }); await settle();
    expect(control('Geometry').value).toBe('geom_fixture'); expect(document.querySelector('canvas')).toBe(canvas);
    expect(posts).toEqual([]);
});

it('late native discovery and explicit retry preserve null, false, zero and cleared numeric drafts', async () => {
    failSettings = true;
    await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={{ ...initial, shape_rfd3_settings: { num_timesteps: '' }, shape_validator_settings_by_engine: { protenix_v2: { protenix_use_msa: false, protenix_num_samples: 0 } } }} />);
    await click('RFD3'); await until('Native settings could not refresh'); failSettings = false; await click('Retry native settings');
    expect(control('num_timesteps').value).toBe(''); await click('Prediction'); expect(control('protenix_num_samples').value).toBe('0'); expect((control('protenix_use_msa') as HTMLInputElement).checked).toBe(false);
});

it('explicit standalone beats ambient context and Skip retains drafts without submitting them', async () => {
    window.history.replaceState({}, '', '/submit?launch_context_id=ambient-other');
    await mount(<ProteinModificationTemplate onBack={() => {}} launchContextId={null} initialValues={{ ...initial, shape_sequence_policy: 'external', shape_sequence_engine: 'caliby_experimental', shape_sequence_settings: { num_workers: 0, verbose: false } }} />);
    await click('Sequence design'); await edit('Sequence policy', 'skip'); await click('Run'); await click('Launch Shape Blueprint');
    expect(latest().body).toMatchObject({ launch_context_id: null, sequences_per_backbone: 0, sequence_settings: {}, sequence_input_settings: {} }); expect(latest().header).toBeUndefined();
    await click('Sequence design'); await edit('Sequence policy', 'external'); expect(control('num_workers').value).toBe('0'); expect((control('verbose') as HTMLInputElement).checked).toBe(false);
});

it.each([true, false])('real mounted Shape submit preserves Project context through remote review and approval=%s', async approve => {
    await import('../../src/components/ExecutionPlanApproval'); review = true;
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, ready.id);
    window.history.replaceState({}, '', '/submit?launch_context_id=ambient-other');
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: 'protein_modification_experimental', mode: 'shape_blueprint', name: 'Remote fixture', params: initial }));
    await mount(<JobSubmission />, '/submit?template=protein_modification_experimental&modification_mode=shape_blueprint&launch_context_id=project-destination');
    await until('Geometry preview');
    await click('Run'); await click('Vast · Fixture worker'); await click('Launch Shape Blueprint'); await until('Review remote execution plan');
    expect(posts[0].body.launch_context_id).toBe('project-destination'); expect(posts[0].header).toBe('project-destination');
    expect(posts[1].body.launch_context_id).toBe('project-destination'); expect(posts[1].header).toBe('project-destination');
    await click(approve ? 'Approve and submit' : 'Cancel');
    const requests = posts.filter(p => p.url === '/api/shape-blueprint/requests'); expect(requests).toHaveLength(approve ? 2 : 1);
    if (approve) expect(requests[1].body).toEqual({ ...requests[0].body, execution_plan_approval: 'd'.repeat(64) });
    else expect(document.body.textContent).toContain('Execution-plan approval cancelled');
    expect(posts.some(p => p.url === '/api/jobs')).toBe(false);
});
