import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { execFileSync } from 'node:child_process';
import { afterEach, expect, it, vi } from 'vitest';
import { rfd3ExecutionKeys, retainedRfd3Draft, retainedRfd3Settings, OtherDeNovoSettings } from '../../src/components/OtherDeNovoSettings';
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
const python = process.env.BMS_TEST_PYTHON ?? 'python3';
const inventories = { rfd3: JSON.parse(execFileSync(python, ['tests/test_de_novo_gap_rfd3.py', 'inventory'], { cwd: '../..', encoding: 'utf8' })) };
function replay(params: unknown) { return JSON.parse(execFileSync(python, ['tests/test_de_novo_gap_rfd3.py', 'replay'], { cwd: '../..', input: JSON.stringify(params), encoding: 'utf8' })); }
let root: Root; let client: QueryClient; const previous = api.defaults.adapter;
let requests: string[] = []; let unexpected: string[] = []; let allowSourceRead = false;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); api.defaults.adapter = previous; vi.restoreAllMocks(); expect(unexpected).toEqual([]); requests = []; unexpected = []; });
async function render(node: React.ReactNode) {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (...args) => { unexpected.push(`fetch:${String(args[0])}`); throw Error('Unexpected fetch'); });
    vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => { unexpected.push('XMLHttpRequest'); throw Error('Unexpected XMLHttpRequest'); });
    api.defaults.adapter = async config => { const name = String(config.url); if (name === '/api/models/protein_modification_experimental') return { data: { engine_references: {} }, config, status: 200, statusText: 'OK', headers: {} }; requests.push(`${name}:${config.params?.generator}`); if (allowSourceRead && name === '/api/files/materialize-structure') throw Error('inert source read'); if (!name.endsWith('/generation-settings')) { unexpected.push(name); throw Error(name); } return { data: inventories.rfd3, config, status: 200, statusText: 'OK', headers: {} }; };
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
}
async function click(text: string) { const item = [...document.querySelectorAll('button,summary')].find(x => x.textContent === text); expect(item, text).toBeTruthy(); await act(async () => (item as HTMLElement).click()); await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); }
async function edit(label: string, value: string) { const item = document.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`[aria-label="${label}"]`)!; expect(item).toBeTruthy(); await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(item), 'value')!.set!.call(item, value); item.dispatchEvent(new Event(item.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); }

function request() { return JSON.parse(document.querySelector('[data-request]')!.textContent!); }
async function reopen(initial: Record<string, unknown>, draft = vi.fn()) {
    await act(async () => root.unmount()); client.clear(); document.body.replaceChildren();
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={initial} />);
}
it('execution discovery exactly matches its explicitly owned sparse projection', () => {
    expect([...rfd3ExecutionKeys, 'dump_trajectories'].sort()).toEqual(Object.keys(inventories.rfd3.execution).sort());
    expect(retainedRfd3Settings({ skip_existing: false, seed: 0, dump_trajectories: false, unknown: 4 })).toEqual({ skip_existing: false });
    expect(retainedRfd3Draft({ conditioning: {}, source_model: 2, generation_mode: 'conditioned', unknown: 4 })).toEqual({ conditioning: {}, source_model: 2, generation_mode: 'conditioned' });
});
it('shows source-qualified execution defaults without writing them into the request', async () => {
    const draft = vi.fn();
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={{ generator: 'rfd3' }} />);
    await click('Sampling'); await click('Execution and native outputs');
    expect(document.querySelector<HTMLInputElement>('[aria-label="skip_existing"]')!.checked).toBe(true);
    expect(document.querySelector<HTMLInputElement>('[aria-label="low_memory_mode"]')!.checked).toBe(false);
    expect(request().params).not.toHaveProperty('skip_existing');
    expect(request().params).not.toHaveProperty('low_memory_mode');
    expect(document.body.textContent).toContain('Inherited reference; not an override');
});

it('shows sampler reference values and resets only the edited sparse override', async () => {
    await render(<ProteinModificationTemplate onBack={() => {}} initialValues={{ generator: 'rfd3' }} />);
    await click('Sampling'); await click('Sampler and classifier-free guidance');
    expect(document.querySelector<HTMLInputElement>('[aria-label="inference_sampler.num_timesteps"]')!.value).toBe('200');
    expect(document.querySelector<HTMLInputElement>('[aria-label="inference_sampler.allow_realignment"]')!.checked).toBe(false);
    expect(request().params).not.toHaveProperty('inference_sampler');
    await edit('inference_sampler.num_timesteps', '250');
    expect(request().params.inference_sampler).toEqual({ num_timesteps: 250 });
    await edit('inference_sampler.num_timesteps presence', 'omitted');
    expect(request().params).not.toHaveProperty('inference_sampler');
    expect(document.querySelector<HTMLInputElement>('[aria-label="inference_sampler.num_timesteps"]')!.value).toBe('200');
});

