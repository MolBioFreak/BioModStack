import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ project: null as any, models: [] as any[], inventory: {} as any, trace: [] as string[], save: vi.fn(), prepare: vi.fn(), reserve: vi.fn(), context: vi.fn(), submit: vi.fn(async (_request: any, _options?: any) => ({ data: { id: 'created-job' } })), library: null as any }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    submitJob: state.submit,
    fetchModelById: vi.fn(async (id: string) => ({ data: roundCatalogs.find((model: any) => model.id === id) ?? { id, params: [] } })),
    fetchModels: vi.fn(async () => ({ data: state.models })), fetchModel: vi.fn(async (id: string) => ({ data: state.models.find(m => m.id === id) })),
    fetchTemplates: vi.fn(async () => ({ data: [] })), fetchTemplateById: vi.fn(async () => ({ data: null })),
    fetchInputPresets: vi.fn(async () => ({ data: [{ id: 'fixture', name: 'Fixture target', path: 'inputs/fixture.pdb', category: 'test' }] })),
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })), listCachedRcsbPdbs: vi.fn(async () => ({ data: { cached: [] } })),
    materializeStructureTarget: vi.fn(async (source: any) => source.path || `inputs/${source.file?.name}`),
    uploadFile: vi.fn(async (_folder: string, file: File) => ({ data: { path: `inputs/${file.name}` } })),
    completeCurrentLaunchContext: vi.fn(async (response: any) => response.return_uri ?? null),
}));
vi.mock('../../src/lib/projectManager', async original => ({ ...await original<typeof import('../../src/lib/projectManager')>(),
    getProjectWorkflowSetup: vi.fn(async () => structuredClone(state.project)), saveProjectWorkflowSetupDraft: state.save,
    prepareProjectWorkflowSetup: state.prepare, launchDomainRunGroup: state.reserve, getLaunchContext: state.context,
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: (props: any) => { state.library = props; return null; } }));
// The parent, native forms, source acquisition, parser, sequence and Project hook
// remain real. Only transport, unrelated integrations and the WebGL owner differ.
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: any) => <output data-viewer={props.structureDocumentId}>{props.structureData}</output> }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { api } from '../../src/lib/api';
import { submitBindCraft2Lifecycle } from '../../src/lib/bindcraft2Lifecycle';
const roundCatalogs = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON || 'python3', ['-c', "import json,yaml; from pathlib import Path; p=Path('../api/config/models'); print(json.dumps([yaml.safe_load((p/(x+'.yaml')).read_text()) for x in ['proteinmpnn','fampnn','caliby_binder','protenix','boltz2','esmfold2']]))"], { encoding: 'utf8' }));
const PDB = 'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C  \nEND\n';
const field = (native_key: string, type: string) => ({ native_key, observed_types: [type], has_native_default: false, native_default: null, status: 'typed' });
const bc2 = { fields: { targets: field('targets', 'array'), max_trajectories: field('max_trajectories', 'integer'), trajectory_only: field('trajectory_only', 'boolean'), losses: field('losses', 'object') }, presets: {}, paratope_conformations: [], registered_metrics: { filters: {}, losses: {} } };
const realActions = process.env.BMS_BC2_ACTION_INVENTORY ? JSON.parse(readFileSync(process.env.BMS_BC2_ACTION_INVENTORY, 'utf8')) : { rank: { properties: { top: { type: 'integer', default: 20 }, list: { type: 'boolean', default: false } } } };
const realInventory = process.env.BMS_NATIVE_GENERATION_INVENTORY ? JSON.parse(readFileSync(process.env.BMS_NATIVE_GENERATION_INVENTORY, 'utf8')) : null;
let root: Root | undefined; let client: QueryClient;
const button = (text: string) => [...document.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!;
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 35)); }); }
async function click(text: string) { expect(button(text), text).toBeTruthy(); await act(async () => button(text).click()); await settle(); }
async function edit(label: string, value: string) { const input = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!; expect(input, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }); await settle(); }
function Location() { return <output data-location>{useLocation().pathname}</output>; }
async function mount(route = '/submit?template=antibody_denovo&project_id=destination&setup_context_id=setup') {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><JobSubmission /><Location /></MemoryRouter></QueryClientProvider>)); await settle(); await settle();
}
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
beforeEach(() => {
    state.trace = [];
    state.project = { project_id: 'destination', setup_context_id: 'setup', generation: 1, draft: {}, project_label: 'Destination', experiment_label: 'Experiment', workflow_label: 'Binder', state: 'open', return_uri: '/projects/destination', field_errors: {}, global_experiment_id: 'global', domain_experiment_id: 'domain' };
    state.inventory = {};
    state.models = ['ppiflow', 'boltzgen'].map(id => ({ id, name: id, params: [], modes: (id === 'ppiflow' ? ['protein_binder', 'antibody_binder', 'nanobody_binder'] : ['protein_binder', 'peptide_binder', 'nanobody_binder']).map(mode => ({ id: mode, name: mode, params: ['target_pdb', 'dataset_seed', 'self_condition'] })) }));
    for (const model of state.models) for (const mode of model.modes) state.inventory[`${model.id}:${mode.id}`] = realInventory?.[`${model.id}:${mode.id}`] ?? { mode: mode.id, parameters: [{ name: 'target_pdb', type: 'file' }, { name: 'dataset_seed', type: 'integer', default: 123 }, { name: 'self_condition', type: 'boolean', default: true }] };
    state.save.mockImplementation(async (_project, _setup, request) => { state.trace.push('save'); expect(request.expected_generation).toBe(state.project.generation); state.project = { ...state.project, generation: state.project.generation + 1, draft: structuredClone(request.draft) }; return structuredClone(state.project); });
    state.prepare.mockImplementation(async (_project, _setup, generation) => { state.trace.push('prepare'); expect(generation).toBe(state.project.generation); return { ...state.project, preparation_id: 'prepared', launch_context_id: 'destination-child' }; });
    state.reserve.mockImplementation(async () => { state.trace.push('reserve'); return {}; });
    state.context.mockImplementation(async () => { state.trace.push('context'); return { launch_context_id: 'destination-child', pinned_scheduler: structuredClone(state.project.draft.native_job_request) }; });
    state.submit.mockImplementation(async () => { state.trace.push('submit'); return { data: { id: 'created-job' } }; });
    vi.spyOn(api, 'post').mockImplementation(async (url: string, body: any) => {
        if (url.endsWith('/bind')) { state.trace.push('bind'); return { data: { return_uri: '/projects/destination' } }; }
        if (url.endsWith('/campaign/preview')) return { data: { preview_digest: 'fixture-preview', requested_settings: body.params.bindcraft2_settings, effective_settings: body.params.bindcraft2_settings } };
        throw new Error(`Unexpected POST ${url}`);
    });
    vi.stubGlobal('fetch', vi.fn(async (input: any) => {
        const url = String(input); const match = /^\/api\/models\/(\w+)\/generation-settings\?mode=(\w+)$/.exec(url);
        if (match) return { ok: true, json: async () => state.inventory[`${match[1]}:${match[2]}`] };
        if (url.includes('/native-settings')) return { ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: { ...bc2, native_actions: realActions } }) };
        return { ok: true, text: async () => PDB, blob: async () => new Blob([PDB]), json: async () => ({}) };
    }));
    if (!Blob.prototype.text) Object.defineProperty(Blob.prototype, 'text', { configurable: true, value: function () { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
});
afterEach(async () => { await unmount(); localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); });
it.each([['ppiflow', 'protein_binder'], ['ppiflow', 'antibody_binder'], ['ppiflow', 'nanobody_binder'], ['boltzgen', 'protein_binder'], ['boltzgen', 'peptide_binder'], ['boltzgen', 'nanobody_binder']])('actual parent %s/%s saves, reopens and submits through Project preparation', async (model, mode) => {
    state.project.draft = { model_id: model, mode, native_generation_authoring: true, job_name: 'Project native', target_pdb: 'inputs/fixture.pdb', target_chain: 'A', antigen_chain: 'A', framework_pdb: 'inputs/framework.pdb', heavy_chain: 'H', light_chain: null, self_condition: false, dataset_seed: 0, target_source: { path: 'inputs/fixture.pdb', name: 'Imported source', jobId: 'foreign-source-job', document: { artifact_id: 'source-doc' } } };
    await mount();
    expect(document.querySelector('[aria-label="Target source"], [aria-label="Antigen source"]')).not.toBeNull();
    const designer = document.querySelector<HTMLSelectElement>('[aria-label="Round sequence designer"]')!;
    await act(async () => { designer.value = 'caliby_binder'; designer.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
    await edit('caliby_num_seqs_per_pdb', '11');
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="Automatic blind complex prediction"]')!.click()); await settle();
    await edit('Native binder job name', 'Edited in same editor'); await click('Save draft');
    const savedRound = structuredClone(state.project.draft.binder_round);
    const savedRoundDrafts = structuredClone(state.project.draft.binder_round_drafts);
    expect(savedRound).toMatchObject({ enabled: false, sequence_design: { model_id: 'caliby_binder', params: { caliby_num_seqs_per_pdb: 11 } } });
    expect(state.project.draft.job_name).toBe('Edited in same editor'); expect(state.project.draft.target_source.jobId).toBe('foreign-source-job');
    await unmount(); await mount();
    expect(document.querySelector<HTMLInputElement>('[aria-label="Native binder job name"]')!.value).toBe('Edited in same editor');
    expect(document.querySelector<HTMLInputElement>('[aria-label="caliby_num_seqs_per_pdb"]')!.value).toBe('11');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Automatic blind complex prediction"]')!.checked).toBe(false);
    state.trace = []; await click('Start run');
    expect(state.trace).toEqual(['save', 'prepare', 'reserve', 'context', 'submit', 'bind']);
    const request = state.submit.mock.calls.at(-1)![0];
    expect(request).toMatchObject({ name: 'Edited in same editor', model_id: model, mode, launch_context_id: 'destination-child' });
    expect(request.binder_round).toEqual(savedRound);
    expect(state.project.draft.binder_round_drafts).toEqual(savedRoundDrafts);
    expect(request.params).not.toHaveProperty('binder_round');
    expect(request.params).not.toHaveProperty('binder_round_drafts');
    expect(request.params).not.toHaveProperty('target_source'); expect(request.params).not.toHaveProperty('native_job_request');
    if (model === 'ppiflow' && mode === 'protein_binder') expect(request.params).toMatchObject({ self_condition: false, dataset_seed: 0 });
    expect(document.querySelector('[data-location]')!.textContent).toBe('/projects/destination');
});
it('direct native URL mounts the receiving mode without a clone or populated draft', async () => {
    await mount('/submit?model=ppiflow&mode=protein_binder');
    expect(document.body.textContent).toContain('PPIFlow initial generation'); expect(document.querySelector('[aria-label="Target source"]')).not.toBeNull();
});
it('BC2 parent Project save/reopen retains typed native values and launch uses the current native preview', async () => {
    const settings = { targets: [{ name: 'foreign source', target_path: 'inputs/fixture.pdb', chains: 'A', weight: 0 }], trajectory_only: false, max_trajectories: 3, losses: {} };
    state.project.draft = { model_id: 'bindcraft2', mode: 'campaign', denovo_generator: 'bindcraft2', job_name: 'BC2 project', bindcraft2_settings: settings };
    await mount(); expect(document.querySelector('[aria-label="BindCraft2 campaign"]')).not.toBeNull();
    await click('Campaign'); await edit('max_trajectories', '0'); await click('Save draft');
    expect(state.project.draft.bindcraft2_settings).toEqual({ ...settings, max_trajectories: 0 });
    await unmount(); await mount(); await click('Preview native campaign'); state.trace = []; await click('Launch BindCraft2 campaign');
    expect(state.trace).toEqual(['save', 'prepare', 'reserve', 'context', 'submit', 'bind']);
    expect(state.submit.mock.calls[0][0]).toMatchObject({ model_id: 'bindcraft2', mode: 'campaign', launch_context_id: 'destination-child', params: { bindcraft2_settings: { ...settings, max_trajectories: 0 }, bc2_preview_digest: 'fixture-preview' } });
});
it('RFantibody real parent preserves uploaded target and framework draft through reopen and Project submit', async () => {
    state.project.draft = { denovo_generator: 'rfantibody', job_name: 'RF Project', framework_type: 'custom', custom_framework_path: 'inputs/framework.pdb', custom_framework_source: { type: 'preset', name: 'Saved framework', path: 'inputs/framework.pdb', document: { artifact_id: 'framework-doc' } }, run_structure_validation: false, run_frustrampnn: false };
    await mount();
    const input = document.querySelector<HTMLInputElement>('input[type="file"][accept*=".pdb"]')!;
    expect(input).toBeTruthy();
    await act(async () => { Object.defineProperty(input, 'files', { configurable: true, value: [new File([PDB], 'retained-target.pdb')] }); input.dispatchEvent(new Event('change', { bubbles: true })); });
    await settle(); await settle();
    const residue = document.querySelector<HTMLButtonElement>('button[title="A1 (ALA)"]')!;
    expect(residue).toBeTruthy(); await act(async () => residue.click()); await settle();
    await click('Save draft');
    expect(state.project.draft.target_pdb).toBe('inputs/retained-target.pdb');
    expect(state.project.draft.custom_framework_source.document.artifact_id).toBe('framework-doc');
    await unmount(); await mount();
    expect(document.querySelector('button[title="A1 (ALA)"]')).not.toBeNull();
    state.trace = []; const launch = [...document.querySelectorAll('button')].find(b => b.textContent?.includes('Launch RFantibody Batch'))!;
    expect(launch).toBeTruthy(); expect(launch.disabled).toBe(false); await act(async () => launch.click()); await settle();
    expect(state.trace).toEqual(['save', 'prepare', 'reserve', 'context', 'submit', 'bind']);
    expect(state.submit.mock.calls[0][0]).toMatchObject({ launch_context_id: 'destination-child', params: { target_pdb: 'inputs/retained-target.pdb', framework_pdb: 'inputs/framework.pdb' } });
});

it('real parent four-generator round trip preserves native drafts and independent source context', async () => {
    state.project.draft = { denovo_generator: 'rfantibody', job_name: 'original RF', target_pdb: 'inputs/fixture.pdb', target_source: { type: 'preset', name: 'Original', path: 'inputs/fixture.pdb' }, selected_chain: 'A', selected_residues: ['A1'] };
    await mount();
    await click('PPIFlow · nanobody generation');
    await edit('Native binder job name', 'PPI retained');
    const designer = document.querySelector<HTMLSelectElement>('[aria-label="Round sequence designer"]')!;
    await act(async () => { designer.value = 'caliby_binder'; designer.dispatchEvent(new Event('change', { bubbles: true })); }); await settle();
    await edit('caliby_num_seqs_per_pdb', '9');
    await click('Change generation engine');
    await click('BindCraft2 campaign'); await click('Campaign'); await edit('max_trajectories', '0');
    await click('RFantibody Stack');
    await click('BoltzGen');
    await edit('Native binder job name', 'Boltz retained');
    await click('Change generation engine'); await click('PPIFlow · nanobody generation');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Native binder job name"]')!.value).toBe('PPI retained');
    expect(document.querySelector<HTMLInputElement>('[aria-label="caliby_num_seqs_per_pdb"]')!.value).toBe('9');
    await click('Save draft');
    expect(state.project.draft.binder_native_drafts['boltzgen:nanobody_binder'].job_name).toBe('Boltz retained');
    expect(state.project.draft.binder_native_drafts['ppiflow:nanobody_binder'].binder_round.sequence_design.params.caliby_num_seqs_per_pdb).toBe(9);
    expect(state.project.draft.binder_workflow_draft.bindcraft2_settings.max_trajectories).toBe(0);
    await unmount(); await mount(); await click('Change generation engine'); await click('BindCraft2 campaign');
    expect(document.querySelector<HTMLInputElement>('[aria-label="max_trajectories"]')!.value).toBe('0');
});

it.each(Object.entries(realActions))('BC2 %s receiving draft uses native action contract through Project save/reopen/submit', async (operation, rawDescriptor) => {
    const descriptor = rawDescriptor as any;
    const options = Object.fromEntries(Object.entries(descriptor.properties).filter(([, field]: any) => Object.hasOwn(field, 'default')).map(([key, field]: any) => [key, field.default]));
    if (operation === 'rank' || operation === 'filter') options.top = 0;
    if (operation === 'score') options.structure_relative_path = 'results/source.cif';
    state.project.draft = { model_id: 'bindcraft2', mode: operation, bc2_source_job_id: 'foreign-campaign', bc2_action_options: options, job_name: `Saved ${operation}` };
    await mount();
    expect(document.querySelector('[aria-label="BindCraft2 lifecycle draft"]')).not.toBeNull();
    expect(document.querySelector('[aria-label="BindCraft2 campaign"]')).toBeNull();
    for (const [key, field] of Object.entries(descriptor.properties) as any[]) {
        if (field.type === 'array') expect(button(`Add ${key}`)).toBeTruthy();
        else expect(document.querySelector(`[aria-label="${key}"]`), key).not.toBeNull();
    }
    await edit('Native action name', `Edited ${operation}`); await click('Save draft');
    expect(state.project.draft.bc2_action_options).toEqual(options);
    await unmount(); await mount(); state.trace = []; await click('Run native operation');
    expect(state.trace).toEqual(['save', 'prepare', 'reserve', 'context', 'submit', 'bind']);
    expect(state.submit.mock.calls[0][0]).toMatchObject({ name: `Edited ${operation}`, model_id: 'bindcraft2', mode: operation, launch_context_id: 'destination-child', params: { bc2_source_job_id: 'foreign-campaign', bc2_action_options: options } });
});

it('failed Project save stays in the editor and exposes the server error without dropping edits', async () => {
    state.project.draft = { model_id: 'ppiflow', mode: 'protein_binder', job_name: 'unsaved' };
    state.save.mockRejectedValueOnce(new Error('generation conflict'));
    await mount(); await click('Save draft');
    expect(document.querySelector('[role="alert"]')!.textContent).toContain('generation conflict');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Native binder job name"]')!.value).toBe('unsaved');
    expect(state.prepare).not.toHaveBeenCalled();
});
it('BC2 lifecycle receives explicit destination and retains server-prepared child context during remote review', async () => {
    const prepared = { name: 'native action', model_id: 'bindcraft2', mode: 'rank', params: { bc2_source_job_id: 'foreign-source', bc2_action_options: { reverse: false, limit: 0 } }, execution_target_id: 'remote', launch_context_id: 'prepared-child' };
    vi.mocked(api.post).mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { detail: { code: 'remote_prepared_job_review_required', job_request: prepared } } } });
    await submitBindCraft2Lifecycle('foreign-source', 'rank', { reverse: false, limit: 0 }, 'remote', 'destination');
    expect(api.post).toHaveBeenCalledWith('/api/jobs', expect.objectContaining({ launch_context_id: 'destination', params: prepared.params }), undefined);
    expect(state.submit).toHaveBeenCalledWith(prepared, { launchContext: true });
});
