import React, { act } from 'react';
import { readFileSync, appendFileSync } from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
import { JobSubmission } from '../../src/components/JobSubmission';
import { fetchGeneralSequenceInventory } from '../../src/lib/generalSequenceDesign';

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

const field = (name: string, type: string, value: unknown) => ({ name, type, default: value });
const protein = [field('seqs_per_design', 'integer', 8), field('mpnn_backbone_noise', 'number', 0), field('mpnn_relax_output', 'boolean', false), field('mpnn_omitAAs', 'string', 'C'), field('input_pdb', 'file', null)];
const fa = [field('seqs_per_design', 'integer', 8), field('fampnn_temperature', 'number', 0.1), field('fampnn_seed', 'integer', 0), field('fampnn_psce_threshold', 'number', 0.3), field('fampnn_repack_last', 'boolean', true), field('fixed_positions', 'string', ''), field('design_chain', 'string', 'A'), field('input_pdb', 'file', null)];
const cal = [field('num_seqs_per_pdb', 'integer', 4), field('num_workers', 'integer', 8), field('verbose', 'boolean', true), field('omit_aas', 'array', ['C'])];
const catalogs: Record<string, any> = {
    proteinmpnn: { params: protein, modes: [{ id: 'design', params: protein.map(p => p.name) }] },
    fampnn: { params: [...fa, field('antibody_only_fixture', 'number', 42)], modes: [{ id: 'design', params: fa.map(p => p.name) }, { id: 'binder_design', params: ['antibody_only_fixture'] }] },
    caliby_experimental: { params: cal, modes: [{ id: 'ensemble_design', params: cal.map(p => p.name), parameter_schema: {
        discriminator: { propertyName: 'task', mapping: { ensemble_design: '#/$defs/EnsembleDesign' } }, oneOf: [{ $ref: '#/$defs/EnsembleDesign' }],
        $defs: { EnsembleDesign: { type: 'object', properties: { task: { const: 'ensemble_design' }, schema_version: { const: 1 }, ensembles: { type: 'array', items: { $ref: '#/$defs/Ensemble' } },
            ...Object.fromEntries(cal.map(p => [p.name, { type: p.type, default: p.default, ...(p.name === 'omit_aas' ? { items: { type: 'string', enum: ['A', 'C', 'D'] } } : {}) }])) } },
        Ensemble: { type: 'object', properties: { states: { type: 'array', items: { $ref: '#/$defs/Conformer' } } } },
        Conformer: { type: 'object', properties: { state_id: { type: 'string' }, path: { type: 'string' }, fixed_pos_seq: { type: 'string', default: '' }, symmetry_pos: { type: 'string', default: '' } } } },
    } }] },
};
const realInventories = process.env.BMS_GENERAL_SEQUENCE_INVENTORY ? JSON.parse(readFileSync(process.env.BMS_GENERAL_SEQUENCE_INVENTORY, 'utf8')) : null;
const response = (data: unknown): any => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const ready = { id: 'vast:fixture', provider: 'vast', provider_instance_id: 'fixture', name: 'Fixture worker', state: 'ready', active: true, host: 'fixture', port: 22, username: 'fixture', remote_root: '/fixture', host_key_sha256: 'c'.repeat(64), capabilities: {}, pricing: {}, last_error: null, last_seen_at: null, activated_at: null };
const originalAdapter = api.defaults.adapter;
let root: Root | undefined;
let client: QueryClient;
let posts: Array<{ url: string; body: any }>;
let saved: any[];
let useReal = false;
let failInventory = false;
beforeEach(() => {
    posts = []; saved = []; useReal = false; failInventory = false; project.value = null;
    api.defaults.adapter = async config => {
        const url = String(config.url);
        if (config.method === 'get') {
            const id = url.match(/^\/api\/models\/([^/]+)$/)?.[1];
            if (id && catalogs[id]) { if (failInventory) throw new Error('Fixture discovery unavailable'); return response(useReal ? realInventories[id] : catalogs[id]); }
            if (url === '/api/execution-targets') return response([ready]);
            if (url.includes('/system')) return response({ gpus: [], gpu_error: null });
            if (url.includes('user-templates')) return response(saved);
            if (url.endsWith('/runtime-inventory')) return response(null);
            return response([]);
        }
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        posts.push({ url, body });
        if (url.endsWith('/provision/preview')) return response({ selection: body, artifacts: [], total_bytes: 0, preview_sha256: 'b'.repeat(64), scientific_ready: false, scope: 'managed_asset_activation' });
        if (url === '/api/jobs') return response({ id: 'fixture-no-live-job', ...body });
        if (url.includes('user-templates')) { const item = { ...body, id: 'saved-fixture', created_at: '2026-01-01', updated_at: '2026-01-01' }; saved.push(item); return response(item); }
        throw new Error(`Unexpected POST ${url}`);
    };
});
afterEach(async () => {
    if (process.env.BMS_GENERAL_SEQUENCE_REQUEST_EVIDENCE) appendFileSync(process.env.BMS_GENERAL_SEQUENCE_REQUEST_EVIDENCE, JSON.stringify({ test: expect.getState().currentTestName, posts }) + '\n');
    if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); api.defaults.adapter = originalAdapter; vi.clearAllMocks();
});
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function mount(node: React.ReactNode, entry = '/submit?template=protein_modification_experimental') {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}>{node}</MemoryRouter></QueryClientProvider>)); await settle();
}
async function unmount() { await act(async () => root!.unmount()); root = undefined; client.clear(); document.body.replaceChildren(); }
function control(name: string): HTMLInputElement | HTMLSelectElement {
    const direct = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${name}"]`);
    const label = [...document.querySelectorAll('label')].find(el => [...el.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim() === name);
    const input = direct ?? label?.querySelector('input,select'); expect(input, name).toBeTruthy(); return input as ReturnType<typeof control>;
}
function reveal(input: Element) { let ancestor = input.parentElement; while (ancestor) { if (ancestor.tagName === 'DETAILS') (ancestor as HTMLDetailsElement).open = true; ancestor = ancestor.parentElement; } }
async function edit(name: string, value: string) { const input = control(name); await act(async () => { reveal(input); Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')!.set!.call(input, value); input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function toggle(name: string) { await act(async () => { reveal(control(name)); (control(name) as HTMLInputElement).click(); }); await settle(); }
async function click(text: string, within: ParentNode = document) { const button = [...within.querySelectorAll<HTMLButtonElement>('button')].find(e => e.textContent?.trim() === text && !e.closest('[hidden]')); expect(button, text).toBeTruthy(); await act(async () => button!.click()); await settle(); }
async function openSettings() { await click('Sequence Design'); }
const job = () => posts.filter(row => row.url === '/api/jobs').at(-1)!.body;
const active = { schema_version: 1, enabled: true, model_id: 'fampnn', params: { fampnn_seed: 0, fampnn_psce_threshold: null, fampnn_repack_last: false, fixed_positions: '' } };

it('keeps OFF compact and puts primary sampling ahead of collapsed native expert groups without dropping any settings', async () => {
    useReal = Boolean(realInventories);
    await mount(<ProteinModificationTemplate onBack={() => {}} />); await openSettings();
    const panel = document.querySelector('[aria-label="General generation sequence design"]')!;
    const retained = panel.querySelector<HTMLDetailsElement>('[data-general-retained-settings]')!;
    expect(retained.open).toBe(false);
    expect(control('Sequence designer').closest('details')).toBeNull(); expect(control('seqs_per_design').closest('details')).toBeNull();
    expect(retained.contains(control('fampnn_temperature'))).toBe(true);
    expect([...panel.querySelectorAll('details')].every(details => !details.open)).toBe(true);
    expect(panel.textContent).not.toContain('Range: unbounded – unbounded'); expect(panel.textContent).not.toContain('Native default: null');
    await toggle('Enable sequence design');
    expect(panel.querySelector('[data-general-retained-settings]')).toBeNull();
    expect(control('fampnn_temperature').closest('details')).toBeNull();
    expect(control('fampnn_seed').closest('details')?.open).toBe(false);
    expect(panel.querySelector('label[for="native-fampnn_seed"]')?.textContent).toBe('Seed');
    expect([...panel.querySelectorAll('[data-general-sequence-advanced] details')].every(details => !(details as HTMLDetailsElement).open)).toBe(true);
    if (useReal) {
        expect(control('fampnn_checkpoint_path').closest('details')?.querySelector('summary')?.textContent).toBe('Model and checkpoint');
        expect(control('fampnn_timestep_mode').closest('details')?.querySelector('summary')?.textContent).toBe('Denoising and backbone noise');
        expect(control('fampnn_mutation_top_n').closest('details')?.querySelector('summary')?.textContent).toBe('Mutation analysis');
        expect(control('fampnn_extra_config').closest('details')?.querySelector('summary')?.textContent).toBe('Native overrides');
    }
    await edit('fampnn_seed', '7'); await toggle('Enable sequence design');
    expect(panel.querySelector<HTMLDetailsElement>('[data-general-retained-settings]')!.open).toBe(false);
    await edit('Sequence designer', 'proteinmpnn'); await edit('Sequence designer', 'fampnn');
    expect(control('fampnn_seed').value).toBe('7');
    await click('Generate candidates'); expect(job()).not.toHaveProperty('sequence_design');
});

it.each(['rfd3', 'disco', 'laproteina'])('%s uses section navigation, defaults off and submits only active designer settings through the real helper', async generator => {
    await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={{ generator }} />);
    expect(control('Sequence designer').value).toBe('fampnn'); expect((control('Enable sequence design') as HTMLInputElement).checked).toBe(false);
    expect(control('Sequence designer').closest('[hidden]')).not.toBeNull();
    await click('Generate candidates'); expect(job()).not.toHaveProperty('sequence_design');
    await openSettings(); expect(control('Sequence designer').closest('[hidden]')).toBeNull();
    await toggle('Enable sequence design'); await edit('fampnn_seed', '0'); await edit('fampnn_psce_threshold', ''); await toggle('fampnn_repack_last');
    await click('Generate candidates');
    expect(job().sequence_design).toMatchObject(active);
    expect(job().params).not.toHaveProperty('sequence_design'); expect(job()).not.toHaveProperty('sequence_design_drafts');
    expect(job().sequence_design.params).not.toHaveProperty('input_pdb'); expect(job().sequence_design.params).not.toHaveProperty('design_chain'); expect(job().sequence_design.params).not.toHaveProperty('antibody_only_fixture');
    expect(job().params.generator).toBe(generator);
});

it('preserves populated per-engine values, generated-state constraints and template snapshots through switch/save/reopen', async () => {
    const draft = vi.fn(); const manager = vi.fn();
    await mount(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} onOpenTemplateManager={manager} initialValues={{ sequence_design: active }} />);
    await openSettings(); await edit('Sequence designer', 'proteinmpnn'); await edit('mpnn_omitAAs', ''); await edit('mpnn_backbone_noise', '0');
    await edit('Sequence designer', 'caliby_experimental'); await edit('num_workers', '0'); await toggle('verbose');
    await act(async () => document.querySelectorAll('details').forEach(e => { e.open = true; }));
    await edit('fixed_pos_seq', 'A:1'); await edit('symmetry_pos', ''); await click('Remove omit_aas[0]');
    expect(document.querySelector('[aria-label="state_id"]')).toBeNull(); expect(document.querySelector('[aria-label="path"]')).toBeNull();
    await edit('Sequence designer', 'fampnn'); expect(control('fampnn_psce_threshold').value).toBe(''); expect((control('fampnn_repack_last') as HTMLInputElement).checked).toBe(false);
    await edit('Sequence designer', 'caliby_experimental'); expect(control('fixed_pos_seq').value).toBe('A:1');
    await click('Template Manager');
    const snapshot = JSON.parse(JSON.stringify(manager.mock.lastCall![0].currentParams));
    expect(snapshot.sequence_design_drafts.proteinmpnn.params).toMatchObject({ mpnn_omitAAs: '', mpnn_backbone_noise: 0 });
    expect(snapshot.sequence_design_drafts.fampnn.params).toMatchObject(active.params);
    await unmount(); await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={snapshot} onDraftChange={draft} />);
    expect(draft.mock.lastCall![0].sequence_design_drafts).toEqual(snapshot.sequence_design_drafts);
    await openSettings(); expect(control('num_workers').value).toBe('0'); expect((control('verbose') as HTMLInputElement).checked).toBe(false);
    await click('Generate candidates'); expect(job().sequence_design).toMatchObject({ model_id: 'caliby_experimental', params: { num_workers: 0, verbose: false, omit_aas: [] }, input_settings: { fixed_pos_seq: 'A:1', symmetry_pos: '' } });
    expect(job().sequence_design.params).not.toHaveProperty('fampnn_seed');
});

it('stage edits reach the real placement/provisioning envelope independently of native generation params', async () => {
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, ready.id);
    await mount(<ProteinModificationTemplate onBack={() => {}} initialValues={{ sequence_design: active }} />);
    await openSettings(); await edit('seqs_per_design', '3');
    const panel = document.querySelector('[aria-label="Unsaved workflow provisioning"]')!;
    await click('Preview artifact downloads', panel);
    const first = posts.at(-1)!.body.workflow_request;
    expect(first.sequence_design.params.seqs_per_design).toBe(3); expect(first.execution_target_id).toBe(ready.id);
    await edit('seqs_per_design', '5'); await click('Preview artifact downloads', document.querySelector('[aria-label="Unsaved workflow provisioning"]')!);
    const second = posts.at(-1)!.body.workflow_request;
    expect(second.sequence_design.params.seqs_per_design).toBe(5); expect(second.params).toEqual(first.params);
    expect(second).not.toHaveProperty('sequence_design_drafts'); expect(posts.some(row => row.url === '/api/jobs')).toBe(false);
});

