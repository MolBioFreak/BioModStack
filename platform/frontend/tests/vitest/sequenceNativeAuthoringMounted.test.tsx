import React, { act } from 'react';
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ models: [] as any[], library: null as any, project: null as any, save: vi.fn() }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    fetchModels: vi.fn(async () => ({ data: state.models.filter(m => !m.experimental) })),
    fetchModelById: vi.fn(async (id: string) => ({ data: state.models.find(m => m.id === id) })),
    fetchTemplates: vi.fn(async () => ({ data: [] })), fetchInputPresets: vi.fn(async () => ({ data: [] })),
    fetchFiles: vi.fn(async () => ({ data: { entries: [{ path: 'inputs/benign-fixture.cif', name: 'benign-fixture.cif', is_directory: false }] } })),
    fetchExecutionTargets: vi.fn(async () => ({ data: [{ id: 'fixture-worker', name: 'Fixture worker', active: true, state: 'ready', capabilities: {} }] })),
}));
vi.mock('../../src/lib/projectManager', async original => ({ ...await original<typeof import('../../src/lib/projectManager')>(),
    getProjectWorkflowSetup: vi.fn(async () => structuredClone(state.project)), saveProjectWorkflowSetupDraft: state.save,
}));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: (props: any) => { state.library = props; return null; } }));
vi.mock('../../src/components/SequenceManagerModal', () => ({ SequenceManagerModal: () => null }));
import { JobSubmission } from '../../src/components/JobSubmission';
import { api } from '../../src/lib/api';
// Preload the actual ordinary remote approval dialog; transport alone is mocked.
import '../../src/components/ExecutionPlanApproval';

