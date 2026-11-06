// Focused receiving journeys ported from the read-only management audit.
// Transport is inert; serialized cold reopen is not a live database assertion.
import React, { act } from 'react';
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ project: null as any, models: [] as any[], inventory: {} as any, trace: [] as string[], save: vi.fn(), prepare: vi.fn(), reserve: vi.fn(), context: vi.fn(), submit: vi.fn(async (_request: any, _options?: any) => ({ data: { id: 'created-job' } })), library: null as any }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    submitJob: state.submit,
    fetchModelById: vi.fn(async (id: string) => ({ data: state.models.find(model => model.id === id) ?? roundCatalogs.find((model: any) => model.id === id) ?? { id, params: [], modes: [] } })),
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
    await vi.waitFor(async () => { await settle(); expect(document.body.textContent).not.toContain('Loading selected editor'); expect(document.body.textContent).not.toContain('Loading Project workflow setup'); });
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
it.each(['fampnn','proteinmpnn'])('audit Project parent retains exact %s round science in native draft envelope',async designer=>{
 const params=designer==='fampnn'?{fampnn_seed:19,fampnn_temperature:0.23,fampnn_psce_threshold:0,fampnn_fix_target_sidechains:true,seqs_per_design:3,fixed_positions:'a:10B,1:5,A:20-30'}:{mpnn_temperature:0.31,mpnn_relax_convergence_score:0,seqs_per_design:7,fixed_positions:'a:10B,1:5,A:20-30'};
 state.project.draft={model_id:'bindcraft2',mode:'campaign',denovo_generator:'bindcraft2',job_name:'MPNN project',bindcraft2_settings:{max_trajectories:3,trajectory_only:false},binder_round:{schema_version:1,enabled:true,binder_chains:['a'],target_chains:['1'],sequence_design:{model_id:designer,params},prediction:{model_id:'protenix',params:{}}}};
 await mount();await click('Preview native campaign');await click('Save draft');
 expect(state.save).toHaveBeenCalled();
 const first=structuredClone(state.project.draft);if(process.env.BMS_FRONTEND_EVIDENCE)writeFileSync(process.env.BMS_FRONTEND_EVIDENCE+'/project-'+designer+'.json',JSON.stringify(first,null,2));
 expect(first.binder_round.sequence_design.params).toMatchObject(params);
 expect(first.native_job_request).toBeUndefined(); // Native request is attached by Start run, not raw Save draft.
 await unmount();await mount();await click('Preview native campaign');await click('Save draft');
 expect(state.save).toHaveBeenCalled();
 expect(state.project.draft.binder_round).toEqual(first.binder_round);
 await click('Launch BindCraft2 campaign');
 expect(state.project.draft.native_job_request.binder_round.sequence_design.params).toMatchObject(params);
 if(process.env.BMS_FRONTEND_EVIDENCE)writeFileSync(process.env.BMS_FRONTEND_EVIDENCE+'/project-'+designer+'-native.json',JSON.stringify(state.project.draft,null,2));
 expect(state.submit.mock.calls.at(-1)![0].binder_round).toEqual(state.project.draft.native_job_request.binder_round);
});