it('response.sequence_design survives the real JobSubmission clone and template modal round trip', async () => {
    localStorage.setItem('clonedJobData', JSON.stringify({ name: 'Cloned fixture', model_id: 'protein_modification_experimental', mode: 'de_novo_design', params: { generator: 'rfd3' }, sequence_design: active }));
    await mount(<JobSubmission />);
    await openSettings(); expect(control('fampnn_psce_threshold').value).toBe(''); expect((control('Enable sequence design') as HTMLInputElement).checked).toBe(true);
    await click('Template Manager');
    const input = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Saved sequence fixture'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template'); expect(saved[0].params.sequence_design).toMatchObject(active);
    expect(saved[0].params.sequence_design_drafts.fampnn.params).toMatchObject(active.params);
    await click('Load'); await openSettings(); await click('Generate candidates'); expect(job().sequence_design).toMatchObject(active);
});

it('Project hydration occurs before reporting and preserves all generator/designer drafts through Save draft and reopen', async () => {
    const sequence_design_drafts = { fampnn: { params: active.params }, proteinmpnn: { params: { mpnn_omitAAs: '', mpnn_relax_output: false, mpnn_backbone_noise: 0 } }, caliby_experimental: { params: { num_workers: 0, verbose: false, omit_aas: [] }, input_settings: { fixed_pos_seq: '', symmetry_pos: '' } } };
    const draft = { generator: 'rfd3', sequence_design: active, sequence_design_drafts, de_novo_drafts: { 'de_novo_design:disco:unconditional': { generator: 'disco', sequence_design: { ...active, model_id: 'proteinmpnn', params: sequence_design_drafts.proteinmpnn.params }, sequence_design_drafts } } };
    project.value = { project_id: 'project-fixture', setup_context_id: 'setup-fixture', generation: 1, draft, project_label: 'Fixture', experiment_label: 'Fixture', workflow_label: 'De Novo Design', state: 'open', return_uri: '/projects/project-fixture', field_errors: {} };
    project.save.mockImplementation(async (_p, _s, request) => { project.value = { ...project.value, generation: 2, draft: JSON.parse(JSON.stringify(request.draft)) }; return project.value; });
    const entry = '/submit?template=protein_modification_experimental&project_id=project-fixture&setup_context_id=setup-fixture';
    await mount(<JobSubmission />, entry); await openSettings(); await edit('Sequence designer', 'proteinmpnn'); await edit('mpnn_omitAAs', ''); await click('Save draft');
    expect(project.value.draft.sequence_design_drafts).toMatchObject(sequence_design_drafts);
    expect(project.value.draft.de_novo_drafts['de_novo_design:disco:unconditional'].sequence_design_drafts).toEqual(sequence_design_drafts);
    await unmount(); await mount(<JobSubmission />, entry); await openSettings(); expect(control('Sequence designer').value).toBe('proteinmpnn'); expect(control('mpnn_omitAAs').value).toBe('');
    await edit('Engine', 'disco'); await openSettings(); expect(control('Sequence designer').value).toBe('proteinmpnn');
    await edit('Sequence designer', 'caliby_experimental'); expect(control('num_workers').value).toBe('0');
    await click('Save draft'); expect(project.value.draft.sequence_design.input_settings).toEqual({ fixed_pos_seq: '', symmetry_pos: '' });
    expect(posts.some(row => row.url === '/api/jobs')).toBe(false);
});

