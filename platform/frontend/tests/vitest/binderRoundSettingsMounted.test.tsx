import React, { act } from 'react';
import { execFileSync } from 'node:child_process';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BinderRoundSettings } from '../../src/components/BinderRoundSettings';
import { AntibodyDenovoTemplate } from '../../src/components/AntibodyDenovoTemplate';
import { JobSubmission } from '../../src/components/JobSubmission';
import { binderRoundDesigners, binderRoundPredictors, hydrateBinderRound, roundParameterIsBound, withRoundCatalogs } from '../../src/lib/binderRound';

// Read the actual global contracts, not a reduced collection of hand-written
// sampling fields. This subprocess only decodes repository YAML; no science runs.
const catalogs = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON || 'python3', ['-c',
    "import json,yaml; from pathlib import Path; p=Path('../api/config/models'); print(json.dumps([yaml.safe_load((p/(x+'.yaml')).read_text()) for x in ['proteinmpnn','fampnn','caliby_binder','protenix','boltz2','esmfold2']]))"], { encoding: 'utf8' }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <div data-webgl-fixture /> }));
const nativeModels = ['ppiflow', 'boltzgen'].map(id => ({ id, name: id, category: 'generative_design', params: [], modes: [{ id: 'protein_binder', name: 'Protein binder', params: ['target_pdb', 'dataset_seed', 'self_condition'] }] }));
let root: Root | undefined; let client: QueryClient;
let submitted: any[]; let previews: any[]; let savedTemplates: any[];
const field = (native_key: string, type: string) => ({ native_key, observed_types: [type], has_native_default: false, native_default: null, status: 'typed' });
const inventory = { fields: { max_trajectories: field('max_trajectories', 'integer'), trajectory_only: field('trajectory_only', 'boolean') }, presets: {}, paratope_conformations: [], registered_metrics: { filters: {}, losses: {} } };
const PDB = 'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C  \nEND\n';
beforeEach(() => {
    submitted = []; previews = []; savedTemplates = [];
    vi.spyOn(api, 'get').mockImplementation(async (url: string) => {
        const model = catalogs.find((item: any) => url === `/api/models/${item.id}`);
        if (model) return { data: structuredClone(model) };
        if (url === '/api/models') return { data: nativeModels };
        if (url.endsWith('/integration')) return { data: { workflows: {} } };
        if (url.includes('system')) return { data: { gpus: [] } };
        if (url.includes('cached')) return { data: { cached: [] } };
        if (url.includes('templates')) return { data: savedTemplates };
        return { data: [] };
    });
    vi.spyOn(api, 'post').mockImplementation(async (url: string, body: any) => {
        if (url.endsWith('/campaign/preview')) { previews.push(structuredClone(body)); return { data: { preview_digest: 'native-only-digest', requested_settings: body.params.bindcraft2_settings, effective_settings: body.params.bindcraft2_settings } }; }
        if (url === '/api/jobs' || url === '/api/jobs/antibody-iteration/from-designs') { submitted.push(structuredClone(body)); return { data: { id: 'round-fixture' } }; }
        if (url.includes('templates')) { savedTemplates.push(structuredClone(body)); return { data: body }; }
        throw new Error(`Unexpected POST ${url}`);
    });
    vi.stubGlobal('fetch', vi.fn(async (input: any) => {
        const url = String(input);
        if (url.includes('/native-settings')) return { ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: inventory }) };
        if (url.includes('/generation-settings')) return { ok: true, json: async () => ({ mode: 'protein_binder', parameters: [{ name: 'target_pdb', type: 'file' }, { name: 'dataset_seed', type: 'integer', default: 12 }, { name: 'self_condition', type: 'boolean', default: true }] }) };
        return { ok: true, text: async () => PDB, blob: async () => new Blob([PDB]), json: async () => ({}) };
    }));
    if (!Blob.prototype.text) Object.defineProperty(Blob.prototype, 'text', { configurable: true, value: function () { return new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(this); }); } });
    if (!URL.createObjectURL) URL.createObjectURL = () => 'blob:fixture';
    if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
});
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); }
async function mount(node: React.ReactNode, route: string | { pathname: string; state: Record<string, unknown> } = '/submit') {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}>{node}</MemoryRouter></QueryClientProvider>)); await settle(); await settle();
}
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await unmount(); localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function select(label: string, value: string) { const node = document.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!; expect(node, label).toBeTruthy(); await act(async () => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); }); await settle(); }
async function edit(label: string, value: string) { const node = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!; expect(node, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); await settle(); }
async function click(label: string) { const node = [...document.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent?.trim() === label && !node.closest('[hidden]')); expect(node, label).toBeTruthy(); expect(node!.disabled).toBe(false); await act(async () => node!.click()); await settle(); }
async function toggle(label: string) { const node = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!; expect(node, label).toBeTruthy(); await act(async () => node.click()); await settle(); }
function expectedRound() { return withRoundCatalogs(hydrateBinderRound(), catalogs).binder_round; }