// Read the real model owners rather than maintain a test-only scientific schema.
// The API development environment supplies Python/PyYAML/Pydantic; an explicit
// interpreter may be selected by CI without changing the request fixtures.
const python = process.env.BMS_TEST_PYTHON || 'python3';
const inventory = JSON.parse(execFileSync(python, ['-c', `
import json,sys,yaml
sys.path.insert(0, '../api')
from services.caliby_native import REQUEST
from services.ligandmpnn_design import NativeOptions
from model_registry import ModelDefinition
print(json.dumps({'models':[ModelDefinition.model_validate(yaml.safe_load(open('../api/config/models/'+name+'.yaml'))).model_dump() for name in ['caliby_experimental','ligandmpnn']], 'caliby':REQUEST.json_schema(), 'ligand':NativeOptions.model_json_schema()}))
`], { encoding: 'utf8' }));
let root: Root | undefined; let client: QueryClient;
const adapter = api.defaults.adapter;
const originalUrl = window.location.href;
let requests: any[]; let previews: any[];
const evidence: any[] = [];
const button = (text: string) => [...document.querySelectorAll('button')].find(b => b.textContent?.trim() === text)!;
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); }
async function click(text: string) { expect(button(text), text).toBeTruthy(); await act(async () => button(text).click()); await settle(); }
async function edit(label: string, value: string) {
    const element = document.querySelector<HTMLInputElement | HTMLSelectElement>(`input[aria-label="${label}"], select[aria-label="${label}"]`)!;
    expect(element, label).toBeTruthy();
    await act(async () => { Object.getOwnPropertyDescriptor(element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(element, value); element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); });
}
async function clickLabel(label: string) { const element = document.querySelector<HTMLElement>(`[aria-label="${label}"]`)!; expect(element, label).toBeTruthy(); await act(async () => element.click()); }
async function mount(model: string, mode: string, params: any = {}, project = false, remote = false, fresh = false) {
    if (!project && !fresh) localStorage.setItem('clonedJobData', JSON.stringify({ name: 'Benign software fixture', model_id: model, mode, params, execution_target_id: remote ? 'fixture-worker' : null }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    const route = `/submit?model=${model}&mode=${mode}${project ? '&project_id=destination&setup_context_id=setup' : ''}`;
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><JobSubmission /></MemoryRouter></QueryClientProvider>));
    await settle(); await settle();
}
async function unmount() { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); }
async function saveAndReopen(model: string, mode: string) {
    await click('Template Manager');
    const saved = JSON.parse(JSON.stringify(state.library.currentParams));
    expect(state.library.currentModelId).toBe(model); expect(state.library.currentMode).toBe(mode);
    await act(async () => state.library.onSelect({ name: 'Reopened fixture', model_id: model, mode, params: saved }));
    await settle(); await click('Template Manager'); expect(state.library.currentParams).toEqual(saved);
    await unmount(); await mount(model, mode, saved);
    await click('Template Manager'); expect(state.library.currentParams).toEqual(saved);
    return saved;
}
async function submit(model: string, mode: string) {
    await click('Launch Experiment'); await settle();
    expect(requests.length).toBeGreaterThan(0);
    const request = requests.at(-1); expect(request).toMatchObject({ model_id: model, mode });
    const effective = JSON.parse(execFileSync(python, ['-c', `
import sys,json
sys.path.insert(0,'../api')
from services.caliby_native import normalize_request
from services.ligandmpnn_design import normalize_design_params
r=json.load(sys.stdin)
print(json.dumps((normalize_request if r['model_id']=='caliby_experimental' else normalize_design_params)(r['mode'],r['params'])))
`], { input: JSON.stringify(request), encoding: 'utf8' }));
    evidence.push({ request, effective });
    if (process.env.BMS_SEQUENCE_UI_EVIDENCE) writeFileSync(process.env.BMS_SEQUENCE_UI_EVIDENCE, JSON.stringify({ inventory, examples: evidence }, null, 2));
    return request;
}
beforeEach(() => {
    window.history.replaceState({}, '', '/submit');
    state.models = structuredClone(inventory.models); state.library = null; requests = []; previews = [];
    state.project = { project_id: 'destination', setup_context_id: 'setup', generation: 1, draft: {}, project_label: 'Destination', experiment_label: 'Experiment', workflow_label: 'Sequence', state: 'open', return_uri: '/projects/destination', field_errors: {}, global_experiment_id: 'global', domain_experiment_id: 'domain' };
    state.save.mockImplementation(async (_project, _setup, request) => { state.project = { ...state.project, generation: state.project.generation + 1, draft: structuredClone(request.draft) }; return structuredClone(state.project); });
    api.defaults.adapter = async config => {
        const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
        if (config.url === '/api/jobs' && config.method === 'post') { requests.push(body); return { data: {}, status: 200, statusText: 'OK', headers: {}, config }; }
        if (config.url === '/api/jobs/execution-plan/preview') { previews.push(body); return { data: { schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true, request: body, plan: { requested_json: body.params, effective_json: body.params, source_identity: { revision: 'fixture', tree: 'fixture' }, metadata: { static_components: [], dynamic_templates: [], external_services: [] } }, deferred_preparation: [], blockers: [] }, status: 200, statusText: 'OK', headers: {}, config }; }
        throw Error(`Unexpected fixture transport: ${config.method} ${config.url}`);
    };
    vi.stubGlobal('fetch', vi.fn(() => { throw Error('Unexpected fixture fetch'); }));
});
afterEach(async () => { await unmount(); api.defaults.adapter = adapter; window.history.replaceState({}, '', originalUrl); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each([['caliby_experimental', 'ensemble_design'], ['caliby_experimental', 'sidechain_pack'], ...['ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware'].map(mode => ['ligandmpnn', mode])])('fresh %s/%s route authors a named request without clone data', async (model, mode) => {
    await mount(model, mode, {}, false, false, true);
    await edit('Sequence job name', 'Fresh benign fixture');
    if (model === 'ligandmpnn') { await click('Browse managed files'); await click('benign-fixture.cif'); }
    else {
        if (mode === 'ensemble_design') { await click('Add ensemble'); await edit('ensembles.0.ensemble_id', 'benign'); }
        const prefix = mode === 'ensemble_design' ? 'ensembles.0.states' : 'structures';
        await click(`Add ${prefix} state`); await edit(`${prefix}.0.state_id`, 'primary'); await click('Browse managed files'); await click('benign-fixture.cif');
    }
    const parameterNames = inventory.models.find((m: any) => m.id === model).modes.find((m: any) => m.id === mode).params;
    const custom = model === 'caliby_experimental' ? ['ensembles', 'structures', 'omit_aas'] : [];
    for (const name of parameterNames.filter((name: string) => !custom.includes(name))) {
        const card = document.querySelector(`[data-sequence-designer-field="${name}"]`);
        expect(card?.querySelector('input, select, button'), name).toBeTruthy();
    }
    const request = await submit(model, mode); expect(request.name).toBe('Fresh benign fixture');
});
it('Caliby ensemble rows, all native constraints and managed sources survive reorder, save, reopen and real submission', async () => {
    await mount('caliby_experimental', 'ensemble_design', { num_workers: 0, gaussian_n_conformers: 0, gaussian_noise_std: 0, potts_rejection_step: false, unrelated: 'discard' });
    expect(document.querySelector('[aria-label="Caliby native authoring"]')).toBeTruthy();
    await click('Add ensemble'); await edit('ensembles.0.ensemble_id', 'benign-ensemble');
    for (let index = 0; index < 2; index++) {
        await click('Add ensembles.0.states state');
        await edit(`ensembles.0.states.${index}.state_id`, `state-${index}`);
        await edit(`ensembles.0.states.${index}.path`, `inputs/benign-${index}.cif`);
        for (const name of ['fixed_pos_seq', 'fixed_pos_scn', 'fixed_pos_override_seq', 'pos_restrict_aatype', 'symmetry_pos']) await edit(`ensembles.0.states.${index}.${name}`, '');
    }
    await click('Browse managed files'); await click('benign-fixture.cif');
    await clickLabel('Move ensembles.0.states.1 up');
    await click('Add ensemble'); await edit('ensembles.1.ensemble_id', 'second'); await click('Add ensembles.1.states state'); await edit('ensembles.1.states.0.state_id', 'only'); await edit('ensembles.1.states.0.path', 'inputs/second.pdb');
    await clickLabel('Move ensembles.1 up'); await clickLabel('Move ensembles.0 down');
    await edit('ensembles.0.states.0.fixed_pos_seq', 'A1');
    await clickLabel('omit_aas.C');
    const saved = await saveAndReopen('caliby_experimental', 'ensemble_design');
    expect(saved.ensembles[0].states.map((s: any) => s.state_id)).toEqual(['state-1', 'state-0']);
    expect(saved.ensembles[0].states[1].path).toBe('inputs/benign-fixture.cif');
    expect(saved.omit_aas).toEqual([]);
    const request = await submit('caliby_experimental', 'ensemble_design');
    expect(request.params).toMatchObject({ ensembles: saved.ensembles, omit_aas: [], num_workers: 0, potts_rejection_step: false });
    expect(request.params).not.toHaveProperty('unrelated'); expect(request.params).not.toHaveProperty('structures');
});
it('Caliby packing has fixed-sequence structure rows only and retains inactive draft on mode switch', async () => {
    await mount('caliby_experimental', 'sidechain_pack', { ensembles: [{ ensemble_id: 'retained', states: [{ state_id: 'primary', path: 'inputs/other.cif' }] }], scn_step_scale: 0, num_workers: 0 });
    await click('Add structures state'); await edit('structures.0.state_id', 'pack'); await edit('structures.0.path', 'inputs/benign-fixture.cif');
    await click('Add structures state'); await edit('structures.1.state_id', 'discard'); await clickLabel('Remove structures.1');
    expect(document.querySelector('[aria-label="omit_aas.C"]')).toBeNull();
    const saved = await saveAndReopen('caliby_experimental', 'sidechain_pack');
    const modePicker = [...document.querySelectorAll('select')].find(item => [...item.options].some(option => option.textContent === 'Select a mode...'))!;
    await act(async () => { modePicker.value = 'ensemble_design'; modePicker.dispatchEvent(new Event('change', { bubbles: true })); });
    expect((document.querySelector('[aria-label="ensembles.0.ensemble_id"]') as HTMLInputElement).value).toBe('retained');
    await act(async () => { modePicker.value = 'sidechain_pack'; modePicker.dispatchEvent(new Event('change', { bubbles: true })); });
    expect((document.querySelector('[aria-label="structures.0.state_id"]') as HTMLInputElement).value).toBe('pack');
    const request = await submit('caliby_experimental', 'sidechain_pack');
    expect(request.params).toMatchObject({ structures: saved.structures, scn_step_scale: 0, num_workers: 0 });
    expect(request.params).not.toHaveProperty('ensembles'); expect(request.params).not.toHaveProperty('temperature');
});

const nullableNames = Object.entries(inventory.ligand.properties).filter(([, schema]: any) => schema.anyOf?.some((branch: any) => branch.type === 'null')).map(([name]) => name === 'seed' ? 'design_seed' : name);
it.each(['ligand_aware', 'ntp_aware', 'metal_aware', 'dna_aware'])('ordinary %s preserves every explicit nullable native option through mounted save and request', async mode => {
    const params = { target_pdb: 'inputs/benign-fixture.cif', ...Object.fromEntries(nullableNames.map(name => [name, null])), write_fasta: false, write_structures: false, ligand_smiles: '', ntp_type: '', metal_type: '', dna_sequence: '' };
    await mount('ligandmpnn', mode, params);
    for (const name of nullableNames) expect(document.querySelector(`[data-native-option="${name}"]`), name).toBeTruthy();
    const saved = await saveAndReopen('ligandmpnn', mode);
    for (const name of nullableNames) expect(saved[name], name).toBeNull();
    const request = await submit('ligandmpnn', mode);
    expect(request.params).toMatchObject(params); expect(request.params).not.toHaveProperty('seed');
});

it('ordinary LigandMPNN authors every nested bias axis, omissions, grouped symmetry and native list with typed controls', async () => {
    await mount('ligandmpnn', 'ligand_aware', { target_pdb: 'inputs/benign-fixture.cif', write_structures: false, write_fasta: false });
    const maps: Record<string, string[]> = { bias: ['ALA'], bias_per_residue: ['A1', 'ALA'], pair_bias: ['ALA', 'GLY'], pair_bias_per_residue_pair: ['A1', 'A2', 'ALA', 'GLY'], temperature_per_residue: ['A1'] };
    for (const [name, keys] of Object.entries(maps)) {
        await click(`Set ${name} empty`); let path = name;
        for (const key of keys) { await edit(`${path}.new-key`, key); await click(`Add ${path} key`); path += `.${key}`; }
        await edit(path, '0');
    }
    await click('Set omit_per_residue empty'); await edit('omit_per_residue.new-key', 'A1'); await click('Add omit_per_residue key'); await click('Add omit_per_residue.A1 entry'); await edit('omit_per_residue.A1.0', 'UNK');
    for (const [name, value] of Object.entries({ remove_ccds: 'HOH', fixed_residues: 'A0B', designed_residues: 'A2', fixed_chains: 'A', designed_chains: 'B', omit: 'UNK' })) {
        await click(`Set ${name} empty`); await click(`Add ${name} entry`); await edit(`${name}.0`, value);
    }
    for (const [name, entries] of Object.entries({ symmetry_residues: ['A1', 'A2'], symmetry_residues_weights: ['0', '1'], homo_oligomer_chains: ['A', 'B'] })) {
        await click(`Set ${name} empty`); await click(`Add ${name} group`);
        for (let i = 0; i < entries.length; i++) { await click(`Add ${name}.0 entry`); await edit(`${name}.0.${i}`, entries[i]); }
    }
    // Remove incompatible scopes explicitly; no frontend scientific normalizer.
    for (const name of ['fixed_residues', 'designed_residues', 'fixed_chains', 'designed_chains', 'homo_oligomer_chains']) await click(`Set ${name} null`);
    await edit('remove_waters', 'false'); await edit('atomize_side_chains', 'false'); await edit('structure_noise', '0'); await edit('design_seed', '0');
    const saved = await saveAndReopen('ligandmpnn', 'ligand_aware');
    expect(saved.pair_bias_per_residue_pair).toEqual({ A1: { A2: { ALA: { GLY: 0 } } } });
    expect(saved.symmetry_residues_weights).toEqual([[0, 1]]); expect(saved.remove_waters).toBe(false);
    const request = await submit('ligandmpnn', 'ligand_aware');
    expect(request.params).toMatchObject({ bias: { ALA: 0 }, bias_per_residue: { A1: { ALA: 0 } }, omit_per_residue: { A1: ['UNK'] }, pair_bias: { ALA: { GLY: 0 } }, temperature_per_residue: { A1: 0 }, design_seed: 0, structure_noise: 0 });
});
it('cleared nullable numeric text is saved as empty, never coerced to null or a default', async () => {
    await mount('ligandmpnn', 'metal_aware', { target_pdb: 'inputs/benign-fixture.cif' });
    await edit('structure_noise', '');
    const saved = await saveAndReopen('ligandmpnn', 'metal_aware');
    expect(saved.structure_noise).toBe('');
    await edit('structure_noise', '0'); await submit('ligandmpnn', 'metal_aware');
});
it.each([['caliby_experimental', 'sidechain_pack'], ['ligandmpnn', 'ntp_aware']])('ordinary remote review binds the exact typed %s/%s request and placement', async (model, mode) => {
    const params = model === 'ligandmpnn' ? { target_pdb: 'inputs/benign-fixture.cif', bias: {}, temperature: null, write_structures: false } : { structures: [{ state_id: 'primary', path: 'inputs/benign-fixture.cif' }], num_workers: 0 };
    await mount(model, mode, params, false, true);
    await click('Launch Experiment');
    await vi.waitFor(async () => { await settle(); expect(button('Approve and submit')).toBeTruthy(); });
    expect(requests).toEqual([]); expect(previews).toHaveLength(1);
    expect(previews[0]).toMatchObject({ model_id: model, mode, execution_target_id: 'fixture-worker', params });
    await click('Show all settings');
    expect(document.body.textContent).toContain(model === 'ligandmpnn' ? '(empty object)' : 'structures.0.path');
    await click('Approve and submit');
    expect(requests).toHaveLength(1); expect(requests[0]).toEqual({ ...previews[0], execution_plan_approval: 'a'.repeat(64) });
});
it('interface_context retains the pre-existing generic editor and diagnostic seed, never the ordinary authoring controls', async () => {
    await mount('ligandmpnn', 'interface_context', { seed: 0, samples: 1, temperature: 0.1, binder_chain: 'A', target_chain: 'B', target_patch: ['B1'] });
    expect(document.querySelector('[aria-label="LigandMPNN native authoring"]')).toBeNull();
    expect(document.querySelector('[aria-label="design_seed"]')).toBeNull();
    await click('Launch Experiment'); expect(requests[0].params).toMatchObject({ seed: 0, samples: 1 });
    expect(requests[0].params).not.toHaveProperty('design_seed');
});
it.each([['caliby_experimental', 'ensemble_design'], ['ligandmpnn', 'dna_aware']])('Project save/unmount/reopen keeps %s/%s nested controls in the real parent', async (model, mode) => {
    const params = model === 'ligandmpnn' ? { target_pdb: 'inputs/benign-fixture.cif', bias: { ALA: 0 }, atomize_side_chains: false, temperature: null } : { ensembles: [{ ensemble_id: 'benign', states: [{ state_id: 'primary', path: 'inputs/benign-fixture.cif', fixed_pos_seq: '' }] }], omit_aas: [], num_workers: 0 };
    state.project.draft = { model_id: model, mode, job_name: 'Project fixture', ...params };
    await mount(model, mode, {}, true);
    if (model === 'ligandmpnn') await edit('bias.ALA', '1'); else await edit('ensembles.0.ensemble_id', 'edited');
    await click('Save draft');
    const saved = structuredClone(state.project.draft);
    expect(saved).toMatchObject({ model_id: model, mode, job_name: 'Project fixture' });
    expect(model === 'ligandmpnn' ? saved.bias.ALA : saved.ensembles[0].ensemble_id).toBe(model === 'ligandmpnn' ? 1 : 'edited');
    await unmount(); await mount(model, mode, {}, true); await click('Save draft');
    expect(state.project.draft).toEqual(saved);
});