it('failed discovery leaves saved native values and populated inactive drafts intact without adding a launch gate', async () => {
    failInventory = true; const draft = vi.fn();
    await mount(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={{ sequence_design: active, sequence_design_drafts: { proteinmpnn: { params: { mpnn_omitAAs: '' } } } }} />);
    expect(draft.mock.lastCall![0].sequence_design).toEqual(active); expect(draft.mock.lastCall![0].sequence_design_drafts.proteinmpnn.params.mpnn_omitAAs).toBe('');
    await click('Generate candidates'); expect(job().sequence_design).toEqual(active);
});

it.runIf(Boolean(realInventories))('actual API inventories expose every applicable native global and Conformer field through the mounted parent', async () => {
    useReal = true;
    await mount(<ProteinModificationTemplate onBack={() => {}} />); await openSettings();
    for (const id of ['proteinmpnn', 'fampnn', 'caliby_experimental'] as const) {
        await edit('Sequence designer', id); const inventory = await fetchGeneralSequenceInventory(id);
        const source = realInventories[id];
        const mode = source.modes.find((m: any) => m.id === (id === 'caliby_experimental' ? 'ensemble_design' : 'design'));
        const expectedFields = id === 'caliby_experimental'
            ? Object.keys(mode.parameter_schema.$defs.EnsembleDesign.properties).filter(name => !['ensembles', 'task', 'schema_version'].includes(name))
            : source.params.filter((p: any) => mode.params.includes(p.name) && !['input_pdb', 'design_chain', 'target_chain', 'binder_chains', 'target_chains'].includes(p.name)).map((p: any) => p.name);
        const actual = [...document.querySelectorAll('[data-sequence-designer-field]')].map(node => node.getAttribute('data-sequence-designer-field')).sort();
        expect(actual).toEqual(expectedFields.sort());
        expect([...document.querySelectorAll('[data-general-input-setting]')].map(node => node.getAttribute('data-general-input-setting')).sort()).toEqual(id === 'caliby_experimental' ? Object.keys(mode.parameter_schema.$defs.Conformer.properties).filter(name => !['path', 'state_id'].includes(name)).sort() : []);
        expect(document.body.textContent).not.toContain('does not advertise a typed editor');
        await toggle('Enable sequence design'); await click('Generate candidates');
        expect(job().sequence_design.params).toEqual(Object.fromEntries(inventory.parameters.filter(p => Object.hasOwn(p, 'default')).map(p => [p.name, p.default])));
        await toggle('Enable sequence design');
    }
});