it('preserves explicit sampler values and updates empty-object custody when drafts change in place', async () => {
    let replace!: (value: Record<string, unknown>) => void;
    let saved: Record<string, unknown> = {};
    function Harness() {
        const [value, setValue] = useState<Record<string, unknown>>({});
        replace = setValue; saved = value;
        return <OtherDeNovoSettings model="rfd3" task="unconditional_monomer" value={value} onChange={setValue} />;
    }
    await render(<Harness />); await click('Sampler and classifier-free guidance');
    await act(async () => replace({ inference_sampler: { num_timesteps: 0, allow_realignment: false, custom: null } }));
    expect(saved.inference_sampler).toEqual({ num_timesteps: 0, allow_realignment: false, custom: null });
    await edit('inference_sampler.num_timesteps presence', 'omitted');
    expect(saved.inference_sampler).toEqual({ allow_realignment: false, custom: null });
    await act(async () => replace({ inference_sampler: {} }));
    await edit('inference_sampler.num_timesteps', '250');
    await edit('inference_sampler.num_timesteps presence', 'omitted');
    expect(saved.inference_sampler).toEqual({});
    await act(async () => replace({}));
    await edit('inference_sampler.num_timesteps', '250');
    await edit('inference_sampler.num_timesteps presence', 'omitted');
    expect(saved).not.toHaveProperty('inference_sampler');
    await act(async () => replace({ inference_sampler: null }));
    expect(saved.inference_sampler).toBeNull();
});