it('all six global inventories render every applicable typed field and preserve catalog defaults', async () => {
    let latest: any;
    function Form() { const [values, setValues] = React.useState(hydrateBinderRound()); latest = values; return <BinderRoundSettings values={values} onChange={setValues} />; }
    await mount(<Form />);
    expect(latest.binder_round).toEqual(expectedRound());
    expect([...document.querySelectorAll('[aria-label="Round sequence designer"] option')].map(node => node.getAttribute('value'))).toEqual(['proteinmpnn', 'fampnn', 'caliby_binder']);
    for (const id of [...binderRoundDesigners, ...binderRoundPredictors]) {
        const designer = binderRoundDesigners.some(model => model === id);
        await select(designer ? 'Round sequence designer' : 'Round complex validator', id);
        const group = document.querySelector(`[aria-label="${designer ? 'Round sequence design' : 'Round complex prediction'}"]`)!;
        if (designer) expect(group.querySelector<HTMLInputElement>(`[aria-label="${id === 'caliby_binder' ? 'caliby_num_seqs_per_pdb' : 'seqs_per_design'}"]`)!.value).toBe(id === 'caliby_binder' ? '4' : '8');
        for (const details of group.querySelectorAll('details')) await act(async () => details.querySelector('summary')!.click());
        const advertised = catalogs.find((model: any) => model.id === id).params.filter((param: any) => !roundParameterIsBound(param.name)).map((param: any) => param.name).sort();
        const controls = [...group.querySelectorAll('[data-native-setting], [data-round-fixed]')].map(node => node.getAttribute('data-native-setting') ?? node.getAttribute('data-round-fixed')).sort();
        expect(controls, id).toEqual(advertised);
        for (const node of group.querySelectorAll('[data-native-setting]')) expect(node.querySelector('input,select,button'), id).not.toBeNull();
    }
    await select('Round sequence designer', 'caliby_binder');
    expect(document.querySelector<HTMLInputElement>('[aria-label="caliby_num_seqs_per_pdb"]')!.value).toBe('4');
});

it('complete BC2 parent preserves designer drafts, false/zero/null and optout across saved/reopened authoring; native preview stays independent', async () => {
    let latest: any;
    const settings = { max_trajectories: 2, trajectory_only: false };
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialDraft={{ model_id: 'bindcraft2', mode: 'campaign', bindcraft2_settings: settings, binder_round_drafts: { proteinmpnn: { mpnn_extra_config: null } } }} onDraftChange={draft => { latest = draft; }} />);
    await select('Round sequence designer', 'proteinmpnn'); await edit('mpnn_backbone_noise', '0'); await edit('seqs_per_design', '3');
    await select('Round sequence designer', 'caliby_binder'); await edit('caliby_num_seqs_per_pdb', '7'); await toggle('caliby_verbose');
    await select('Round sequence designer', 'fampnn'); await edit('seqs_per_design', '5'); await toggle('fampnn_exclude_cys'); await edit('fampnn_psce_threshold', '0');
    await select('Round sequence designer', 'caliby_binder');
    expect(document.querySelector<HTMLInputElement>('[aria-label="caliby_num_seqs_per_pdb"]')!.value).toBe('7');
    expect(document.querySelector<HTMLInputElement>('[aria-label="caliby_verbose"]')!.checked).toBe(false);
    await select('Round complex validator', 'esmfold2'); await edit('seed', '0');
    await select('Round complex validator', 'protenix'); await toggle('protenix_use_msa'); await edit('protenix_n_sample', '3');
    expect(document.querySelector<HTMLInputElement>('[aria-label="protenix_use_template"]')!.disabled).toBe(true);
    await edit('Round binder_chains', 'h,');
    expect(document.querySelector<HTMLInputElement>('[aria-label="Round binder_chains"]')!.value).toBe('h,');
    await edit('Round binder_chains', 'h,l'); await edit('Round target_chains', 'T');
    expect(latest.binder_round.binder_chains).toEqual(['h', 'l']);
    expect(latest.binder_round.target_chains).toEqual(['T']);
    await click('Preview native campaign');
    expect(previews).toEqual([{ model_id: 'bindcraft2', mode: 'campaign', params: { bindcraft2_settings: settings } }]);
    await toggle('Automatic blind complex prediction');
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')).not.toBeNull();
    const saved = structuredClone(latest);
    expect(saved.binder_round.enabled).toBe(false);
    await click('Launch BindCraft2 campaign');
    expect(submitted[0].binder_round).toEqual(saved.binder_round);
    expect(submitted[0].params).toEqual({ bindcraft2_settings: settings, bc2_preview_digest: 'native-only-digest' });
    await unmount();
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialDraft={saved} onDraftChange={draft => { latest = draft; }} />);
    expect(latest.binder_round).toEqual(saved.binder_round); expect(latest.binder_round_drafts).toEqual(saved.binder_round_drafts);
    expect(latest.binder_round_drafts.proteinmpnn.mpnn_extra_config).toBeNull();
    expect(latest.binder_round_drafts.proteinmpnn.seqs_per_design).toBe(3);
    expect(latest.binder_round_drafts.fampnn.seqs_per_design).toBe(5);
    await select('Round sequence designer', 'fampnn'); expect(document.querySelector<HTMLInputElement>('[aria-label="fampnn_exclude_cys"]')!.checked).toBe(false);
    expect(document.querySelector<HTMLInputElement>('[aria-label="fampnn_psce_threshold"]')!.value).toBe('0');
    await select('Round complex validator', 'esmfold2'); expect(document.querySelector<HTMLInputElement>('[aria-label="seed"]')!.value).toBe('0');
});

