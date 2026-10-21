import React, { act } from 'react';
import { execFileSync } from 'node:child_process';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ models: [] as any[], templates: [] as any[], requests: [] as any[], inventories: {} as any }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    fetchModels: async () => ({ data: state.models }),
    fetchModelById: async (id: string) => ({ data: state.models.find(m => m.id === id) ?? { id, params: [], modes: [] } }),
    fetchTemplates: async () => ({ data: [] }), fetchTemplateById: async () => ({ data: null }),
    fetchInputPresets: async () => ({ data: [] }), fetchExecutionTargets: async () => ({ data: [] }),
    listCachedRcsbPdbs: async () => ({ data: { cached: [] } }),
    completeCurrentLaunchContext: async () => null,
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: any) => <output data-viewer>{props.structureData}</output> }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => null }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { binderAuthoringKey, readBinderAuthoringSession } from '../../src/lib/binderAuthoringSession';
const catalogs = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON || 'python3', ['-c', "import json,yaml; from pathlib import Path; p=Path('../api/config/models'); print(json.dumps([yaml.safe_load((p/(x+'.yaml')).read_text()) for x in ['ppiflow','boltzgen','proteinmpnn','fampnn','caliby_binder','protenix','boltz2','esmfold2']]))"], { encoding: 'utf8' }));
const inventories = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON || 'python3', ['-c', "import sys,json;sys.path.insert(0,'../api');from services.ppiflow_generation import ppiflow_generation_inventory;from services.boltzgen_request_compatibility import boltzgen_generation_inventory; print(json.dumps({**{'ppiflow:'+m:ppiflow_generation_inventory(m) for m in ['protein_binder','antibody_binder','nanobody_binder']},**{'boltzgen:'+m:boltzgen_generation_inventory(m) for m in ['protein_binder','peptide_binder','nanobody_binder']}}))"], { encoding: 'utf8' }));
let root: Root | undefined; let client: QueryClient; const originalAdapter = api.defaults.adapter;
const pdb = 'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C  \nEND\n';
const visible = (node: Element) => !node.closest('[hidden]');
const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(node => visible(node) && node.textContent?.trim() === text);
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); }
async function click(text: string) { expect(button(text), text).toBeTruthy(); await act(async () => button(text)!.click()); await settle(); }
async function edit(label: string, value: string) { const node = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!; expect(node, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); await settle(); }
function Location() { const location = useLocation(); return <output data-location>{location.pathname + location.search}</output>; }
async function mount(route: string) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><JobSubmission /><Location /></MemoryRouter></QueryClientProvider>));
    await settle(); await settle();
}
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
const saved = () => readBinderAuthoringSession(sessionStorage, binderAuthoringKey(document.querySelector('[data-location]')!.textContent!.split('?')[1]))!;
beforeEach(() => {
    if (!Blob.prototype.text) Object.defineProperty(Blob.prototype, 'text', { configurable: true, value: function () { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    state.models = structuredClone(catalogs); state.inventories = structuredClone(inventories); state.templates = []; state.requests = [];
    api.defaults.adapter = async config => {
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        state.requests.push({ method: config.method, url: config.url, body });
        let data: any;
        if (config.url === '/api/user-templates' && config.method === 'get') data = state.templates;
        else if (config.url === '/api/user-templates' && config.method === 'post') { data = { ...body, id: 'saved-template' }; state.templates.push(JSON.parse(JSON.stringify(data))); }
        else if (config.url === '/api/jobs' && config.method === 'post') data = { ...body, id: 'offline-created' };
        else if (config.url?.endsWith('/campaign/preview')) data = { preview_digest: 'offline-preview', requested_settings: body.params.bindcraft2_settings, effective_settings: body.params.bindcraft2_settings };
        else throw new Error(`Unexpected offline Axios transport: ${config.method} ${config.url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    vi.stubGlobal('fetch', vi.fn(async input => {
        const url = String(input); const match = /\/api\/models\/(\w+)\/generation-settings\?mode=(\w+)/.exec(url);
        if (match) return { ok: true, json: async () => state.inventories[`${match[1]}:${match[2]}`] };
        if (url.includes('/native-settings')) return { ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: { fields: {}, presets: {}, registered_metrics: { filters: {}, losses: {} }, paratope_conformations: [] } }) };
        if (url.startsWith('/api/files/download/')) return { ok: true, text: async () => pdb };
        throw new Error(`Unexpected offline fetch: ${url}`);
    }));
});
afterEach(async () => { await unmount(); api.defaults.adapter = originalAdapter; sessionStorage.clear(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(['bindcraft2', 'ppiflow', 'boltzgen'])('generation-only %s Clone remains OFF through real parent receiving boundary', async model => {
    const mode = model === 'bindcraft2' ? 'campaign' : 'protein_binder';
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: model, mode, name: 'Only generation', binder_round: null, execution_target_id: null, params: model === 'bindcraft2' ? { bindcraft2_settings: { targets: [], max_trajectories: 0 } } : { target_pdb: '', input_csv: 'inputs/data.csv', dataset_seed: 0, native_generation_authoring: true } }));
    await mount('/submit?template=antibody_denovo');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector<HTMLInputElement>('[aria-label="Automatic blind complex prediction"]')?.checked).toBe(false); });
    if (model === 'bindcraft2') { await click('Preview native campaign'); await click('Launch BindCraft2 campaign'); }
    else { const launch = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.includes('Launch') && visible(b)); expect(launch).toBeTruthy(); await act(async () => launch!.click()); await settle(); }
    const request = state.requests.find(r => r.url === '/api/jobs')?.body;
    expect(request).toBeDefined(); expect(request.binder_round.enabled).toBe(false); expect(request.execution_target_id).toBeNull(); expect(request.params).not.toHaveProperty('binder_round');
});

it('native unsaved cold reopen restores scientific draft and Local together', async () => {
    await mount('/submit?model=ppiflow&mode=protein_binder');
    await edit('Native binder job name', 'unsaved exact'); await edit('dataset_seed', '0'); await edit('target_pdb', 'inputs/cleared.pdb'); await edit('target_pdb', '');
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="Automatic blind complex prediction"]')!.click()); await settle();
    const retained = JSON.parse(JSON.stringify(saved())); expect(retained).toMatchObject({ job_name: 'unsaved exact', dataset_seed: 0, target_pdb: '', execution_target_id: null, binder_round: { enabled: false } });
    await unmount(); sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'unrelated-worker'); await mount('/submit?model=ppiflow&mode=protein_binder');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Native binder job name"]')!.value).toBe('unsaved exact');
    expect(document.querySelector<HTMLInputElement>('[aria-label="dataset_seed"]')!.value).toBe('0'); expect(sessionStorage.getItem(EXECUTION_TARGET_STORAGE_KEY)).toBeNull();
    expect(saved().binder_round).toEqual(retained.binder_round);
});

it('real library Save serializes native drafts; explicit Load replaces cross-engine name, source and clears', async () => {
    await mount('/submit?model=ppiflow&mode=protein_binder'); await edit('Native binder job name', 'saved PPI'); await edit('target_pdb', 'inputs/cleared.pdb'); await edit('target_pdb', ''); await edit('dataset_seed', '0');
    await click('Template Manager');
    const name = document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Protein Design Setup"]') ?? [...document.querySelectorAll<HTMLInputElement>('input')].find(i => i.placeholder.includes('e.g.'))!;
    expect(name).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'PPI saved exact'); name.dispatchEvent(new Event('input', { bubbles: true })); }); await settle();
    await click('Save Template'); expect(state.templates[0].params).toMatchObject({ job_name: 'saved PPI', dataset_seed: 0, target_pdb: '', execution_target_id: null });
    const close = document.querySelector<HTMLButtonElement>('button[title="Close"]') ?? document.querySelector<HTMLButtonElement>('button[aria-label="Close"]');
    if (close) { await act(async () => close.click()); await settle(); } else await click('Close');
    await click('Change generation engine'); await click('BoltzGen'); await edit('Native binder job name', 'stale Boltz'); await edit('boltzgen_target_pdb_path', 'inputs/stale.pdb');
    await click('Template Manager'); await click('Cancel');
    const all = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find(i => i.parentElement?.textContent?.includes('All workflows'))!; await act(async () => all.click()); await settle(); await click('Load');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector<HTMLInputElement>('[aria-label="Native binder job name"]')?.value).toBe('saved PPI'); });
    expect(document.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!.value).toBe(''); expect(saved().dataset_seed).toBe(0); expect(saved().binder_native_drafts).not.toHaveProperty('boltzgen:nanobody_binder');
});

it('explicit legacy partial-flow Load replaces the retained RF draft including empty hotspot selection', async () => {
    state.templates = [{ id: 'legacy', name: 'Saved partial flow', model_id: 'template_antibody_denovo', mode: 'antibody_denovo', base_template_id: 'antibody_denovo', params: { denovo_generator: 'ppiflow', job_name: 'test', selected_residues: [], selected_chain: '', target_pdb: null, target_source: null, binder_round: null } }];
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: 'template_antibody_denovo', mode: 'antibody_denovo', name: 'old RF', params: { denovo_generator: 'rfantibody', target_pdb: 'inputs/old.pdb', selected_chain: 'A', selected_residues: ['A1'] } }));
    await mount('/submit?template=antibody_denovo'); await click('BoltzGen'); await click('Template Manager'); await click('Cancel');
    const all = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find(i => i.parentElement?.textContent?.includes('All workflows'))!;
    await act(async () => all.click()); await settle(); await click('Load');
    await vi.waitFor(async () => { await settle(); expect(saved()?.denovo_generator).toBe('ppiflow'); });
    expect(saved().job_name).toBe('test'); expect(saved().selected_residues).toEqual([]); expect(saved().selected_chain).toBe(''); expect(saved().target_source).toBeFalsy();
    expect(saved().binder_round.enabled).toBe(false);
});

it.each(['ligand_binder', 'ntp_binder', 'scaffold_around_ligand', 'backbone_docking'])('advertised BoltzGen %s retains native source/editor shell', async mode => {
    await mount(`/submit?model=boltzgen&mode=${mode}`);
    expect(document.body.textContent).toContain('BoltzGen generation'); expect(document.querySelector('[aria-label="Native binder execution settings"]')).not.toBeNull();
    const model = catalogs.find((m: any) => m.id === 'boltzgen'); const advertised = model.modes.find((m: any) => m.id === mode);
    for (const key of advertised.params) expect(document.querySelector(`[data-native-setting="${key}"]`), key).not.toBeNull();
    expect(document.querySelector('[aria-label="Automatic blind complex prediction"]')).toBeNull();
});