it.each([false, true, undefined])('parent cold reopen and unrelated edit preserve skip_existing=%s', async value => {
    const draft = vi.fn(); const template = vi.fn();
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} onOpenTemplateManager={template} initialValues={{ generator: 'rfd3', ...(value === undefined ? {} : { skip_existing: value }) }} />);
    await click('Template Manager');
    const saved = JSON.parse(JSON.stringify(template.mock.calls.at(-1)![0].currentParams));
    await reopen(saved);
    await click('Sampling'); await click('Execution and native outputs');
    const checkbox = document.querySelector<HTMLInputElement>('[aria-label="low_memory_mode"]')!;
    await act(async () => checkbox.click());
    const params = request().params;
    expect(Object.hasOwn(params, 'skip_existing')).toBe(value !== undefined);
    expect(params.skip_existing).toBe(value);
    const result = replay(params);
    expect(Object.hasOwn(result.request.execution, 'skip_existing')).toBe(value !== undefined);
    expect(result.request.execution.skip_existing).toBe(value);
});
it('edits source-free typed C2 and explicit false/null/zero through parent and cold reopen into preparation', async () => {
    const draft = vi.fn();
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={{ generator: 'rfd3', conditioning: { ori_token: [0, 0, 0], select_hotspots: null, plddt_enhanced: false } }} />);
    await click('Sampling'); await click('Conditions'); await click('Set symmetry');
    await edit('symmetry.id presence', 'value'); await edit('symmetry.id variant', '0'); await edit('symmetry.id', 'C2');
    await edit('symmetry.is_symmetric_motif presence', 'value');
    await edit('symmetry.is_unsym_motif presence', 'value');
    await act(async () => document.querySelector<HTMLInputElement>('[aria-label="symmetry.is_symmetric_motif"]')!.click());
    const expected = { ori_token: [0, 0, 0], select_hotspots: null, plddt_enhanced: false, symmetry: { id: 'C2', is_symmetric_motif: false, is_unsym_motif: null } };
    expect(request().params.conditioning).toEqual(expected);
    const saved = JSON.parse(JSON.stringify(draft.mock.calls.at(-1)![0]));
    await reopen(saved);
    expect(request().params.conditioning).toEqual(expected);
    expect(request().params).not.toHaveProperty('symmetry');
    const prepared = replay(request().params);
    expect(prepared.request.conditioning).toEqual(expected);
    expect(prepared.request).not.toHaveProperty('source');
    expect(prepared.native.generation_0.symmetry).toEqual(expected.symmetry);
});
it('opening conditions never fills an omitted object; explicit empty and literal saved mode survive', async () => {
    const changed = vi.fn();
    await render(<OtherDeNovoSettings generalConditions model="rfd3" task="unconditional_monomer" value={{}} onChange={changed} />);
    await click('Conditions'); expect(changed).not.toHaveBeenCalled();
    await reopen({ generator: 'rfd3', conditioning: {}, generation_mode: 'conditioned' });
    expect(request().params.conditioning).toEqual({});
    expect(request().params.generation_mode).toBe('conditioned');
    expect(() => replay(request().params)).toThrow(); // unchanged public rejection, never translated.
});
it('source-bound nested selectors and exact MODEL survive unrelated editing and cold reopen', async () => {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');
    const { createHash } = await import('node:crypto');
    const directory = mkdtempSync(join(tmpdir(), 'rfd3-source-'));
    const source = join(directory, 'two-models.pdb');
    const bytes = 'MODEL        1\nATOM      1  CA  ALA A  42       1.000   2.000   3.000  1.00 20.00           C\nENDMDL\nMODEL        2\nATOM      1  CA  ALA A  42       4.000   5.000   6.000  1.00 20.00           C\nENDMDL\nEND\n';
    writeFileSync(source, bytes);
    try {
        const draft = vi.fn();
        const initial = { generator: 'rfd3', input_structure: source, source_mapping: 'bms.rfd3.source-map.v1', source_model: 2, conditioning: { select_fixed_atoms: { A42: ['CA'] }, plddt_enhanced: false } };
        await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={initial} />);
        await click('Sampling'); await click('Conditions'); await click('Set select_hotspots'); await edit('select_hotspots', 'A42');
        const saved = JSON.parse(JSON.stringify(draft.mock.calls.at(-1)![0]));
        await reopen(saved);
        const params = request().params;
        expect(params).toMatchObject({ input_structure: source, source_mapping: initial.source_mapping, source_model: 2, conditioning: { ...initial.conditioning, select_hotspots: 'A42' } });
        const prepared = JSON.parse(execFileSync(python, ['tests/test_de_novo_gap_rfd3.py', 'replay'], { cwd: '../..', input: JSON.stringify(params), encoding: 'utf8', env: { ...process.env, BMS_INPUTS: directory } }));
        expect(prepared.request.source.sha256).toBe(createHash('sha256').update(bytes).digest('hex'));
        expect(prepared.request.source_model).toBe(2);
        expect(prepared.native.generation_0.select_hotspots).toBe('A1');
        expect(prepared.native.generation_0.select_fixed_atoms).toEqual({ A1: ['CA'] });
        expect(prepared.receipt.source_mapping.original_sha256).toBe(prepared.request.source.sha256);
    } finally { rmSync(directory, { recursive: true, force: true }); }
});
it('region tasks write the typed redesign request, keep it on reopen, and clear it with the structure', async () => {
    const draft = vi.fn(); allowSourceRead = true;
    await render(<ProteinModificationTemplate onBack={() => {}} onDraftChange={draft} initialValues={{ generator: 'rfd3', input_structure: 'inputs/region.pdb' }} />);
    await click('Choose / edit RFD3 context');
    expect(document.querySelector('[aria-label="Redesign ranges"]')).toBeNull();
    await edit('RFD3 region task', 'partial_diffusion');
    await edit('Redesign ranges', 'A30-40'); await edit('Partial noise exact Å', '5');
    expect(request().params).toMatchObject({ generation_task: 'partial_diffusion', redesign: { design_chains: ['A'], redesign_ranges: 'A30-40' }, conditioning: { partial_t: 5 } });
    await edit('RFD3 region task', 'minimal_insertion');
    expect(request().params.conditioning).not.toHaveProperty('partial_t');
    await edit('Insertion anchor', 'A40'); await edit('Insertion minimum length', '4'); await edit('Insertion maximum length', '6');
    const saved = JSON.parse(JSON.stringify(draft.mock.calls.at(-1)![0]));
    await reopen(saved);
    expect(request().params).toMatchObject({ generation_task: 'minimal_insertion', redesign: { design_chains: ['A'], insertion_anchor: 'A40', insertion_min_length: 4, insertion_max_length: 6 } });
    await click('Choose / edit RFD3 context'); await click('Clear input structure');
    expect(request().params).not.toHaveProperty('generation_task'); expect(request().params).not.toHaveProperty('redesign');
    allowSourceRead = false;
});
it('redesign sourceConditions stays flat and does not adopt the general nested owner', async () => {
    let saved: Record<string, unknown> = {};
    function Harness() { const [value, setValue] = useState<Record<string, unknown>>({ select_buried: 'A42', skip_existing: false }); saved = value; return <OtherDeNovoSettings sourceConditions model="rfd3" task="partial_diffusion" value={value} onChange={setValue} />; }
    await render(<Harness />); await click('Additional source conditions and unindexed atoms');
    await edit('select_buried', 'A43');
    expect(saved).toEqual({ select_buried: 'A43', skip_existing: false });
    expect(saved).not.toHaveProperty('conditioning');
});
it.each([20, 40, 600, 601])('public discovery supplies form bounds without clamping saved %s', async length => {
    await render(<ProteinModificationTemplate onBack={() => {}} initialValues={{ generator: 'rfd3', min_length: length, max_length: length }} />);
    const inputs = [...document.querySelectorAll<HTMLInputElement>('input[type="number"]')].filter(x => x.closest('label')?.textContent?.includes('length'));
    expect(inputs).toHaveLength(2);
    for (const input of inputs) { expect(input.value).toBe(String(length)); expect(input.min).toBe('40'); expect(input.max).toBe('600'); }
    if (length === 40 || length === 600) expect(replay(request().params).request.generation.min_length).toBe(length);
    else expect(request()).toBeNull();
});
