import React, { act, useState, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { JobSubmission } from '../../src/components/JobSubmission';
import { NativeBinderSource } from '../../src/components/NativeBinderSource';
import { TemplateManagerModal } from '../../src/components/TemplateManagerModal';
import { BindCraft2Campaign } from '../../src/components/BindCraft2Campaign';
import { BindCraft2StructureInputs } from '../../src/components/BindCraft2StructureInputs';
import type { BC2Source } from '../../src/lib/bindcraft2StructureInputs';
import { QualitySettingsPanel } from '../../src/components/QualitySettingsPanel';
import { PRESETS } from '../../src/components/qualitySettingsLogic';

const renderer = vi.hoisted(() => ({ mounts: 0 }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: any) => {
    useEffect(() => { renderer.mounts++; }, []);
    return <output data-viewer data-format={props.format} data-document={props.structureDocumentId}>{props.structureData}</output>;
} }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
const PDB = 'ATOM      1  CA  ALA a  42B      7.000   2.000   3.000  1.00 20.00           C  \nEND\n';
const CIF = `data_fixture\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n_atom_site.label_atom_id\n_atom_site.label_comp_id\n_atom_site.label_asym_id\n_atom_site.label_seq_id\n_atom_site.auth_asym_id\n_atom_site.auth_seq_id\n_atom_site.auth_comp_id\n_atom_site.pdbx_PDB_ins_code\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n_atom_site.pdbx_PDB_model_num\nATOM 1 C CA ALA X 1 a 42 ALA B 7 2 3 7\n#\n`;
const materialization = { path: 'inputs/derived.pdb', format: 'pdb', sha256: 'derived-sha', native_path: 'inputs/native.cif', native_format: 'cif', native_sha256: 'native-sha', model_numbers: [7], model_number: 7, author_residues: [{ model_number: 7, auth_asym_id: 'a', auth_seq_id: 42, insertion_code: 'B', residue_name: 'ALA' }], source_identity: {}, source_path: 'inputs/native.cif' };
const field = (key: string, type: string) => ({ native_key: key, observed_types: [type], has_native_default: false, native_default: null, status: 'typed' });
const bc2 = { fields: { targets: field('targets', 'array'), max_trajectories: field('max_trajectories', 'integer'), trajectory_only: field('trajectory_only', 'boolean') }, presets: {}, paratope_conformations: [], registered_metrics: { filters: {}, losses: {} }, native_actions: { rank: { properties: { top: { type: 'integer', default: 20 }, list: { type: 'boolean', default: false } } } } };
const nativeParameters = [{ name: 'target_pdb', type: 'file' }, { name: 'framework_pdb', type: 'file' }, { name: 'scaffold_path', type: 'file' }, { name: 'target_chain', type: 'string' }, { name: 'antigen_chain', type: 'string' }, { name: 'heavy_chain', type: 'string' }, { name: 'light_chain', type: 'string', nullable: true }, { name: 'specified_hotspots', type: 'string' }, { name: 'dataset_seed', type: 'integer', default: 123 }, { name: 'self_condition', type: 'boolean', default: true }];
let root: Root | undefined, client: QueryClient;
let rows: any[], calls: Array<{ method: string; url: string; body: any; headers: any }>;
let draft: any, project: any, sourceState: any;
let oldAdapter: any;
const reference = { name: 'Exact alternate model', path: 'inputs/native.cif', modelNumber: 7, jobId: 'producer', designId: 'design', document: { artifact_id: 'alt-doc', sha256: 'native-sha', target_state: 'alternate' }, materialization: { ...materialization, path: 'inputs/native.cif', format: 'cif' } };
const handoff = { target: { path: 'inputs/native.cif', reference, modelNumber: 7 }, framework: { path: 'inputs/framework.pdb' }, bc2: { settings: { targets: [{ target_path: 'inputs/native.cif' }], binder_scaffold: 'inputs/framework.pdb' }, references: { 'target:0': { path: 'inputs/native.cif', source: reference }, scaffold: { path: 'inputs/framework.pdb', source: { name: 'Framework', path: 'inputs/framework.pdb', document: { artifact_id: 'framework-doc' } } } } } };
function Location() { const loc = useLocation(); return <output data-location>{loc.pathname + loc.search}</output>; }
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); }
async function ready(predicate: () => boolean) { for (let i = 0; i < 120; i++) { await settle(); if (predicate()) return; } throw new Error(document.body.textContent || 'UI did not settle'); }
const button = (label: string) => [...document.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent?.trim() === label)!;
async function click(label: string) { expect(button(label), label).toBeTruthy(); await act(async () => button(label).click()); await settle(); }
async function edit(selector: string, value: string) { const input = document.querySelector<HTMLInputElement>(selector)!; expect(input, selector).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }); await settle(); }
async function select(label: string, value: string) { const input = document.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!; expect(input).toBeTruthy(); await act(async () => { input.value = value; input.dispatchEvent(new Event('change', { bubbles: true })); }); await settle(); }
async function mount(entry = '/submit', content: React.ReactNode = <JobSubmission />) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}>{content}<Location /></MemoryRouter></QueryClientProvider>));
    await ready(() => !document.body.textContent?.includes('Loading selected editor') && !document.body.textContent?.includes('Loading Project workflow setup'));
    await settle();
}
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
beforeEach(() => {
    rows = []; calls = []; renderer.mounts = 0; draft = undefined; project = undefined;
    oldAdapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
        const url = config.url!, method = config.method || 'get', body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        calls.push({ method, url, body, headers: config.headers.toJSON() });
        let data: any = [];
        if (url === '/api/user-templates') { if (method === 'post') { data = { ...body, id: `saved-${rows.length}` }; rows.push(data); } else data = rows; }
        else if (url === '/api/models') data = [];
        else if (url.startsWith('/api/models/')) { const id = url.split('/').at(-1)!; data = { id, name: id, params: [], modes: ['protein_binder', 'antibody_binder', 'nanobody_binder', 'peptide_binder', 'predict', 'sequence_design'].map(mode => ({ id: mode, name: mode, params: nativeParameters.map(p => p.name) })) }; }
        else if (url.includes('/workflow-setup/') || url.includes('/workflow-setups/')) { if (body?.draft) project = { ...project, draft: body.draft, generation: project.generation + 1 }; data = project; }
        else if (url === '/api/jobs') data = { id: 'fixture-created-job' };
        else if (url === '/api/files/materialize-structure') data = materialization;
        else if (url === '/api/files/upload') data = { path: `inputs/upload-${calls.filter(call => call.url === url).length}.pdb` };
        else if (url.includes('/campaign/preview')) data = { preview_digest: 'fixture-preview', requested_settings: body.params.bindcraft2_settings, effective_settings: body.params.bindcraft2_settings };
        else if (url === '/api/rcsb') data = { cached: [] };
        else if (url.includes('/templates/')) data = { user_params: [], preset_params: {}, stages: [] };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    vi.stubGlobal('fetch', vi.fn(async (input: any) => {
        const url = String(input);
        if (url.includes('/native-settings')) return { ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: bc2 }) };
        if (url.includes('/generation-settings')) return { ok: true, json: async () => ({ mode: new URL(url, 'http://fixture').searchParams.get('mode'), parameters: nativeParameters }) };
        return { ok: true, text: async () => url.includes('native.cif') ? CIF : url.includes('6aru.pdb') ? PDB.replace('ALA a', 'ALA A') : PDB, blob: async () => new Blob([PDB]), json: async () => ({}) };
    }));
    if (!Blob.prototype.text) Object.defineProperty(Blob.prototype, 'text', { configurable: true, value: function () { return new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(this); }); } });
});
afterEach(async () => { await unmount(); api.defaults.adapter = oldAdapter; localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('Project transport save/unmount/reopen retains native provenance, explicit clears and OFF round without chooser reset', async () => {
    project = { schema: 'bms.project-workflow-setup.detail.v1', project_id: 'P', setup_context_id: 'S', generation: 1,
        global_experiment_id: 'G', domain_experiment_id: 'D', relationship_kind: 'primary', capability_id: 'binder', state: 'open', validation_state: 'incomplete',
        setup_destination: '/submit?project_id=P&setup_context_id=S', return_uri: '/projects/P', project_label: 'Project', experiment_label: 'Experiment', workflow_label: 'Binder', field_errors: {}, diagnostics: {},
        draft: { model_id: 'ppiflow', mode: 'protein_binder', job_name: 'Project source', native_generation_authoring: true, target_pdb: '', target_pdb_source_reference: reference,
            binder_source_handoff: handoff, binder_round: { schema_version: 1, enabled: false, sequence_design: { model_id: 'fampnn', params: {} }, prediction: { model_id: 'protenix', params: {} }, binder_chains: [], target_chains: [] } } };
    await mount('/submit?template=antibody_denovo&project_id=P&setup_context_id=S');
    await ready(() => !!document.querySelector('[aria-label="Native binder job name"]'));
    expect(document.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!.value).toBe('');
    await click('Save draft'); await ready(() => calls.some(call => call.method === 'put'));
    expect(project.draft.binder_source_handoff).toEqual(handoff); expect(project.draft.binder_round.enabled).toBe(false);
    expect(project.draft.target_pdb).toBe('');
    const saved = JSON.stringify(project.draft);
    await unmount(); await mount('/submit?template=antibody_denovo&project_id=P&setup_context_id=S');
    await ready(() => !!document.querySelector('[aria-label="Native binder job name"]'));
    expect(document.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!.value).toBe('');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Native binder job name"]')!.value).toBe('Project source');
    expect(JSON.stringify(project.draft)).toBe(saved);
    expect(calls.some(call => call.url === '/api/files/materialize-structure')).toBe(false);
});
it('fresh binder card opens the existing chooser without resetting a saved clone selection', async () => {
    await mount('/submit?extra=keep');
    const card = [...document.querySelectorAll<HTMLElement>('h3')].find(node => node.textContent?.includes('De Novo Binder Design'))?.closest<HTMLElement>('.cursor-pointer')!;
    await act(async () => card.click()); await ready(() => !!document.querySelector('[aria-label="Binder modality and generation engine"]'));
    const details = [...document.querySelectorAll('summary')].find(node => node.textContent === 'Change generation engine')!.parentElement as HTMLDetailsElement;
    expect(details.open).toBe(true); expect(document.body.textContent).toContain('BindCraft2 campaign'); expect(document.body.textContent).toContain('PPIFlow'); expect(document.body.textContent).toContain('BoltzGen');
});
it.each(['ppiflow', 'boltzgen'])('actual BC2 to %s receiving parent saves/reopens selected source envelope outside native transport', async model => {
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: 'bindcraft2', mode: 'campaign', name: 'Handoff', params: {
        bindcraft2_settings: { targets: [{ name: 'alternate', target_path: 'inputs/native.cif' }], binder_scaffold: 'inputs/framework.pdb', max_trajectories: 3 },
        bc2_source_references: handoff.bc2.references,
    } }));
    await mount('/submit?extra=keep'); await ready(() => !!document.querySelector('[aria-label="BindCraft2 campaign"]'));
    await click(model === 'ppiflow' ? 'PPIFlow · protein binder generation' : 'BoltzGen · protein binder');
    await ready(() => !!document.querySelector('[aria-label="Choose destination sources"]'));
    const checkbox = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find(node => node.parentElement?.textContent?.includes('Also reuse'))!;
    await act(async () => checkbox.click());
    await click('Use BC2 target 1: alternate');
    await ready(() => !!document.querySelector('[aria-label="Native binder job name"]') && !!document.querySelector('[data-viewer]'));
    if (model === 'ppiflow') await ready(() => document.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!.value === 'inputs/derived.pdb');
    const source = document.querySelector('[aria-label="Target source"]')!;
    const scene = source.querySelector('[data-viewer]');
    const sourceButton = (text: string) => [...source.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent === text)!;
    await act(async () => sourceButton('Open full viewer').click()); await act(async () => sourceButton('Sequence').click());
    expect(source.querySelector('[data-viewer]')).toBe(scene);
    await click('Generation'); await click(model === 'ppiflow' ? 'Targets & templates' : 'Sources'); expect(source.querySelector('[data-viewer]')).toBe(scene);
    await click('Template Manager'); await edit('input[placeholder="e.g., My Boltz Config"]', 'Saved handoff'); await click('Save Template'); await ready(() => rows.length === 1);
    const saved = JSON.parse(JSON.stringify(rows[0]));
    expect(saved.params.binder_source_handoff.bc2.references).toEqual(handoff.bc2.references);
    expect(saved.params.binder_source_handoff.bc2.settings).toEqual({ targets: [{ name: 'alternate', target_path: 'inputs/native.cif' }], binder_scaffold: 'inputs/framework.pdb', max_trajectories: 3 });
    expect(saved.params.target_pdb_source_reference.document).toEqual(reference.document);
    expect(saved.params).not.toHaveProperty('specified_hotspots'); expect(saved.params).not.toHaveProperty('target_chain');
    if (model === 'boltzgen') expect(saved.params.scaffold_path).toBe('inputs/framework.pdb');
    await unmount(); localStorage.setItem('clonedJobData', JSON.stringify({ model_id: model, mode: 'protein_binder', name: 'Saved handoff', params: saved.params }));
    await mount('/submit'); await ready(() => !!document.querySelector('[data-viewer]'));
    const reopenedSource = document.querySelector('[aria-label="Target source"]')!;
    expect([...reopenedSource.querySelectorAll('button')].find(node => node.textContent === 'Sequence')?.getAttribute('aria-pressed')).toBe('true');
    expect(reopenedSource.textContent).toContain('Close full viewer');
    await click('Launch Experiment'); await ready(() => calls.some(call => call.url === '/api/jobs'));
    const request = calls.find(call => call.url === '/api/jobs')!.body;
    expect(request.params).not.toHaveProperty('binder_source_handoff'); expect(request.params).not.toHaveProperty('target_pdb_source_reference'); expect(request.params).not.toHaveProperty('bc2_source_references');
    expect(request.params.target_pdb).toBe(model === 'ppiflow' ? 'inputs/derived.pdb' : 'inputs/native.cif');
    expect(request.execution_target_id).toBeNull();
});
it('deliberate BC2 antibody handoff keeps the independent PPIFlow framework document identity', async () => {
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: 'bindcraft2', mode: 'campaign', name: 'Framework handoff', params: { bindcraft2_settings: { targets: [{ name: 'alternate', target_path: 'inputs/native.cif' }], binder_scaffold: 'inputs/framework.pdb', max_trajectories: 3 }, bc2_source_references: handoff.bc2.references } }));
    await mount('/submit'); await ready(() => !!document.querySelector('[aria-label="BindCraft2 campaign"]'));
    await select('Binder format / objective', 'antibody'); await click('PPIFlow · antibody generation'); await ready(() => !!document.querySelector('[aria-label="Choose destination sources"]'));
    const checkbox = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find(node => node.parentElement?.textContent?.includes('Also reuse'))!;
    await act(async () => checkbox.click()); await click('Use BC2 target 1: alternate'); await ready(() => !!document.querySelector('[aria-label="framework_pdb"]'));
    expect(document.querySelector<HTMLInputElement>('[aria-label="framework_pdb"]')!.value).toBe('inputs/framework.pdb');
    await click('Template Manager'); await edit('input[placeholder="e.g., My Boltz Config"]', 'Framework saved'); await click('Save Template'); await ready(() => rows.length === 1);
    expect(rows[0].params.framework_pdb_source_reference).toMatchObject({ path: 'inputs/framework.pdb', document: { artifact_id: 'framework-doc' } });
    expect(rows[0].params).not.toHaveProperty('antigen_chain'); expect(rows[0].params).not.toHaveProperty('specified_hotspots');
});
it.each([1280, 390])('native parent composition at %ipx puts retained round only in Generation and policy after source editor', async width => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
    await mount('/submit?model=ppiflow&mode=protein_binder'); await ready(() => !!document.querySelector('[aria-label="Native binder job name"]'));
    const round = document.querySelector<HTMLInputElement>('[aria-label="Automatic blind complex prediction"]')!;
    expect(round.closest('[hidden]')).not.toBeNull();
    const sceneParent = document.querySelector('[aria-label="Target source"]')!;
    const policy = document.querySelector('[aria-label="Native binder execution settings"]')!;
    expect(sceneParent.compareDocumentPosition(policy) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await click('Generation'); expect(round.closest('[hidden]')).toBeNull();
    await act(async () => round.click()); await settle(); expect(round.checked).toBe(false);
    await click('Targets & templates'); expect(round.closest('[hidden]')).not.toBeNull();
    await click('Generation'); expect(document.querySelector('[aria-label="Automatic blind complex prediction"]')).toBe(round); expect(round.checked).toBe(false);
});
it('actual parent source collision preserves destination clear across mode roundtrip and emitted request', async () => {
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: 'ppiflow', mode: 'protein_binder', name: 'Collision', params: { native_generation_authoring: true, target_pdb: '', target_pdb_source_reference: reference, binder_source_handoff: handoff } }));
    await mount('/submit'); await ready(() => !!document.querySelector('[aria-label="target_pdb"]'));
    expect(document.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!.value).toBe('');
    const changeMode = async (mode: string) => { const node = [...document.querySelectorAll('select')].find(node => [...node.options].some(option => option.value === 'protein_binder'))!; await act(async () => { node.value = mode; node.dispatchEvent(new Event('change', { bubbles: true })); }); await settle(); };
    await changeMode('antibody_binder'); await changeMode('protein_binder');
    expect(document.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!.value).toBe('');
    await click('Template Manager'); await edit('input[placeholder="e.g., My Boltz Config"]', 'Collision saved'); await click('Save Template'); await ready(() => rows.length === 1);
    expect(rows[0].params.target_pdb).toBe(''); expect(rows[0].params.binder_source_handoff).toEqual(handoff);
    await click('✕'); await click('Launch Experiment'); await ready(() => calls.some(call => call.url === '/api/jobs'));
    expect(calls.find(call => call.url === '/api/jobs')!.body.params.target_pdb).toBe('');
    expect(calls.find(call => call.url === '/api/jobs')!.body.params).not.toHaveProperty('binder_source_handoff');
    expect(calls.some(call => call.url === '/api/files/materialize-structure')).toBe(false);
    expect((fetch as any).mock.calls.some(([url]: any[]) => String(url).includes('native.cif'))).toBe(false);
});
it('canonical direct campaign mounts dedicated owner from INIT and retains context URL', async () => {
    await mount('/submit?model=bindcraft2&mode=campaign&return_to=%2Fprojects%2FP&extra=keep');
    await ready(() => !!document.querySelector('[aria-label="BindCraft2 campaign"]'));
    expect(document.querySelector('[aria-label="Draft name"]')).not.toBeNull();
    expect(document.querySelector('[data-location]')!.textContent).toContain('engine=bindcraft2');
    expect(document.querySelector('[data-location]')!.textContent).toContain('extra=keep');
    expect(document.body.textContent).toContain('Required: explicit positive integer; no default');
});
it('real parent BC2 Back/re-entry restores engine URL in the same transition with retained sparse values', async () => {
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: 'bindcraft2', mode: 'campaign', name: 'Retained', params: { bindcraft2_settings: { max_trajectories: 7, trajectory_only: false } } }));
    await mount('/submit?extra=keep&return_to=%2Fprojects%2FP');
    await ready(() => !!document.querySelector('[aria-label="BindCraft2 campaign"]'));
    await click('← Back to workflows');
    const card = [...document.querySelectorAll<HTMLElement>('h3')].find(node => node.textContent?.includes('De Novo Binder Design'))?.closest<HTMLElement>('.cursor-pointer')!;
    expect(card).toBeTruthy(); await act(async () => card.click());
    expect(document.querySelector('[data-location]')!.textContent).toContain('engine=bindcraft2');
    await ready(() => !!document.querySelector('[aria-label="BindCraft2 campaign"]'));
    expect(document.querySelector<HTMLInputElement>('[aria-label="Draft name"]')!.value).toBe('Retained');
    expect(document.body.textContent).toContain('7');
    expect(document.querySelector('[data-location]')!.textContent).toContain('extra=keep');
});
it('noncampaign direct action uses existing lifecycle source/options editor and actual Axios submission', async () => {
    await mount('/submit?model=bindcraft2&mode=rank&bc2_source_job_id=producer');
    await ready(() => !!document.querySelector('[aria-label="BindCraft2 lifecycle draft"]'));
    expect(document.querySelector<HTMLInputElement>('[aria-label="Source campaign Job"]')!.value).toBe('producer');
    await edit('[aria-label="top"]', '3'); await click('Run native operation');
    const request = calls.find(call => call.url === '/api/jobs')!.body;
    expect(request).toMatchObject({ model_id: 'bindcraft2', mode: 'rank', execution_target_id: null, params: { bc2_source_job_id: 'producer', bc2_action_options: { top: 3, list: false } } });
});
it('actual campaign Save/unmount/fresh Template Manager Load restores governed source inspection and unchanged scientific drafts without Job POST', async () => {
    const path = 'rcsb/6aru.pdb';
    const origin = { name: 'RCSB: 6ARU', path, url: 'https://files.rcsb.org/download/6ARU.pdb', document: { artifact_id: 'rcsb-6aru', target_state: 'native' } };
    const references = { 'target:0': { path, source: { name: 'Retained model', path: 'original/other.pdb', modelNumber: 7, chainIds: ['A'], derivedFrom: origin } } };
    const settings = { targets: [{ name: '6ARU', target_path: path, chains: 'A', hotspots: 'A42' }], modality: ['VHH'], max_trajectories: 4, trajectory_only: false };
    const round = { schema_version: 1, enabled: true, sequence_design: { model_id: 'fampnn', params: { seqs_per_design: 3, fampnn_psce_threshold: 0 } }, prediction: { model_id: 'protenix', params: { protenix_model_weights: 'protenix-v2', protenix_use_msa: false } }, binder_chains: [], target_chains: [] };
    const roundDrafts = { fampnn: { seqs_per_design: 3, fampnn_psce_threshold: 0 }, caliby_binder: { caliby_num_seqs_per_pdb: 5, caliby_verbose: false } };
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: 'bindcraft2', mode: 'campaign', name: 'Saved campaign', params: { bindcraft2_settings: settings, bc2_source_references: references, binder_round: round, binder_round_drafts: roundDrafts } }));
    await mount('/submit'); await ready(() => !!document.querySelector('[aria-label="BindCraft2 campaign"]'));
    await click('Save campaign draft'); await ready(() => !!document.querySelector('input[placeholder="e.g., My Boltz Config"]'));
    await edit('input[placeholder="e.g., My Boltz Config"]', 'Campaign saved'); await click('Save Template'); await ready(() => rows.length === 1);
    const saved = JSON.parse(JSON.stringify(rows[0]));
    expect(saved.model_id).toBe('bindcraft2'); expect(saved.mode).toBe('campaign');
    expect(saved.params.bindcraft2_settings).toEqual(settings); expect(saved.params.binder_round).toEqual(round);
    expect(saved.params.bc2_source_references).toEqual(references); expect(saved.params.binder_round_drafts).toMatchObject(roundDrafts);
    await unmount(); (fetch as any).mockClear(); calls = [];
    await mount('/submit'); expect(document.querySelector('[aria-label="BindCraft2 campaign"]')).toBeNull();
    await click('Template Manager'); await ready(() => !!document.querySelector('input[placeholder="Search templates..."]'));
    await click('Load'); await ready(() => !!document.querySelector('[aria-label="Source identity"]') && !!document.querySelector('[data-viewer]'));
    expect(document.querySelector('[aria-label="Source identity"]')!.textContent).toContain('RCSB: 6ARU · PDB');
    expect(document.querySelector('[aria-label="Source identity"]')!.textContent).toContain('source document rcsb-6aru');
    expect(document.querySelector('[aria-label="Source identity"]')!.textContent).toContain('model 7 · chains A');
    expect(document.querySelector('[data-viewer]')!.textContent).toBe(PDB.replace('ALA a', 'ALA A'));
    expect(document.querySelector('[aria-label="targets.0.target_path"]')!.textContent).toBe(path);
    expect((fetch as any).mock.calls.map(([url]: any[]) => String(url)).filter((url: string) => !url.includes('/native-settings'))).toEqual([`/api/files/download/${encodeURIComponent(path)}`]);
    await click('Save campaign draft'); await edit('input[placeholder="e.g., My Boltz Config"]', 'Campaign reopened'); await click('Save Template'); await ready(() => rows.length === 2);
    expect(rows[1].params.bindcraft2_settings).toEqual(settings); expect(rows[1].params.bc2_source_references).toEqual(references);
    expect(rows[1].params.binder_round).toEqual(round); expect(rows[1].params.binder_round_drafts).toEqual(saved.params.binder_round_drafts);
    expect(calls.some(call => call.url === '/api/jobs' && call.method === 'post')).toBe(false);
    expect(calls.some(call => call.url === '/api/files/materialize-structure' || call.url === '/api/files/upload')).toBe(false);
});
function BC2InspectionHarness({ initial, references }: { initial: any; references: Record<string, { path: string; source: BC2Source }> }) {
    const [value, setValue] = useState(initial);
    const [refs, setRefs] = useState(references);
    sourceState = value; draft = refs;
    return <><button type="button" onClick={() => setRefs(old => ({ ...old, 'target:0': { ...old['target:0'], source: { ...old['target:0'].source, name: 'Updated inspection context' } } }))}>Update inspection context</button>
        <button type="button" onClick={() => setValue((old: any) => ({ ...old, targets: [{ ...old.targets[0], target_path: 'inputs/replaced.pdb' }] }))}>Replace governed path</button>
        <BindCraft2StructureInputs value={value} onChange={setValue} inventory={bc2 as any} sourceReferences={refs} onSourcePrepared={entry => setRefs(old => ({ ...old, [entry.role === 'target' ? `target:${entry.targetIndex}` : 'scaffold']: { path: entry.path, source: entry.source } }))} /></>;
}
it('BC2 cached inspection updates provenance without reread/remount and replaced path ignores stale role ancestry', async () => {
    const path = 'inputs/active.pdb';
    const source = { name: 'RCSB: 6ARU', path: 'inputs/wrong.pdb', url: 'https://files.rcsb.org/download/6ARU.pdb', file: new File(['WRONG BYTES'], 'wrong.pdb'), document: reference.document, modelNumber: 7, chainIds: ['a'] };
    const refs = { 'target:0': { path, source }, 'target:1': { path: 'inputs/replaced.pdb', source: { ...source, name: 'Wrong target slot' } }, scaffold: { path: 'inputs/replaced.pdb', source: { ...source, name: 'Wrong scaffold slot' } } };
    await mount('/submit', <BC2InspectionHarness initial={{ targets: [{ name: 'Active', target_path: path, chains: 'a', hotspots: 'a42' }] }} references={refs} />);
    await ready(() => !!document.querySelector('[data-viewer]'));
    expect(document.querySelector('[aria-label="Source identity"]')!.textContent).toContain('RCSB: 6ARU');
    expect(document.querySelector('[data-viewer]')!.textContent).toBe(PDB);
    const scene = document.querySelector('[data-viewer]'), mounts = renderer.mounts;
    await click('Update inspection context');
    expect(document.querySelector('[aria-label="Source identity"]')!.textContent).toContain('Updated inspection context');
    expect(document.querySelector('[data-viewer]')).toBe(scene); expect(renderer.mounts).toBe(mounts);
    expect((fetch as any).mock.calls.map(([url]: any[]) => String(url))).toEqual([`/api/files/download/${encodeURIComponent(path)}`]);
    await click('Replace governed path'); await ready(() => document.querySelector('[aria-label="Source identity"]')?.textContent === 'inputs/replaced.pdb · PDB');
    expect(sourceState.targets[0]).toEqual({ name: 'Active', target_path: 'inputs/replaced.pdb', chains: 'a', hotspots: 'a42' });
    expect(draft['target:0'].path).toBe(path);
    expect((fetch as any).mock.calls.map(([url]: any[]) => String(url))).toEqual([`/api/files/download/${encodeURIComponent(path)}`, '/api/files/download/inputs%2Freplaced.pdb']);
    expect(calls).toHaveLength(0);
});
it('BC2 explicit model and scaffold-chain derivatives retain matching governed document context', async () => {
    const path = 'inputs/models.pdb';
    const source = { name: 'RCSB: 6ARU', path: 'ancestry/not-active.pdb', url: 'https://files.rcsb.org/download/6ARU.pdb', document: reference.document, modelNumber: 7, chainIds: ['a'], derivedFrom: { name: 'Original RCSB', document: reference.document } };
    const secondModel = PDB.replace('7.000', '9.000');
    const models = `MODEL        1\n${PDB.replace('END\n', '')}ENDMDL\nMODEL        2\n${secondModel.replace('END\n', '')}ENDMDL\nEND\n`;
    (fetch as any).mockImplementation(async () => ({ ok: true, text: async () => models }));
    await mount('/submit', <BC2InspectionHarness initial={{ targets: [{ target_path: path }], binder_scaffold: path }} references={{ 'target:0': { path, source }, scaffold: { path, source: { ...source, name: 'Scaffold RCSB' } } }} />);
    await ready(() => !!document.querySelector('[aria-label="Use source model"]'));
    await select('Use source model', '2'); await ready(() => sourceState.targets[0].target_path === 'inputs/upload-1.pdb');
    const targetSource = draft['target:0'].source;
    expect(targetSource.modelNumber).toBe(2);
    expect(targetSource.derivedFrom).toEqual({ ...source, path, file: undefined });
    expect(targetSource.derivedFrom.document).toEqual(reference.document);
    expect(targetSource.derivedFrom.modelNumber).toBe(7); expect(targetSource.derivedFrom.chainIds).toEqual(['a']);
    expect(await calls.find(call => call.url === '/api/files/upload')!.body.get('file').text()).toBe(secondModel);
    await click('Inspect scaffold'); await ready(() => !!button('Use selected scaffold chains'));
    await click('Use selected scaffold chains'); await ready(() => sourceState.binder_scaffold === 'inputs/upload-2.pdb');
    expect(draft.scaffold.source.chainIds).toEqual(['a']);
    expect(draft.scaffold.source.derivedFrom).toEqual({ ...source, name: 'Scaffold RCSB', path, file: undefined });
    expect((fetch as any).mock.calls.map(([url]: any[]) => String(url))).toEqual([`/api/files/download/${encodeURIComponent(path)}`]);
    expect(calls.filter(call => call.url === '/api/files/upload')).toHaveLength(2);
    expect(calls.some(call => call.url === '/api/files/materialize-structure' || call.url === '/api/jobs')).toBe(false);
});
it('campaign browse/save use real modal; incompatible Load stays open and explicit own-workflow Load routes', async () => {
    rows = [{ id: 'other', name: 'Other native workflow', model_id: 'ppiflow', mode: 'protein_binder', icon: 'bookmark', color: '#6B7280', params: { native_generation_authoring: true, target_pdb: 'inputs/other.pdb', dataset_seed: 0, self_condition: false } }];
    await mount('/submit?model=bindcraft2&mode=campaign');
    await ready(() => !!document.querySelector('[aria-label="BindCraft2 campaign"]'));
    await click('Saved campaigns'); await ready(() => !!document.querySelector('input[placeholder="Search templates..."]'));
    expect(document.body.textContent).toContain('My Templates');
    await click('Load'); expect(document.body.textContent).toContain('belongs to another workflow');
    expect(document.querySelector('input[placeholder="Search templates..."]')).not.toBeNull();
    await click('Load in its own workflow'); await ready(() => !!document.querySelector('[aria-label="Native binder job name"]'));
    expect(document.querySelector('[data-location]')!.textContent).toContain('model=ppiflow');
});
it('initial modal intent is edge-triggered through Back, late draft, controlled close and reopen', async () => {
    function Harness() { const [open, setOpen] = useState(true); const [value, setValue] = useState({ zero: 0 });
        return <><button onClick={() => setValue({ zero: 9 })}>Late draft</button><button onClick={() => setOpen(!open)}>Toggle modal</button><TemplateManagerModal isOpen={open} onClose={() => setOpen(false)} initialIntent="save" currentParams={value} currentModelId="bindcraft2" currentMode="campaign" /></>; }
    await mount('/submit', <Harness />); expect(document.body.textContent).toContain('Save as Template');
    await click('← Back to list'); await click('Late draft'); expect(document.body.textContent).toContain('My Templates');
    await click('Toggle modal'); await click('Toggle modal'); expect(document.body.textContent).toContain('Save as Template');
    await edit('input[placeholder="e.g., My Boltz Config"]', 'Late saved'); await click('Save Template');
    await ready(() => rows.length === 1); expect(rows[0].params).toEqual({ zero: 9 });
    await ready(() => document.body.textContent!.includes('My Templates'));
});
function SourceHarness({ initial }: { initial: any }) { const [values, setValues] = useState(initial); sourceState = values; return <NativeBinderSource model="ppiflow" mode="protein_binder" field="target_pdb" label="Target" values={values} onPatch={patch => setValues((old: any) => ({ ...old, ...patch }))} onChains={() => {}} />; }
it('receiving exact CIF uses checked server conversion; provenance and full/sequence view survive serialized reopen', async () => {
    await mount('/submit', <SourceHarness initial={{ target_pdb: 'inputs/native.cif', target_pdb_source_reference: reference }} />);
    await ready(() => sourceState.target_pdb === 'inputs/derived.pdb' && !!document.querySelector('[data-viewer]'));
    expect(calls.filter(call => call.url === '/api/files/materialize-structure')).toHaveLength(1);
    expect(calls.find(call => call.url === '/api/files/materialize-structure')!.body).toMatchObject({ path: 'inputs/native.cif', output_format: 'pdb', model_number: 7 });
    const scene = document.querySelector('[data-viewer]'); const mounts = renderer.mounts;
    await click('Open full viewer'); await click('Sequence'); await click('3D structure');
    expect(document.querySelector('[data-viewer]')).toBe(scene); expect(renderer.mounts).toBe(mounts);
    await click('Sequence'); const saved = JSON.parse(JSON.stringify(sourceState));
    await unmount(); await mount('/submit', <SourceHarness initial={saved} />); await ready(() => !!document.querySelector('[data-viewer]'));
    expect(button('Sequence').getAttribute('aria-pressed')).toBe('true'); expect(button('Close full viewer')).toBeTruthy();
    expect(sourceState.target_pdb_source_reference.materialization).toEqual(materialization);
    expect(calls.filter(call => call.url === '/api/files/materialize-structure')).toHaveLength(1);
});
it.each(['', 'inputs/replaced.pdb'])('explicit destination %j never reads or reacquires stale producer bytes', async path => {
    await mount('/submit', <SourceHarness initial={{ target_pdb: path, target_pdb_source_reference: reference }} />);
    await settle(); expect(sourceState.target_pdb).toBe(path);
    expect(calls.some(call => call.url === '/api/files/materialize-structure')).toBe(false);
    expect((fetch as any).mock.calls.some(([url]: any[]) => String(url).includes('native.cif'))).toBe(false);
});
it('campaign review separates requested/inherited/effective and missing limit refocus is presentation only', async () => {
    let section = '';
    const requested = { trajectory_only: false };
    function Harness() { const [preview, setPreview] = useState<any>(null); return <BindCraft2Campaign name="Sparse" onNameChange={() => {}} onBack={() => {}} generatorChooser={null} requestedSettings={requested} inheritedSettings={{ number_of_final_designs: 1 }} inheritedOrigins={{ number_of_final_designs: 'native runtime fallback' }} preview={preview} previewBusy={false} submitting={false} onPreview={() => setPreview({ effective_settings: { number_of_final_designs: 9 } })} onLaunch={() => {}} onOpenLibrary={() => {}} executionTarget={null} library={null} onSectionChange={next => { section = next; }}><div data-bc2-field="max_trajectories"><input aria-label="max_trajectories" /></div></BindCraft2Campaign>; }
    await mount('/submit', <Harness />);
    expect(document.body.textContent).toContain('Required: explicit positive integer; no default');
    expect(document.body.textContent).toContain('native runtime fallback');
    await click('Set required attempt limit'); await settle(); expect(section).toBe('campaign'); expect(document.activeElement?.getAttribute('aria-label')).toBe('max_trajectories');
    expect(button('Preview native campaign').disabled).toBe(false);
    await click('Preview native campaign'); expect(document.body.textContent).toContain('matching native preview');
    const retained = [...document.querySelectorAll('dt')].find(node => node.textContent === 'Retained designs')!;
    expect(retained.nextElementSibling?.textContent).toBe('9'); expect(requested).toEqual({ trajectory_only: false });
});
it('retained legacy RF refinement sliders have exact numeric pairs and preserve saved noise precision', async () => {
    function Harness() { const [settings, setSettings] = useState({ ...PRESETS.balanced, rfantibody_noise_scale_ca: 0.25, rfantibody_noise_scale_frame: 0.25125 }); draft = settings; return <QualitySettingsPanel settings={settings} onSettingsChange={setSettings} showRfantibodySettings showSequenceDesignSettings={false} showStructureValidationSettings={false} showPpiflowSettings={false} />; }
    await mount('/submit', <Harness />);
    const header = [...document.querySelectorAll('button')].find(node => node.textContent?.includes('Quality'))!; await act(async () => header.click());
    await ready(() => !!document.querySelector('[aria-label="Noise scale CA exact"]'));
    expect(document.querySelector<HTMLInputElement>('[aria-label="Noise scale CA exact"]')!.value).toBe('0.25');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Noise scale frame exact"]')!.value).toBe('0.25125');
    await edit('[aria-label="Noise scale CA exact"]', '0.34567'); expect(draft.rfantibody_noise_scale_ca).toBe(0.34567);
});
