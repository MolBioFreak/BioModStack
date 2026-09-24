import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_body: any) => ({ data: {} })), iteration: vi.fn(async (_body: any) => ({ data: {} })), select: null as any }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(), fetchInputPresets: vi.fn(async () => ({ data: [] })), listCachedRcsbPdbs: vi.fn(async () => ({ data: { cached: [] } })), uploadImmutableFile: vi.fn(async () => ({ data: { path: 'inputs/protein_local_redesign/source.pdb' } })), uploadFile: vi.fn(async () => ({ data: { path: 'source.pdb' } })), submitJob: mocks.submit, fetchExecutionTargets: vi.fn(async () => ({ data: [] })), launchAntibodyIteration: mocks.iteration, completeCurrentLaunchContext: vi.fn(async () => null) }));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: ({ currentParams, onSelect }: any) => { mocks.select = onSelect; return <output data-saved>{JSON.stringify(currentParams)}</output>; } }));
vi.mock('../../src/components/FrameworkBrowser', () => ({ FrameworkBrowser: () => null }));

vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => null }));
vi.mock('../../src/components/Rfd3SourceSelector', () => ({ Rfd3SourceSelector: () => null }));
import { api } from '../../src/lib/api';
import { AntibodyDenovoTemplate } from '../../src/components/AntibodyDenovoTemplate';
import { BinderGeneratorChooser } from '../../src/components/BinderGeneratorChooser';
import { BindCraft2Settings } from '../../src/components/BindCraft2Settings';
import { QualitySettingsPanel } from '../../src/components/QualitySettingsPanel';
import { PRESETS } from '../../src/components/qualitySettingsLogic';
let root: Root; let client: QueryClient;
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function mount(node: React.ReactNode, refinement = false) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    const entry = refinement ? { pathname: '/submit', state: { refinementMode: true, sourceJobId: 'source', selectedDesignIds: ['design'] } } : '/submit';
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}>{node}</MemoryRouter></QueryClientProvider>));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
}
async function click(label: string) { const button = [...document.querySelectorAll('button')].find(el => el.textContent?.includes(label)); expect(button, label).toBeTruthy(); await act(async () => button!.click()); }

it('shared quality panel keeps ThermoMPNN visible by default for other callers', async () => {
    const change = vi.fn();
    await mount(<QualitySettingsPanel settings={PRESETS.balanced} onSettingsChange={change} />);
    await click('Quality Settings');
    expect(document.body.textContent).toContain('(stability scoring)');
    expect(change).not.toHaveBeenCalled();
});
it('new binder quality controls omit ThermoMPNN without changing saved scientific defaults', async () => {
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ initial_orchestration_qc: true }} />);
    const before = JSON.parse(document.querySelector('[data-saved]')!.textContent!);
    await click('Quality Settings');
    expect(document.body.textContent).not.toContain('(stability scoring)');
    expect(JSON.parse(document.querySelector('[data-saved]')!.textContent!)).toEqual(before);
});
it.each([
    { run_thermompnn: true }, { run_thermompnn: false },
    { run_stability_scoring: true }, { run_stability_scoring: false },
    { quality_settings: { run_thermompnn: true, thermompnn_max_ddg: 0 } },
    { qualitySettings: { run_thermompnn: false } },
    { quality_settings: { run_stability_scoring: false } },
    { qualitySettings: { run_stability_scoring: true } },
])('explicit historical Thermo flags remain visible on clone and modal load: %j', async flags => {
    const params = { initial_orchestration_qc: true, ...flags };
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={params} />);
    await click('Quality Settings');
    expect(document.body.textContent).toContain('(stability scoring)');
    const saved = JSON.parse(document.querySelector('[data-saved]')!.textContent!);
    await act(async () => mocks.select({ name: 'new request', params: { initial_orchestration_qc: true } }));
    expect(document.body.textContent).not.toContain('(stability scoring)');
    await act(async () => mocks.select({ name: 'legacy request', params }));
    expect(document.body.textContent).toContain('(stability scoring)');
    const reopened = JSON.parse(document.querySelector('[data-saved]')!.textContent!);
    expect(reopened.run_thermompnn).toBe(saved.run_thermompnn);
    expect(reopened.run_stability_scoring).toBe(saved.run_stability_scoring);
    expect(reopened.quality_settings).toEqual(saved.quality_settings);
});

