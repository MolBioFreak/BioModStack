import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';
import { api } from '../../src/lib/api';
import { readSavedConditions, savedConditionLayers } from '../../src/lib/rfd4SavedConditions';
import { Rfd4TaskWorkspace } from '../../src/components/Rfd4TaskWorkspace';
import { DeNovoNativeGenerationSettings } from '../../src/components/DeNovoNativeGenerationSettings';
// Isolate launch placement and optional operations, not authoring, sources, schema widgets or HTTP serialization.
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: () => null }));
vi.mock('../../src/components/dashboard/IndependentProvisionPanel', () => ({ DE_NOVO_PRELOAD_SELECTION: { kind: 'workflow_pack', workflow_id: 'de_novo_design' } }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: (props: any) => <div data-native-viewer data-layers={JSON.stringify(props.metricLayers)} data-linked={props.showSequenceTrack} data-controlled={props.controlledResidueSelection} data-selection={JSON.stringify(props.residueSelections)} /> }));
const cwd = resolve(process.cwd(), '../..');
const inventories = JSON.parse(execFileSync(process.env.BMS_TEST_API_PYTHON ?? 'python', ['-c', "import json,sys;sys.path.insert(0,'platform/api');from services.rfd4_proteina import generation_settings as r;from services.complexa import discovery as c;print(json.dumps({'rfd4_proteina':{m:r(m) for m in ('generate','evaluate')},'complexa':{m:c() for m in ('generate','evaluate')}}))"], { cwd, encoding: 'utf8', maxBuffer: 16000000 }));
let root: Root | undefined, client: QueryClient, old: typeof api.defaults.adapter, draft: any;
let wire: any[], unexpected: string[];
let xhr: ReturnType<typeof vi.spyOn>, fetchSpy: ReturnType<typeof vi.spyOn>;
const source = { path: '/retained/target.cif', model_number: 2, expected_sha256: 'exact-hash', output_format: 'native' };
const positions = [{ model_number: 2, label_asym_id: 'X', label_seq_id: 7, auth_asym_id: 'A', auth_seq_id: 42, pdbx_PDB_ins_code: 'B', label_comp_id: 'GLY', label_entity_id: '1', atoms: [{ label_atom_id: 'CA', auth_atom_id: 'CA', model_atom_index: 0, type_symbol: 'C', label_alt_id: '.' }] }];
beforeEach(() => {
    old = api.defaults.adapter; wire = []; unexpected = []; draft = undefined; localStorage.clear(); sessionStorage.clear();
    xhr = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => { unexpected.push('XHR'); throw new Error('No network'); });
    fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { unexpected.push('fetch'); throw new Error('No network'); });
    api.defaults.adapter = async config => {
        const url = config.url ?? '', body = config.data ? JSON.parse(config.data) : undefined;
        wire.push({ url, body }); let data: unknown;
        const match = url.match(/^\/api\/models\/(rfd4_proteina|complexa)\/generation-settings$/);
        if (match) data = inventories[match[1]][config.params.mode];
        else if (url === '/api/jobs') data = { id: 'inert' };
        else if (url.includes('materialize')) data = { path: source.path, source_structure: source, source_positions: positions, model_number: 2, model_numbers: [2], author_residues: [], native_format: 'cif', format: 'cif', source_identity: {}, source_path: source.path };
        else { unexpected.push(url); throw new Error(`Unexpected HTTP ${url}`); }
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
afterEach(async () => { await unmount(); api.defaults.adapter = old; xhr.mockRestore(); fetchSpy.mockRestore(); expect(unexpected).toEqual([]); });
const settle = () => act(async () => { await new Promise(r => setTimeout(r, 15)); });
async function mount(model: 'rfd4_proteina' | 'complexa', values: Record<string, unknown> = {}, mode = 'generate') {
    const el = document.createElement('div'); document.body.append(el); root = createRoot(el);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter><DeNovoNativeGenerationSettings model={model} mode={mode} initialValues={values} onDraftChange={v => { draft = v; }} /></MemoryRouter></QueryClientProvider>));
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[data-rfd4-task-workspace],[data-complexa-task-workspace]')).not.toBeNull(); });
}
async function click(text: string) { const node = [...document.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === text && !b.closest('[hidden]')); expect(node, text).toBeTruthy(); await act(async () => node!.click()); await settle(); }
async function edit(label: string, value: string) { const node = document.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`); expect(node, label).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), 'value')!.set!.call(node, value); node!.dispatchEvent(new Event(node!.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
async function disclose(text: string) { const node = [...document.querySelectorAll('summary')].find(s => s.textContent?.trim() === text); expect(node, text).toBeTruthy(); await act(async () => { const d = node!.parentElement as HTMLDetailsElement; d.open = true; d.dispatchEvent(new Event('toggle')); }); }
function normalize(request: unknown, model = 'rfd4', mode = 'generate') { return JSON.parse(execFileSync('python', ['-c', model === 'rfd4' ? "import sys,json;from scripts.lib.rfd4_proteina_contract import normalize_request;print(json.dumps(normalize_request(json.loads(sys.argv[1]),sys.argv[2])))" : "import sys,json;from scripts.lib.complexa_contract import normalize_request;print(json.dumps(normalize_request(json.loads(sys.argv[1]))))", JSON.stringify(request), mode], { cwd, encoding: 'utf8' })); }
it('saved projection retains native targets, model-local indices and missing payloads', async () => {
    const cif = readFileSync(resolve('tests/fixtures/rfd4-saved-conditions.cif'), 'utf8');
    const rows = await readSavedConditions(cif, 'exact-native', 1);
    expect(rows.find(r => r.family === 'C_INTCRD')?.value).toEqual(['1.0', '2.0', '3.0']);
    expect(rows.find(r => r.family === 'C_CRDGRP')?.value).toBe('7');
    expect(await readSavedConditions(cif, 'exact-native', 2)).toEqual([]);
    expect(savedConditionLayers(rows, 'exact-native').find(l => l.descriptor.label === 'Saved C_CRD')?.values[0]).toMatchObject({ value: null, missingness: 'unavailable' });
    const absent = cif.replace(/^_condition_InternalCoordinate_1_atom[^\n]*\n/gm, '');
    expect((await readSavedConditions(absent, 'exact-native')).some(r => r.family === 'C_INTCRD')).toBe(false);
    const scoped = cif.replace('_condition_InternalCoordinate_1_atom.idx0', '_condition_InternalCoordinate_1_atom.model_num 2\n_condition_InternalCoordinate_1_atom.idx0');
    expect((await readSavedConditions(scoped, 'exact-native', 1)).some(r => r.family === 'C_INTCRD')).toBe(false);
});
it('native enum choices write explicit values without changing manual historical keys', async () => {
    await mount('rfd4_proteina'); await edit('RFD4 task', 'motif'); await click('Add conditioned example');
    await edit('Condition family', 'C_HB'); await edit('Known native value', 'ACCEPTOR');
    await edit('Selection expression', 'chain_id == "X"'); await click('Apply native condition');
    expect(draft.rfd4_request.input.examples[0].conditions.C_HB.ACCEPTOR).toEqual([{ select: 'chain_id == "X"' }]);
    await edit('Native condition value', '2'); await click('Apply native condition');
    expect(draft.rfd4_request.input.examples[0].conditions.C_HB['2']).toHaveLength(1);
});
it('mounted source projects native-writer saved targets separately from selection', async () => {
    const cif = readFileSync(resolve('tests/fixtures/rfd4-saved-conditions.cif'), 'utf8').replace(/(_atom_site\.pdbx_PDB_model_num\s+)1/, '$12');
    // Keep native writer bytes unchanged apart from selected MODEL fixture binding.
    fetchSpy.mockImplementation(async () => ({ ok: true, text: async () => cif } as Response));
    await mount('rfd4_proteina', { native_sources: { exact: source }, rfd4_request: { input: { kind: 'annotated', slots: ['exact'] } } });
    await disclose('Native sources'); await click('Inspect retained exact'); await click('Read saved conditions for exact');
    const layers = JSON.parse(document.querySelector('[data-native-viewer]')!.getAttribute('data-layers')!);
    expect(layers.find((l: any) => l.descriptor.label === 'Saved C_INTCRD').values[0]).toMatchObject({ value: 1, identity: { documentId: source.path, labelAsymId: 'X', atomIndex: 0, modelNumber: 2 } });
    expect(document.body.textContent).toContain('1.0, 2.0, 3.0');
    expect(layers.find((l: any) => l.descriptor.label === 'Saved C_CRDGRP').values[0].value).toBe(7);
    expect(JSON.parse(document.querySelector('[data-native-viewer]')!.getAttribute('data-selection')!)).toEqual([]);
    expect(draft.rfd4_request.input).toEqual({ kind: 'annotated', slots: ['exact'] });
});
it('fresh source-free chains reach actual Axios and native normalization without default hydration', async () => {
    await mount('rfd4_proteina'); await edit('RFD4 task', 'source_free'); await edit('Input name', 'chains');
    await click('Add generated chain'); await edit('Chain 1 ID', 'A'); await edit('Chain 1 min_length', '20'); await edit('Chain 1 max_length', '30');
    await click('Add generated chain'); await edit('Chain 2 ID', 'B'); await edit('Chain 2 min_length', '40'); await edit('Chain 2 max_length', '40'); await edit('Seed', '0');
    expect(document.querySelector('[data-native-viewer]')).toBeNull();
    await click('Generate candidates'); const request = wire.find(w => w.url === '/api/jobs').body.params.rfd4_request;
    expect(request).toEqual({ input: { kind: 'unconditional', name: 'chains', chains: [{ chain_id: 'A', min_length: 20, max_length: 30 }, { chain_id: 'B', min_length: 40, max_length: 40 }] }, settings: { seed: 0 } });
    expect(normalize(request)).toMatchObject(request); expect(draft.native_job_request.params.native_recipe).toBeUndefined();
});
it('an unclassified legacy request and exact source binding can be restored after choosing a recipe', async () => {
    const request = { input: { kind: 'annotated', slots: ['target.v2'] }, settings: { seed: 0 } };
    await mount('rfd4_proteina', { rfd4_request: request, native_sources: { 'target.v2': source } });
    await edit('RFD4 task', 'motif');
    const select = document.querySelector<HTMLSelectElement>('[aria-label="RFD4 task"]')!;
    const restore = [...select.options].find(o => o.value.startsWith('legacy:'))!;
    await edit('RFD4 task', restore.value); expect(draft.rfd4_request).toEqual(request); expect(draft.native_sources).toEqual({ 'target.v2': source }); expect(draft.native_recipe).toBeUndefined();
});
it('same-kind recipes retain source identity, explicit clears and zero through serialized cold reopen', async () => {
    await mount('rfd4_proteina'); await edit('RFD4 task', 'motif'); await click('Add conditioned example'); await edit('Example 1 name', 'motif');
    await edit('Seed', '0'); await edit('Seed', ''); const motif = JSON.parse(JSON.stringify(draft.rfd4_request));
    await edit('RFD4 task', 'protein_context'); await click('Add conditioned example'); await edit('Example 1 name', 'target');
    const saved = JSON.parse(JSON.stringify(draft)); await unmount(); await mount('rfd4_proteina', saved); await edit('RFD4 task', 'motif');
    expect(draft.rfd4_request).toEqual(motif); expect(draft.rfd4_request.settings.seed).toBe(''); expect(draft.native_recipe).toBe('motif');
});
it('actual exact source selection explicitly applies independent coordinates/probability, regions and pairs', async () => {
    await mount('rfd4_proteina', { native_recipe: 'motif', native_sources: { 'target.v2': source }, rfd4_request: { input: { kind: 'condition_spec', examples: [{ name: 'motif', sources: [{ slot: 'target.v2', input_as_annotated_cif: true }] }] } } });
    await disclose('Native sources'); await click('Inspect retained target.v2');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Retained residue for target.v2"]')).not.toBeNull(); });
    await edit('Retained residue for target.v2', '0'); await edit('Retained atom for target.v2', '0'); await click('Add exact selection from target.v2');
    expect(draft.rfd4_request.input.examples[0].conditions).toBeUndefined();
    expect(document.querySelector('[data-native-viewer]')?.getAttribute('data-linked')).toBe('true');
    expect(JSON.parse(document.querySelector('[data-native-viewer]')!.getAttribute('data-selection')!)).toMatchObject([{ labelAsymId: 'X', authSeqId: 42 }]);
    await click('Use visual selection in new condition'); await edit('Native condition value', 'true'); await click('Apply native condition');
    await edit('Condition family', 'C_SEQ_PROB_CYS'); await edit('Native condition value', '0'); await click('Apply native condition');
    await edit('Native contig', 'X7-7,20-40,/0,X7-7'); await click('Move region 2 earlier'); await click('Add pair condition'); await click('Use selection for pair 1 left'); await click('Use selection for pair 1 right');
    const ex = draft.rfd4_request.input.examples[0]; expect(ex.contig).toBe('20-40,X7-7,/0,X7-7'); expect(ex.conditions.C_SEQ_PROB_CYS['0'][0].select).toContain('res_id == 7'); expect(ex.conditions.C_CRD.true[0].select).toContain('atom_name == "CA"');
    await click('Clear visual selection'); expect(draft.rfd4_request.input.examples[0]).toEqual(ex);
    await click('Generate candidates'); const body = wire.find(w => w.url === '/api/jobs').body;
    expect(body.params.native_sources['target.v2']).toEqual(source); expect(normalize(body.params.rfd4_request)).toMatchObject(body.params.rfd4_request);
});
it.each(['partial', 'inverse'])('%s has separate annotated task and exact native request', async recipe => {
    await mount('rfd4_proteina'); await edit('RFD4 task', recipe); await click('Add annotated slot'); await edit('Annotated slot 1', 'saved.targets');
    if (recipe === 'partial') { await edit('partial_trajectory_fraction_steps', '0.25'); await edit('partial_t_backbone', '0'); await edit('partial_t_latent', '0.5'); }
    await click('Generate candidates'); const request = wire.find(w => w.url === '/api/jobs').body.params.rfd4_request;
    expect(request.input).toEqual({ kind: 'annotated', slots: ['saved.targets'] }); expect(normalize(request)).toMatchObject(request);
    if (recipe === 'inverse') expect(request.groups).toEqual({ generation: 'inverse_folding' }); else expect(request.settings.generation.args).toEqual({ partial_trajectory_fraction_steps: 0.25, partial_t_backbone: 0, partial_t_latent: 0.5 });
});
it('fresh evaluation basket and retained task labels reach evaluate transport without sampling defaults', async () => {
    await mount('rfd4_proteina', { rfd4_request: { evaluation_tasks: [{ slot: 'exact', task: 'enzyme' }] } }, 'evaluate'); await click('Add annotated slot'); await edit('Annotated slot 1', 'exact'); await click('Evaluate candidates');
    const body = wire.find(w => w.url === '/api/jobs').body; expect(body.mode).toBe('evaluate'); expect(body.params.rfd4_request.settings).toBeUndefined(); expect(normalize(body.params.rfd4_request, 'rfd4', 'evaluate').evaluation_tasks).toEqual([{ slot: 'exact', task: 'enzyme' }]);
});
it('checkpoint timeline edits sparse actual native search and explicit empty state', async () => {
    const request = { input: { kind: 'unconditional', name: 'x', chains: [{ chain_id: 'A', min_length: 20, max_length: 20 }] }, settings: { search: { checkpoints: { '40': { rewards: { exact: { weight: 0, normalize_by: 2, clip: { min: -1, max: 1 } } }, continuation_criteria: { criterion: { min_exclusive: 0, max: null, mean_of: ['raw'] } } } }, save_states: false } } };
    await mount('rfd4_proteina', { rfd4_request: request }); await click('Native settings'); await disclose('Search checkpoint timeline'); await edit('Integration step', '80'); await click('Add checkpoint');
    expect(draft.rfd4_request.settings.search.checkpoints['40']).toEqual(request.settings.search.checkpoints['40']); expect(draft.rfd4_request.settings.search.checkpoints['80']).toEqual({});
    await click('Explicit empty checkpoints'); expect(draft.rfd4_request.settings.search).toEqual({ checkpoints: {}, save_states: false });
    await click('Generate candidates'); expect(normalize(wire.find(w => w.url === '/api/jobs').body.params.rfd4_request).settings.search.checkpoints).toEqual({});
});
it('Complexa separate motif/ligand adoption retains author insertion identity and inactive reward across reopen', async () => {
    const reward = { tmol: { enable_hbond: false, elec_weight: 0 }, tmol_weight: 0 };
    await mount('complexa', { native_sources: { 'target.v2': source }, complexa_request: { task: 'AME', task_name: 'ame', source: { document_id: 'target.v2' }, reward } });
    await disclose('Native sources'); await click('Inspect retained target.v2');
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="Retained residue for target.v2"]')).not.toBeNull(); });
    await edit('Retained residue for target.v2', '0'); await edit('Retained atom for target.v2', '0'); await click('Add exact selection from target.v2');
    expect(draft.complexa_request.source.motif_atoms).toBeUndefined(); await click('Apply selection to motif atoms');
    expect(draft.complexa_request.source.motif_atoms).toEqual([{ chain: 'A', residue: 42, insertion_code: 'B', atoms: ['CA'] }]); expect(draft.complexa_request.source.ligand_atoms).toBeUndefined();
    await click('Apply selection to ligand atoms'); await disclose('Reward activation'); await edit('Native reward state', 'off');
    const saved = JSON.parse(JSON.stringify(draft)); await unmount(); await mount('complexa', saved); await disclose('Reward activation'); await edit('Native reward state', 'configured');
    expect(draft.complexa_request.reward).toEqual(reward); await click('Generate candidates');
    const body = wire.find(w => w.url === '/api/jobs').body; expect(body.params.native_control_drafts).toBeUndefined(); expect(normalize(body.params.complexa_request, 'complexa')).toEqual(body.params.complexa_request);
});
it('all discovered RFD4 condition families get explicit rows and native request round trips', async () => {
    const inputSchema = inventories.rfd4_proteina.generate.parameter_schema.properties.input.oneOf.find((s: any) => s.properties.kind.const === 'condition_spec');
    const families = Object.keys(inputSchema.properties.examples.items.properties.conditions.properties);
    await mount('rfd4_proteina', { native_recipe: 'motif', rfd4_request: { input: { kind: 'condition_spec', examples: [{ name: 'all-families', sources: [{ slot: 'exact' }] }] } } });
    await edit('Selection expression', 'chain_id == "X" and res_id == 7');
    for (const family of families) { await edit('Condition family', family); await edit('Native condition value', family.startsWith('C_SEQ_PROB_') ? '0' : 'true'); await click('Apply native condition'); }
    const conditions = draft.rfd4_request.input.examples[0].conditions;
    expect(Object.keys(conditions)).toEqual(families);
    await click('Undo condition edit'); expect(Object.keys(draft.rfd4_request.input.examples[0].conditions)).toEqual(families.slice(0, -1));
    await click('Apply native condition'); await click('Generate candidates');
    const request = wire.find(w => w.url === '/api/jobs').body.params.rfd4_request; expect(normalize(request).input.examples[0].conditions).toEqual(conditions);
});
it('ordered generated spans and source fragments directly edit native contig', async () => {
    await mount('rfd4_proteina'); await edit('RFD4 task', 'motif'); await click('Add conditioned example');
    await edit('Generated minimum', '25'); await edit('Generated maximum', '50'); await click('Append generated region');
    await edit('Source fragment', 'X7-12'); await click('Append source fragment'); await click('Add chain break');
    expect(draft.rfd4_request.input.examples[0].contig).toBe('25-50,X7-12,/0'); await click('Move region 2 earlier'); expect(draft.rfd4_request.input.examples[0].contig).toBe('X7-12,25-50,/0');
});
it('Complexa evaluation basket preserves order and exact slots across cold reopen and transport', async () => {
    await mount('complexa', { complexa_request: { task: 'protein_binder', task_name: 'eval', operation: 'analysis' }, native_sources: { exact: source } });
    await click('Add evaluation candidate'); await edit('Complexa evaluation slot 1', 'exact');
    await click('Add evaluation candidate'); await edit('Complexa evaluation slot 2', 'second');
    await click('Remove evaluation candidate 1');
    const saved = JSON.parse(JSON.stringify(draft)); await unmount(); await mount('complexa', saved);
    expect(draft.complexa_request.evaluation_slots).toEqual(['second']); expect(draft.native_sources.exact).toEqual(source);
    await click('Evaluate and analyze'); const body = wire.find(w => w.url === '/api/jobs').body;
    expect(normalize(body.params.complexa_request, 'complexa').evaluation_slots).toEqual(['second']);
});
it.each(['rfd4_proteina', 'complexa'] as const)('%s shows real native snapshots without default hydration', async model => {
    const request = model === 'complexa' ? { task: 'AME', reward: null } : { groups: { generation: 'inverse_folding', recycle_strategy: 'thinking_low' }, settings: { seed: 0 } };
    await mount(model, { [model === 'complexa' ? 'complexa_request' : 'rfd4_request']: request });
    await click('Native settings'); await disclose('Native profile snapshots');
    expect(document.body.textContent).toContain(model === 'complexa' ? 'reward_model' : 'independent single-group snapshots');
    expect(document.body.textContent).toContain('n_recycle');
    expect(draft[model === 'complexa' ? 'complexa_request' : 'rfd4_request']).toEqual(request);
});
it.skipIf(!process.env.BMS_TEST_NATIVE_READBACK)('replays actual installed combined composition through observed backend preview wire without hydration', async () => {
    const result = JSON.parse(readFileSync(process.env.BMS_TEST_NATIVE_READBACK!, 'utf8'));
    const adapter = api.defaults.adapter as Function;
    api.defaults.adapter = async config => {
        if (config.url?.endsWith('/generation-settings/preview')) {
            expect(JSON.parse(config.data)).toEqual(result.request); expect(config.params.mode).toBe('generate');
            return {config,data:result,status:200,statusText:'OK',headers:{}};
        }
        return adapter(config);
    };
    await mount('rfd4_proteina', {rfd4_request:result.request}); await click('Native settings'); await disclose('Native profile snapshots'); await click('Read selected effective configuration');
    expect(JSON.parse(document.querySelector('[aria-label="Selected native effective configuration"]')!.textContent!)).toEqual(result.effective_config);
    expect(draft.rfd4_request).toEqual(result.request);
    await click('Design'); await edit('Seed','1'); await click('Native settings');
    expect(document.querySelector('[aria-label="Selected native effective configuration"]')).toBeNull(); expect(document.body.textContent).toContain('Request changed since readback');
});
it('native readback failure is observational and does not alter request or run availability', async () => {
    const adapter = api.defaults.adapter as Function;
    api.defaults.adapter = async config => {
        if (config.url?.endsWith('/generation-settings/preview')) throw new Error('Native test unavailable');
        return adapter(config);
    };
    await mount('rfd4_proteina', {rfd4_request:{settings:{seed:0}}}); await click('Native settings'); await disclose('Native profile snapshots'); await click('Read selected effective configuration');
    expect(document.body.textContent).toContain('Native test unavailable'); expect(draft.rfd4_request).toEqual({settings:{seed:0}});
    expect([...document.querySelectorAll('button')].find(b=>b.textContent === 'Generate candidates')!.disabled).toBe(false);
});
it.each([{ second: ['Y'], adopt: true }, { second: ['X'], adopt: false }, { second: undefined, adopt: false }])('multi-source selection requires known noncolliding native chains: %j', async ({second, adopt}) => {
    const el = document.createElement('div'); document.body.append(el); root = createRoot(el);
    await act(async () => root!.render(<Rfd4TaskWorkspace request={{input:{kind:'condition_spec',examples:[{name:'multi',sources:[{slot:'first'},{slot:'second'}]}]}}} schema={inventories.rfd4_proteina.generate.parameter_schema} mode="generate" recipe="motif" onRecipe={() => {}} onChange={() => {}} renderNative={() => null} sourceNames={['first','second']} sourceChains={{first:['X'],...(second ? {second} : {})}} visual={{slot:'first',expression:'chain_id == "X" and res_id == 7'}} />));
    expect([...document.querySelectorAll('button')].some(b => b.textContent === 'Use visual selection in new condition')).toBe(adopt);
    expect(document.querySelector('[aria-label="Selection expression"]')).not.toBeNull();
});
it('provider catalog searches real schema constructors without activation and retained state slots keep paths', async () => {
    await mount('rfd4_proteina', { native_sources: { state: { kind: 'file', path: '/retained/search.pt' } }, rfd4_request: {} });
    await click('Native settings'); await disclose('Metric providers, constructors and native oracles'); await edit('Search native providers and metrics', 'amino_acid');
    const menu = document.querySelector<HTMLSelectElement>('[aria-label="Provider / metric"]')!;
    expect(menu.options.length).toBeGreaterThan(1); await edit('Provider / metric', menu.options[1].value);
    expect(draft.rfd4_request.providers).toBeUndefined();
    await disclose('Runtime, output and retained assets'); await edit('resume_slot', 'state');
    expect(document.body.textContent).toContain('/retained/search.pt'); expect(draft.rfd4_request.resume_slot).toBe('state');
    await click('Omit resume_slot'); expect(draft.rfd4_request.resume_slot).toBeUndefined();
});
it('Complexa tasks/counts/reward and authoritative operation survive actual transport', async () => {
    await mount('complexa'); await edit('Complexa task', 'monomer_motif_indexed'); await edit('Native task name', 'indexed'); await edit('nsamples', '3'); await edit('seed', '0'); await edit('Native operation', 'analysis');
    await disclose('Reward activation'); await edit('Native reward state', 'off'); await click('Evaluate and analyze'); const body = wire.find(w => w.url === '/api/jobs').body;
    expect(body.mode).toBe('evaluate'); expect(body.params.complexa_request).toEqual({ task: 'monomer_motif_indexed', task_name: 'indexed', nsamples: 3, seed: 0, operation: 'analysis', reward: null }); expect(normalize(body.params.complexa_request, 'complexa')).toEqual(body.params.complexa_request);
});
