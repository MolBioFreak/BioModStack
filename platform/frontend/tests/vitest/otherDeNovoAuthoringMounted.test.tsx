import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { afterEach, expect, it, vi } from 'vitest';
import { DiscoJobsInput } from '../../src/components/DiscoJobsInput';
import { OtherDeNovoSettings } from '../../src/components/OtherDeNovoSettings';
import { moveDiscoEntity, removeDiscoEntity, discoInputAuthority } from '../../src/lib/discoAuthoring';
import { laMotifAtoms, laAtomKey, laMotifAtomSpec } from '../../src/lib/laProteinaAtomSelection';
import { api } from '../../src/lib/api';
const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_payload: any) => ({ data: {} })) }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(), submitJob: mocks.submit, completeCurrentLaunchContext: vi.fn(async () => null) }));
vi.mock('../../src/components/ExecutionTargetPicker', () => ({ ExecutionTargetPicker: ({ workflowRequest }: any) => <output data-request>{JSON.stringify(workflowRequest)}</output> }));
vi.mock('../../src/components/ModelDocumentationLinks', () => ({ ModelDocumentationLinks: () => null }));
vi.mock('../../src/components/ShapeBlueprintTemplate', () => ({ default: () => null }));
vi.mock('../../src/components/GeneralSequenceDesignSettings', () => ({ GeneralSequenceDesignSettings: () => null }));
vi.mock('../../src/components/LaProteinaMotifInput', () => ({ LaProteinaMotifInput: () => null }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => null }));
import { ProteinModificationTemplate } from '../../src/components/ProteinModificationTemplate';
const inventories = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python3', ['-c', `import json
from scripts.lib.rfd3_scientific_settings import scientific_inventory
from scripts.lib.laproteina_contract import settings_schema, PROFILES, resolve_profile
from scripts.lib.disco_contract import NativeSettings
from pydantic import TypeAdapter
print(json.dumps({'rfd3':scientific_inventory(),'laproteina':{'parameter_schema':settings_schema(),'profiles':{k:resolve_profile(k) for k in PROFILES}},'disco':{'parameter_schema':TypeAdapter(NativeSettings).json_schema()}}))`], { cwd: '../..', encoding: 'utf8' }));
let root: Root; let client: QueryClient; const previous = api.defaults.adapter;
let requests: string[] = []; let unexpected: string[] = [];
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); api.defaults.adapter = previous; vi.restoreAllMocks(); expect(unexpected).toEqual([]); requests = []; unexpected = []; });
async function render(node: React.ReactNode) {
    api.defaults.adapter = async config => { const name = String(config.url); if (name === '/api/models/protein_modification_experimental') return { data: { engine_references: {} }, config, status: 200, statusText: 'OK', headers: {} }; requests.push(`${name}:${config.params?.generator}`); if (!name.endsWith('/generation-settings')) { unexpected.push(name); throw Error(name); } return { data: inventories[config.params.generator], config, status: 200, statusText: 'OK', headers: {} }; };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
}
async function click(text: string) { const item = [...document.querySelectorAll('button,summary')].find(x => x.textContent === text); expect(item, text).toBeTruthy(); await act(async () => (item as HTMLElement).click()); await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); }
async function edit(label: string, value: string) { const item = document.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(item).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(item), 'value')!.set!.call(item, value); item.dispatchEvent(new Event(item.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }
it('RFD3 uses its own inventory, preserves saved sampler values and submits them without DISCO', async () => {
    const draft = vi.fn(); const settings = { num_timesteps: 23, cfg_scale: 0, allow_realignment: false, cfg_t_max: null, cfg_features: [] };
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={{ generator: 'rfd3', inference_sampler: settings, low_memory_mode: false }} />);
    expect(requests.every(x => x.endsWith(':rfd3'))).toBe(true);
    expect(document.body.textContent).not.toContain('DISCO native overrides');
    const request = JSON.parse(document.querySelector('[data-request]')!.textContent!);
    expect(request.params.inference_sampler).toEqual(settings); expect(request.params.low_memory_mode).toBe(false);
    const normalized = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python3', ['-c', 'import json,sys; from scripts.lib.rfd3_scientific_settings import execution_settings; print(json.dumps(execution_settings(json.loads(sys.stdin.read()))))'], { cwd: '../..', input: JSON.stringify(request.params), encoding: 'utf8' }));
    expect(normalized.inference_sampler).toEqual(settings);
    expect(draft.mock.calls.at(-1)![0].inference_sampler).toEqual(settings);
    await click('Generate candidates'); expect(mocks.submit.mock.calls.at(-1)![0].params.inference_sampler).toEqual(settings);
});
it('all current settings are reachable in model-specific sections without filling sparse requests', async () => {
    const changed = vi.fn(); await render(<OtherDeNovoSettings model="laproteina" task="motif_scaffolding" value={{}} onChange={changed} />);
    for (const title of ['Backbone sampler', 'Local latent sampler', 'Motif overrides', 'Sampling and outputs']) await click(title);
    for (const field of Object.keys(inventories.laproteina.parameter_schema.properties)) expect(document.querySelector(`[data-native-setting="${field}"], [title="${field}"]`), field).not.toBeNull();
    expect(changed).not.toHaveBeenCalled();
    for (const profile of Object.keys(inventories.laproteina.profiles)) expect(document.body.textContent).toContain(profile);
});
it.each(['rfd3', 'disco'] as const)('%s discovers the complete current settings inventory without writing overrides', async model => {
    const changed = vi.fn(); const value = model === 'rfd3' ? { inference_sampler: {} } : { pcad_disco_settings_json: { noisy_guidance: {}, sequence_sampling: {} } };
    await render(<OtherDeNovoSettings model={model} task="unconditional" value={value} onChange={changed} />);
    for (const summary of [...document.querySelectorAll('summary')]) await click(summary.textContent!);
    const fields = model === 'rfd3' ? inventories.rfd3.execution : inventories.disco.parameter_schema.properties;
    for (const name of Object.keys(fields).filter(name => name !== 'dump_trajectories')) expect(document.querySelector(`[data-native-setting="${name}"], [title="${name}"], [aria-label="inference_sampler.${name} presence"]`), name).not.toBeNull();
    if (model === 'rfd3') for (const name of Object.keys(fields.inference_sampler.properties)) expect(document.querySelector(`[data-native-setting="${name}"], [title="${name}"], [aria-label="inference_sampler.${name} presence"]`), name).not.toBeNull();
    expect(changed).not.toHaveBeenCalled();
});
it('native La modes replace invalid menu choices while preserving an explicit historical saved token', async () => {
    await render(<ProteinModificationTemplate onBack={() => {}} initialValues={{ generator: 'laproteina', design_task: 'motif_scaffolding', laproteina_atom_selection_mode: 'backbone' }} />);
    const select = [...document.querySelectorAll('select')].find(x => [...x.options].some(o => o.value === 'bb3o'))!;
    expect([...select.options].map(o => o.value)).toEqual(['all_atom', 'tip_atoms', 'bb3o', 'ca', 'backbone']); expect(select.value).toBe('backbone');
    expect(document.body.textContent).not.toContain('Samples per length');
});
it('ordered explicit jobs edit literal strands and retain bonds, unknown fields and file precedence', async () => {
    let saved: any;
    function Harness() { const [value, setValue] = useState<any>([{ name: 'duplex', retained: false, sequences: [{ proteinChain: { sequence: 'A--C', count: 1 } }, { dnaSequence: { sequence: 'ACGT', count: 1 } }], covalent_bonds: [{ left_entity: 1, left_position: 2, left_atom: 'CA', right_entity: 2, right_position: 1, right_atom: 0, right_copy: 1 }] }]); saved = value; return <DiscoJobsInput value={value} path="/native.json" onChange={setValue} />; }
    await render(<Harness />); await edit('Job 1 entity 2 sequence', 'AGGT'); await click('Move entity 2 up (remap bonds)');
    expect(saved[0].sequences).toEqual([{ dnaSequence: { sequence: 'AGGT', count: 1 } }, { proteinChain: { sequence: 'A--C', count: 1 } }]);
    expect(saved[0].covalent_bonds[0]).toMatchObject({ left_entity: 2, right_entity: 1, right_atom: 0, right_copy: 1 }); expect(saved[0].retained).toBe(false);
    expect(document.body.textContent).toContain('Active input: Explicit ordered jobs'); await click('Clear explicit jobs override'); expect(saved).toBeUndefined(); expect(document.body.textContent).toContain('Active input: Native JSON file');
});
it('native input precedence and reorder do not reinterpret DNA or atom types', () => {
    expect(discoInputAuthority([], '/source.json')).toBe('Explicit ordered jobs'); expect(discoInputAuthority(undefined, '')).toBe('Task shorthand');
    const job = { sequences: [{ dnaSequence: { sequence: 'ATGC', count: 1 } }, { ion: { ion: 'ZN', count: 2 } }], covalent_bonds: [{ left_entity: 1, right_entity: 2, left_atom: 'P', right_atom: 0 }] };
    expect(moveDiscoEntity(moveDiscoEntity(job, 0, 1), 0, 1)).toEqual(job);
    expect(removeDiscoEntity(job, 0)).toEqual({ sequences: [job.sequences[1]], covalent_bonds: [] });
});
it('DISCO parent retains explicit molecular jobs and sparse science across serialized cold reopen', async () => {
    const draft = vi.fn();
    const jobs = [{ name: 'native', sequences: [{ dnaSequence: { sequence: 'AGTC', count: 1 } }, { ligand: { ligand: 'CCD_ATP', count: 1 } }, { ion: { ion: 'MG', count: 2 } }], covalent_bonds: [] }];
    const initial = { generator: 'disco', design_task: 'custom_json', pcad_disco_jobs_json: jobs, pcad_disco_settings_json: { seeds: 0, num_inference_seeds: null, deterministic: false, noisy_guidance: { omega_seq: 0 } }, disco_input_json_path: '/retained/native.json' };
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={initial} />);
    await edit('DISCO job 1 name', 'renamed');
    const saved = JSON.parse(JSON.stringify(draft.mock.calls.at(-1)![0]));
    const expected = saved.native_job_request.params;
    await act(async () => root.unmount()); client.clear(); document.body.replaceChildren();
    await render(<ProteinModificationTemplate onBack={() => {}} initialValues={saved} />);
    const request = JSON.parse(document.querySelector('[data-request]')!.textContent!);
    expect(request.params).toEqual(expected);
    const normalized = JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON ?? 'python3', ['-c', 'import json,sys; from scripts.lib.disco_contract import build_native_jobs; print(json.dumps(build_native_jobs(json.loads(sys.stdin.read()))))'], { cwd: '../..', input: JSON.stringify(request.params.pcad_disco_jobs_json), encoding: 'utf8' }));
    expect(normalized).toEqual([{ ...jobs[0], name: 'renamed' }]);
    expect(request.params.pcad_disco_settings_json).toEqual(initial.pcad_disco_settings_json);
});
it('La atom authoring reads only existing selected source atoms and preserves native names', () => {
    const atomLine = (name: string, chain: string, residue: number) => `ATOM      1 ${name.padStart(4)}AALA ${chain}${String(residue).padStart(4)}    0.000   0.000   0.000`;
    const pdb = [atomLine('CA', 'A', 64), atomLine('O', 'A', 64), atomLine('N', 'B', 65)].join('\n');
    const atoms = laMotifAtoms(pdb, new Set(['A64'])); expect(atoms.map(a => a.atom)).toEqual(['CA', 'O']);
    expect(laMotifAtomSpec(atoms, new Set([laAtomKey(atoms[1])]))).toBe('A64: [O]'); expect(laMotifAtomSpec(atoms, new Set())).toBe('');
});