const inventory = { upstream_commit: 'pin', fields: {
    max_trajectories: { native_key: 'max_trajectories', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' as const },
    trajectory_only: { native_key: 'trajectory_only', observed_types: ['boolean'], has_native_default: true, native_default: true, status: 'typed' as const },
    relax_steps: { native_key: 'relax_steps', observed_types: ['integer'], has_native_default: false, native_default: null, runtime_fallback: 200, status: 'typed' as const },
}, presets: {}, paratope_conformations: [], registered_metrics: { filters: {}, losses: {} } };
function discovery() {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: inventory }) })));
}
async function edit(label: string, value: string) {
    const input = document.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
    expect(input).not.toBeNull();
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); });
}
it('actual BC2 authoring previews server payload, submits digest, and reopens untouched requested values', async () => {
    discovery();
    const settings = { max_trajectories: 4, trajectory_only: false, targets: [], aa_bias: { A: 0 }, relax_steps: 0, filters: {}, binder_name: null };
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { preview_digest: 'compiled-digest', requested_settings: settings, effective_settings: { resolved: true }, warnings: ['native notice'] } });
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ model_id: 'bindcraft2', mode: 'campaign', bindcraft2_settings: settings }} />);
    expect(document.querySelector('[aria-label="trajectory_only"]')?.getAttribute('type')).toBe('checkbox');
    expect((document.querySelector('[aria-label="trajectory_only"]') as HTMLInputElement).checked).toBe(false);
    expect(document.body.textContent).not.toContain('CDR Design');
    await click('Preview native campaign');
    expect(post).toHaveBeenCalledWith('/api/models/bindcraft2/campaign/preview', { model_id: 'bindcraft2', mode: 'campaign', params: { bindcraft2_settings: settings } });
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')?.textContent).toContain('native notice');
    await click('Launch BindCraft2 campaign');
    expect(mocks.submit.mock.calls[0][0]).toMatchObject({ model_id: 'bindcraft2', mode: 'campaign', params: { bindcraft2_settings: settings, bc2_preview_digest: 'compiled-digest' } });
    const saved = JSON.parse(document.querySelector('[data-saved]')!.textContent!);
    expect(saved.bindcraft2_settings).toEqual(settings);
    await act(async () => mocks.select({ name: 'reopened', model_id: 'bindcraft2', mode: 'campaign', params: saved }));
    expect(JSON.parse(document.querySelector('[data-saved]')!.textContent!).bindcraft2_settings).toEqual(settings);
    await edit('max_trajectories', '9');
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')).toBeNull();
    const launch = [...document.querySelectorAll('button')].find(button => button.textContent === 'Launch BindCraft2 campaign')!;
    expect(launch.disabled).toBe(true);
    await click('Preview native campaign');
    expect(post.mock.calls.at(-1)?.[1]).toMatchObject({ params: { bindcraft2_settings: { ...settings, max_trajectories: 9 } } });
});
it('a preview arriving after a scientific edit cannot bind the newer request', async () => {
    discovery();
    let resolve!: (value: any) => void;
    vi.spyOn(api, 'post').mockReturnValue(new Promise(done => { resolve = done; }));
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialValues={{ denovo_generator: 'bindcraft2', bindcraft2_settings: { max_trajectories: 2 } }} />);
    await click('Preview native campaign');
    await edit('max_trajectories', '3');
    await edit('max_trajectories', '2');
    await act(async () => resolve({ data: { preview_digest: 'stale', effective_settings: {} } }));
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')).toBeNull();
    expect(mocks.submit).not.toHaveBeenCalled();
});
it('four-generator choice routes initial PPIFlow natively and keeps RFD3 separate', async () => {
    const select = vi.fn(); const open = vi.fn();
    await mount(<BinderGeneratorChooser generator="rfantibody" onSelect={select} onOpenNativeRoute={open} />);
    const choose = async (value: string) => { const input = document.querySelector<HTMLSelectElement>('[aria-label="Binder format / objective"]')!; await act(async () => { input.value = value; input.dispatchEvent(new Event('change', { bubbles: true })); }); };
    await choose('protein');
    expect(select).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain('RFD3');
    await click('PPIFlow · protein binder generation'); expect(open).toHaveBeenLastCalledWith({ modelId: 'ppiflow', mode: 'protein_binder' });
    await click('BoltzGen · protein binder'); expect(open).toHaveBeenLastCalledWith({ modelId: 'boltzgen', mode: 'protein_binder' });
    await click('BindCraft2 campaign'); expect(select).toHaveBeenLastCalledWith('bindcraft2');
    await choose('ligand');
    await click('BoltzGen · ligand binder'); expect(open).toHaveBeenLastCalledWith({ modelId: 'boltzgen', mode: 'ligand_binder' });
    await click('BoltzGen · nucleotide binder'); expect(open).toHaveBeenLastCalledWith({ modelId: 'boltzgen', mode: 'ntp_binder' });
    await choose('seeded'); await click('PPIFlow · retained partial flow'); expect(select).toHaveBeenLastCalledWith('ppiflow');
});
it('grouped native controls keep advanced relaxation reachable and distinguish omission from explicit zero', async () => {
    let latest: any;
    function Form() { const [value, setValue] = React.useState({ max_trajectories: 2, relax_steps: 0, trajectory_only: false }); latest = value; return <BindCraft2Settings inventory={inventory} value={value} onChange={next => setValue(next as typeof value)} />; }
    await mount(<Form />);
    const relaxation = [...document.querySelectorAll('summary')].find(node => node.textContent?.startsWith('Native relaxation'));
    expect(relaxation).toBeTruthy();
    await act(async () => relaxation!.click());
    expect((document.querySelector('[aria-label="relax_steps"]') as HTMLInputElement).value).toBe('0');
    const reset = document.querySelector<HTMLButtonElement>('[aria-label="Reset relax_steps"]')!;
    await act(async () => reset.click());
    expect(Object.hasOwn(latest, 'relax_steps')).toBe(false);
    expect(latest.trajectory_only).toBe(false);
    expect((document.querySelector('[aria-label="relax_steps"]') as HTMLInputElement).value).toBe('');
});