it.each(['ppiflow', 'boltzgen'])('complete %s JobSubmission clone submits the round outside exact native params', async model => {
    const round = expectedRound(); round.enabled = false; round.prediction.params.protenix_n_sample = 3;
    const params = { native_generation_authoring: true, target_pdb: '', dataset_seed: 0, self_condition: false };
    localStorage.setItem('clonedJobData', JSON.stringify({ model_id: model, mode: 'protein_binder', name: 'Native round', binder_round: round, params }));
    await mount(<JobSubmission />, `/submit?model=${model}&mode=protein_binder`);
    expect(document.querySelector<HTMLInputElement>('[aria-label="Automatic blind complex prediction"]')!.checked).toBe(false);
    await click('Launch Experiment');
    expect(submitted).toHaveLength(1);
    expect(submitted[0]).toMatchObject({ model_id: model, mode: 'protein_binder', name: 'Native round', binder_round: round });
    expect(submitted[0].params).toEqual({ target_pdb: '', dataset_seed: 0, self_condition: false });
});

it('historical refinement keeps its selected designer and does not acquire a round envelope', async () => {
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialDraft={{ seq_designer: 'proteinmpnn', run_structure_validation: false, run_frustrampnn: false, framework_type: 'nanobody' }} />, { pathname: '/submit', state: { refinementMode: true, sourceJobId: 'source-fixture', selectedDesignIds: ['design-fixture'] } });
    expect(document.querySelector('[aria-label="Initial candidate round"]')).toBeNull();
    await click('Launch Antibody Refinement (1 outputs)');
    expect(submitted).toHaveLength(1);
    expect(submitted[0]).toMatchObject({ source_job_id: 'source-fixture', design_ids: ['design-fixture'], action: 'ui_refinement', param_overrides: { seq_designer: 'proteinmpnn', run_structure_validation: false } });
    expect(submitted[0]).not.toHaveProperty('binder_round');
    expect(submitted[0].param_overrides).not.toHaveProperty('binder_round');
});

it('complete RFantibody parent requests a round without running historical sequence/validation stages', async () => {
    let latest: any;
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialDraft={{ denovo_generator: 'rfantibody', job_name: 'RF round', target_source: { type: 'preset', path: 'inputs/target.pdb', name: 'Target' }, selected_chain: 'A', selected_residues: ['A1'], framework_type: 'nanobody' }} onDraftChange={draft => { latest = draft; }} />);
    const launch = [...document.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent?.includes('Launch RFantibody Batch'))!;
    expect(launch).toBeTruthy(); expect(launch.disabled).toBe(false);
    await act(async () => launch.click()); await settle();
    expect(submitted).toHaveLength(1);
    expect(submitted[0].binder_round).toEqual(latest.binder_round);
    expect(submitted[0].binder_round).toEqual(expectedRound());
    expect(submitted[0].params).not.toHaveProperty('binder_round');
    expect(submitted[0].params.run_structure_validation).toBe(false);
    expect(submitted[0].params.seq_designer).toBe('none');
});