it('every source-typed top-level scientific inventory field has a mounted control', async () => {
    const native = JSON.parse(readFileSync('../api/config/models/bindcraft2_native_inventory.json', 'utf8'));
    const overlay = JSON.parse(readFileSync('../api/config/models/bindcraft2_typed_inventory.json', 'utf8'));
    for (const [key, descriptor] of Object.entries(overlay.top_level_resolved) as Array<[string, any]>) {
        native.fields[key] = { ...native.fields[key], ...descriptor, status: 'typed' };
    }
    const internal = new Set(['project_folder', 'resume', 'gpu_ids', 'auto_multi_gpu', 'design_workers', 'workers_per_gpu', 'max_workers_per_gpu', 'worker_launch_stagger', 'compile_next_length']);
    await mount(<BindCraft2Settings inventory={native} value={{}} onChange={() => {}} />);
    const fields = Object.entries(native.fields).filter(([key, descriptor]: [string, any]) => !internal.has(key) && descriptor.status === 'typed');
    const cards = [...document.querySelectorAll('section[aria-label="BindCraft2 settings"] [data-bc2-field]')];
    expect(cards.map(node => node.getAttribute('data-bc2-field')).sort()).toEqual(fields.map(([key]) => key).sort());
    for (const [key] of fields) {
        const card = cards.find(node => node.getAttribute('data-bc2-field') === key);
        expect(card, key).toBeTruthy();
        expect(card!.querySelector('input, select, button'), key).not.toBeNull();
        expect(card!.textContent, key).not.toContain('Unsupported typed control');
    }
    expect(fields.length).toBeGreaterThan(200);
});
it('registered list parameters and entry prediction state have typed controls', async () => {
    const full = { ...inventory, fields: { ...inventory.fields, losses: { native_key: 'losses', observed_types: ['object'], has_native_default: true, native_default: { binder_pae: { params: { domain_ids: [] } } }, status: 'typed' as const } }, registered_metrics: { filters: {}, losses: { binder_pae: { params: { domain_ids: { default_literal: [], source_default: '()', request_types: ['array'] } } } } } };
    let latest: any;
    function Form() { const [value, setValue] = React.useState({}); latest = value; return <BindCraft2Settings inventory={full} value={value} onChange={setValue} />; }
    await mount(<Form />);
    const configure = [...document.querySelectorAll('[data-bc2-field="losses"] summary')].find(node => node.textContent?.startsWith('Configure '));
    expect(configure).toBeTruthy();
    await act(async () => (configure as HTMLElement).click());
    const add = document.querySelector<HTMLButtonElement>('[role="group"][aria-label="losses.binder_pae.params.domain_ids"] button');
    expect(add).not.toBeNull();
    await act(async () => add!.click());
    await edit('losses.binder_pae.params.domain_ids.0', 'A');
    await edit('losses.binder_pae.prediction_state', 'complex');
    expect(latest.losses.binder_pae).toEqual({ prediction_state: 'complex', params: { domain_ids: ['A'] } });
});


it('saved ESMFold2 survives modal hydration and reports the same live Project draft', async () => {
    const drafts = vi.fn();
    await mount(<AntibodyDenovoTemplate onBack={() => {}} onDraftChange={drafts} initialDraft={{ structure_validator: 'boltz2' }} />);
    await act(async () => mocks.select({ name: 'saved ESMFold2', params: { structure_validator: 'esmfold2', initial_orchestration_validation: true, extra_native_setting: { enabled: false, count: 0, names: [], missing: null } } }));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    const saved = JSON.parse(document.querySelector('[data-saved]')!.textContent!);
    expect(saved.structure_validator).toBe('esmfold2');
    expect(drafts.mock.calls.at(-1)?.[0]).toEqual(saved);
    expect(saved.extra_native_setting).toEqual({ enabled: false, count: 0, names: [], missing: null });
});

it('workspace navigation retains actual source acquisition tools and emits no scientific changes', async () => {
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialDraft={{ job_name: 'kept draft', boltzgen_step_scale: 0, boltzgen_filter_biased: false }} />);
    await click('RCSB');
    const sourceInput = document.querySelector<HTMLInputElement>('input[placeholder="4I27"]')!;
    expect(sourceInput).not.toBeNull();
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(sourceInput, '1ABC'); sourceInput.dispatchEvent(new Event('input', { bubbles: true })); });
    // The real picker stays mounted, including its active acquisition tab.
    const before = JSON.parse(document.querySelector('[data-saved]')!.textContent!);

    const nav = document.querySelector('nav[aria-label="Binder workspace sections"]')!;
    await act(async () => [...nav.querySelectorAll('button')].find(button => button.textContent === 'Expert')!.click());
    await act(async () => [...nav.querySelectorAll('button')].find(button => button.textContent === 'Target')!.click());
    expect(sourceInput.isConnected).toBe(true);
    expect(sourceInput.value).toBe('1ABC');
    expect(JSON.parse(document.querySelector('[data-saved]')!.textContent!)).toEqual(before);
    expect(before.boltzgen_step_scale).toBe(0);
    expect(before.boltzgen_filter_biased).toBe(false);
});


it('a late saved-target load cannot undo deliberate clear in the real source picker', async () => {
    let resolve!: (response: any) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise(done => { resolve = done; })));
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialDraft={{ target_source: { type: 'preset', path: 'inputs/saved-target.pdb', name: 'Saved target' }, selected_chain: 'a', selected_residues: ['a42A'] }} />);
    expect(document.body.textContent).toContain('Saved target');
    await click('Clear');
    await act(async () => resolve({ ok: true, blob: async () => new Blob(['END\n']) }));
    const saved = JSON.parse(document.querySelector('[data-saved]')!.textContent!);
    expect(saved.target_source).toBeNull();
    expect(saved.selected_residues).toEqual([]);
    expect(saved.selected_chain).toBeNull();
    expect(document.body.textContent).not.toContain('Saved target');
});

it('native engine handoff preserves materialized source and role selection without a seed request', async () => {
    const open = vi.fn();
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    await mount(<AntibodyDenovoTemplate onBack={() => {}} onOpenNativeRoute={open} initialDraft={{ target_source: { type: 'preset', path: 'inputs/saved-target.cif', name: 'Saved target' }, selected_chain: 'a', selected_residues: ['a42A'], target_model_number: 2 }} />);
    await click('PPIFlow · antibody generation');
    expect(open).toHaveBeenCalledWith({ modelId: 'ppiflow', mode: 'antibody_binder', sources: { target: { reference: expect.objectContaining({ path: 'inputs/saved-target.cif', type: 'preset' }), path: 'inputs/saved-target.cif', name: 'Saved target', source: 'preset', modelNumber: 2, chain: 'a', residues: ['a42A'] } } });
    expect(mocks.submit).not.toHaveBeenCalled();
});


it('changing generation engines keeps the populated source tool mounted and inactive settings intact', async () => {
    discovery();
    await mount(<AntibodyDenovoTemplate onBack={() => {}} initialDraft={{ boltzgen_step_scale: 0, boltzgen_filter_biased: false }} />);
    await click('RCSB');
    const sourceInput = document.querySelector<HTMLInputElement>('input[placeholder="4I27"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(sourceInput, '1ABC'); sourceInput.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('BindCraft2 campaign');
    expect(sourceInput.isConnected).toBe(true);
    await click('RFantibody Stack');
    expect(sourceInput.isConnected).toBe(true);
    expect(sourceInput.value).toBe('1ABC');
    const draft = JSON.parse(document.querySelector('[data-saved]')!.textContent!);
    expect(draft.boltzgen_step_scale).toBe(0);
    expect(draft.boltzgen_filter_biased).toBe(false);
    expect(draft.denovo_generator).toBe('rfantibody');
});
