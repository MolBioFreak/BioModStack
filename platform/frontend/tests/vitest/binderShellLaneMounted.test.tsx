import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ submit: vi.fn(async (_payload: any) => ({ data: {} })), templates: [] as any[], save: vi.fn(async () => ({ data: {} })) }));
vi.mock('../../src/lib/api', async original => ({ ...await original<typeof import('../../src/lib/api')>(),
    fetchModelById: vi.fn(async (id: string) => ({ data: { id, params: [] } })), fetchInputPresets: vi.fn(async () => ({ data: [] })),
    listCachedRcsbPdbs: vi.fn(async () => ({ data: { cached: [] } })), fetchExecutionTargets: vi.fn(async () => ({ data: [] })),
    fetchUserTemplates: vi.fn(async () => ({ data: mocks.templates })), createUserTemplate: mocks.save,
    getSabdabFrameworks: vi.fn(async () => ({ data: { frameworks: [] } })),
    submitJob: mocks.submit, completeCurrentLaunchContext: vi.fn(async () => null),
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [] }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: () => <output>Structure renderer</output> }));
import { api } from '../../src/lib/api';
import * as apiTransport from '../../src/lib/api';
import { AntibodyDenovoTemplate } from '../../src/components/AntibodyDenovoTemplate';
import { initialRoundSteps, bc2SourceHandoff, adoptShellSourcePath } from '../../src/lib/binderShell';
import { hydrateBinderRound } from '../../src/lib/binderRound';
import { binderShellError } from '../../src/lib/launchRecipeErrors';
import { BindCraft2Tab, canonicalBindCraft2Entry } from '../../src/components/tabs/BindCraft2Tab';
let root: Root; let client: QueryClient;
const inventory = { upstream_commit: 'pin', fields: { max_trajectories: { native_key: 'max_trajectories', observed_types: ['integer'], has_native_default: false, native_default: null, status: 'typed' } }, presets: {}, paratope_conformations: [], registered_metrics: { filters: {}, losses: {} } };
function Route() { return <output data-route>{useLocation().search}</output>; }
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); mocks.templates = []; });
async function mount(values?: any, props: any = {}, entry = '/submit?template=antibody_denovo&project_id=kept&return_uri=%2Fprojects%2Fkept', retainedPdb = '') {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => retainedPdb, blob: async () => new Blob([retainedPdb], { type: 'chemical/x-pdb' }), json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: inventory }) })));
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><AntibodyDenovoTemplate onBack={() => {}} initialValues={values} {...props} /><Route /></MemoryRouter></QueryClientProvider>));
    await settle();
}
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); }
async function click(label: string) { const button = [...document.querySelectorAll('button')].find(el => el.textContent?.trim() === label); expect(button, label).toBeTruthy(); await act(async () => button!.click()); }
async function edit(label: string, value: string) { const input = document.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!; expect(input).not.toBeNull(); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); }); }
const bc2 = { denovo_generator: 'bindcraft2', bindcraft2_settings: { max_trajectories: 2, targets: [] } };
const firstAtom = 'ATOM      1  CA  ALA A  42       7.000   2.000   3.000  1.00 20.00           C  ';
const secondAtom = firstAtom.replace('ALA', 'GLY').replace('7.000', '9.000');
const secondChainAtom = 'ATOM      2  CA  VAL B  55      11.000   2.000   3.000  1.00 20.00           C  ';
const selectedPdb = `MODEL        2\n${secondAtom}\n${secondChainAtom}\nENDMDL\nEND\n`;
const multiPdb = `MODEL        1\n${firstAtom}\nENDMDL\n${selectedPdb}`;
const multiCif = `data_fixture
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_seq_id
_atom_site.auth_asym_id
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA ALA A 1 A 42 ALA ? 7 2 3 1
ATOM 2 C CA GLY A 1 A 42 GLY ? 9 2 3 2
ATOM 3 C CA VAL B 1 B 55 VAL ? 11 2 3 2
#
`;
function fileTextSupport() {
    vi.stubGlobal('File', class extends File { text() { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
}
function sourceTransport(original: string, derived: string, format: 'pdb' | 'cif' = 'pdb') {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true,
        text: async () => String(url).includes('selected.pdb') ? derived : original,
        json: async () => ({ model_id: 'bindcraft2', settings: inventory }) })));
    return vi.spyOn(api, 'post').mockImplementation(async (url, payload: any) => {
        expect(url).toBe('/api/files/materialize-structure');
        expect(payload).toMatchObject({ output_format: 'pdb', model_number: 2 });
        return { data: { path: 'inputs/selected.pdb', format: 'pdb', sha256: 'fixture-selected',
            native_path: payload.path, native_format: format, native_sha256: 'fixture-original',
            model_numbers: [1, 2], model_number: 2, author_residues: [], source_identity: {}, source_path: payload.path } } as any;
    });
}
async function switchToRf() {
    const format = document.querySelector<HTMLSelectElement>('[aria-label="Binder format / objective"]')!;
    await act(async () => { format.value = 'antibody'; format.dispatchEvent(new Event('change', { bubbles: true })); });
    await click('RFantibody Stack'); await settle();
}
it('ordinary untouched entry opens the existing four-engine chooser; saved BC2 restores URL and context', async () => {
    await mount();
    const chooser = document.querySelector('[aria-label="Binder modality and generation engine"]')!;
    expect(chooser.closest('details')?.open).toBe(true);
    expect(chooser.textContent).toContain('RFantibody'); expect(chooser.textContent).toContain('BoltzGen'); expect(chooser.textContent).toContain('PPIFlow'); expect(chooser.textContent).toContain('BindCraft2');
});
it('retained BC2 re-entry synchronizes URL without touching requested values', async () => {
    await mount(bc2);
    const query = new URLSearchParams(document.querySelector('[data-route]')!.textContent!);
    expect(query.get('engine')).toBe('bindcraft2'); expect(query.get('project_id')).toBe('kept'); expect(query.get('return_uri')).toBe('/projects/kept');
    expect(document.querySelector<HTMLInputElement>('[aria-label="max_trajectories"]')!.value).toBe('2');
});
it('late failed A preview cannot label scientific B or clear its newer spinner; current B failure is actionable', async () => {
    let rejectA!: (e: any) => void; let rejectB!: (e: any) => void;
    vi.spyOn(api, 'post').mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectA = reject; })).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectB = reject; }));
    await mount(bc2); await click('Preview native campaign'); await edit('max_trajectories', '3'); await click('Preview native campaign');
    await act(async () => rejectA({ response: { data: { detail: 'obsolete A' } } }));
    expect(document.body.textContent).not.toContain('obsolete A'); expect(document.body.textContent).toContain('Compiling native preview…');
    await act(async () => rejectB({ response: { data: { detail: 'BC2 native compilation timed out after 120 seconds' } } }));
    expect(document.body.textContent).toContain('BC2 native compilation timed out after 120 seconds'); await click('Open campaign settings');
    expect(document.querySelector('[aria-label="Campaign workspace sections"] button[aria-pressed="true"]')?.textContent).toBe('Generation');
});
it('late success is discarded after editing away and back', async () => {
    let resolve!: (v: any) => void;
    vi.spyOn(api, 'post').mockReturnValue(new Promise(done => { resolve = done; }));
    await mount(bc2); await click('Preview native campaign'); await edit('max_trajectories', '3'); await edit('max_trajectories', '2');
    await act(async () => resolve({ data: { preview_digest: 'stale', effective_settings: {} } }));
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')).toBeNull();
});
it('round-only OFF/ON edits preserve the current native compiler preview and exact sparse settings', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { preview_digest: 'current', effective_settings: { max_trajectories: 2 } } });
    await mount(bc2); await click('Preview native campaign'); await click('Generation');
    const preview = document.querySelector('[aria-label="Compiled native campaign preview"]'); expect(preview).not.toBeNull();
    const enabled = document.querySelector<HTMLInputElement>('[aria-label="Automatic blind complex prediction"]')!;
    await act(async () => enabled.click());
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')).toBe(preview);
    expect(document.querySelector('[aria-label="Initial generation flow"]')?.textContent).not.toContain('Protenix');
    await act(async () => enabled.click());
    expect(document.querySelector('[aria-label="Compiled native campaign preview"]')).toBe(preview);
    expect(post).toHaveBeenCalledTimes(1); expect(post.mock.calls[0][1]).toMatchObject({ params: { bindcraft2_settings: bc2.bindcraft2_settings } });
});

it('round belongs only to Generation/Campaign and reflects actual designer and predictor', async () => {
    await mount(bc2);
    const flow = document.querySelector('[aria-label="Initial generation flow"]')!;
    expect(flow.parentElement?.hidden).toBe(true); await click('Generation'); expect(flow.parentElement?.hidden).toBe(false);
    expect(flow.textContent).toContain('FA-MPNN'); expect(flow.textContent).toContain('Protenix'); expect(flow.textContent).toContain('backbone-only');
    await click('Sources'); expect(document.querySelector('[aria-label="Initial generation flow"]')).toBe(flow);
});
it('unrestricted saved collection browses first and unrelated Load stays open, then deliberately routes separately', async () => {
    mocks.templates = [{ id: 'other', name: 'Other workflow', model_id: 'boltzgen', mode: 'ligand_binder', icon: 'bookmark', color: '#123456', params: { num_designs: 0 } }];
    const load = vi.fn(); await mount(bc2, { onLoadTemplate: load }); await click('Saved campaigns'); await settle();
    expect(document.body.textContent).toContain('My Templates'); expect(document.body.textContent).toContain('Other workflow'); await click('Load');
    expect(document.body.textContent).toContain('belongs to another workflow'); expect(document.body.textContent).toContain('My Templates'); expect(load).not.toHaveBeenCalled();
    await click('Load in its own workflow'); expect(load).toHaveBeenCalledWith(mocks.templates[0]);
});
it('save is separate from browse and keeps the full typed campaign draft', async () => {
    await mount(bc2); await click('Save campaign draft');
    expect(document.body.textContent).toContain('Save as Template');
    const input = document.querySelector<HTMLInputElement>('[placeholder="e.g., My Boltz Config"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Saved typed campaign'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('Save Template'); expect(mocks.save.mock.calls[0][0]).toMatchObject({ model_id: 'bindcraft2', mode: 'campaign', params: { bindcraft2_settings: bc2.bindcraft2_settings, binder_round: { enabled: true } } });
});
it('native engine handoff waits for positive source choice, not stale RF state, and retains full BC2 context', async () => {
    const open = vi.fn(); const settings = { max_trajectories: 2, targets: [{ target_path: 'inputs/exact.cif', name: 'native exact', chains: 'a', hotspots: 'a42A' }], binder_scaffold: 'inputs/framework.pdb' };
    await mount({ denovo_generator: 'bindcraft2', bindcraft2_settings: settings }, { onOpenNativeRoute: open });
    await click('BoltzGen · protein binder'); expect(open).not.toHaveBeenCalled();
    const scaffold = document.querySelector<HTMLInputElement>('[aria-label="Choose destination sources"] input[type=checkbox]')!; await act(async () => scaffold.click());
    await click('Use BC2 target 1: native exact');
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ modelId: 'boltzgen', sources: expect.objectContaining({ target: { path: 'inputs/exact.cif', name: undefined, modelNumber: undefined }, framework: { path: 'inputs/framework.pdb', name: undefined }, bc2: { settings, references: {} } }) }));
    expect(open.mock.calls[0][0].sources.target.chain).toBeUndefined(); expect(open.mock.calls[0][0].sources.target.residues).toBeUndefined();
});
it('RF disabled explanation uses existing target/hotspot causes and opens the source section', async () => {
    await mount({ denovo_generator: 'rfantibody' });
    const reasons = document.querySelector('[aria-label="Missing launch requirements"]')!;
    expect(reasons.textContent).toContain('Choose a target structure'); expect(reasons.textContent).toContain('Select target hotspots'); await click('Generation'); await click('Choose a target structure →');
    expect(document.querySelector('[aria-label="Binder workspace sections"] button[aria-current="page"]')?.textContent).toBe('Target');
});
it('BC2 to RF preserves the current source until positive adoption and does not translate hotspot grammar', async () => {
    vi.stubGlobal('File', class extends File { text() { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    const draft = vi.fn();
    await mount({ denovo_generator: 'bindcraft2', bindcraft2_settings: { targets: [{ target_path: 'inputs/bc2-target.pdb', name: 'Exact BC2', chains: 'a', hotspots: 'a42B' }] } }, { onDraftChange: draft });
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => 'ATOM      1  CA  ALA a  42B      7.000   2.000   3.000  1.00 20.00           C  \nEND\n', json: async () => ({ model_id: 'bindcraft2', settings: inventory }) })));
    const format = document.querySelector<HTMLSelectElement>('[aria-label="Binder format / objective"]')!;
    await act(async () => { format.value = 'antibody'; format.dispatchEvent(new Event('change', { bubbles: true })); });
    await click('RFantibody Stack'); await settle();
    expect(document.querySelector('[aria-label="BindCraft2 source handoff"]')).not.toBeNull();
    expect(draft.mock.calls.at(-1)?.[0].target_source?.path).not.toBe('inputs/bc2-target.pdb');
    await click('Use BC2 target 1: Exact BC2');
    await vi.waitFor(async () => { await settle(); expect(draft.mock.calls.at(-1)?.[0].target_source?.path).toBe('inputs/bc2-target.pdb'); });
    expect(draft.mock.calls.at(-1)?.[0].selected_residues).toEqual([]);
});

it('OFF summary is generation-only, provenance and explicit clears remain lossless', () => {
    const draft = hydrateBinderRound(); const original = structuredClone(draft.binder_round);
    expect(initialRoundSteps('rfantibody', draft.binder_round).map(s => s.title)).toEqual(['RFantibody', 'FA-MPNN', 'Protenix']); expect(draft.binder_round).toEqual(original);
    expect(initialRoundSteps('bindcraft2', { ...original, enabled: false })).toHaveLength(1);
    const settings = { targets: [{ target_path: 'a.pdb' }, { target_path: 'b.cif' }], binder_scaffold: 'scaffold.cif' };
    const handoff = bc2SourceHandoff(settings); expect(handoff.target).toBeUndefined(); expect(handoff.bc2?.settings).toEqual(settings);
    expect(adoptShellSourcePath({ target_pdb: '' }, 'target_pdb', 'other.pdb')).toEqual({ target_pdb: '' });
    expect(binderShellError({ response: { data: { detail: 'max_trajectories is required' } } })).toMatchObject({ section: 'campaign', field: 'max_trajectories' });
});
it('dedicated direct link canonicalization preserves context and all supported action aliases', async () => {
    expect(canonicalBindCraft2Entry('?model=bindcraft2&mode=campaign&project_id=P')).toBe('?project_id=P&template=antibody_denovo&engine=bindcraft2');
    expect(canonicalBindCraft2Entry('?model=bindcraft2&mode=resume')).toBe('?model=bindcraft2&mode=resume');
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => root.render(<MemoryRouter initialEntries={['/submit?project_id=P']}><BindCraft2Tab actions={['resume', 'validate']} /></MemoryRouter>));
    expect(document.querySelector<HTMLAnchorElement>('a')?.href).toContain('template=antibody_denovo'); expect(document.body.textContent).toContain('resume');
});

it.each(['pdb', 'cif'] as const)('BC2 model-2 %s target preserves exact conformation through RF draft reopen and submission', async format => {
    fileTextSupport();
    const draft = vi.fn(); const original = format === 'pdb' ? multiPdb : multiCif;
    // PDB selection can retain MODEL 2; checked CIF conversion can emit an implicit MODEL 1.
    const derived = format === 'pdb' ? selectedPdb : `${secondAtom}\n${secondChainAtom}\nEND\n`;
    const model = format === 'pdb' ? 2 : 1; const path = `inputs/two-models.${format}`;
    await mount({ denovo_generator: 'bindcraft2', bindcraft2_settings: { targets: [{ target_path: path, name: 'Model two' }] },
        bc2_source_references: { 'target:0': { path, source: { name: 'Model two', path, modelNumber: 2 } } } }, { onDraftChange: draft });
    const post = sourceTransport(original, derived, format);
    await switchToRf(); await click('Use BC2 target 1: Model two');
    await vi.waitFor(async () => { await settle(); expect(draft.mock.lastCall?.[0]).toMatchObject({ target_pdb: 'inputs/selected.pdb', target_model_number: model }); });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith('/api/files/materialize-structure', expect.objectContaining({ path, model_number: 2 }));
    expect(draft.mock.lastCall![0].target_source).toMatchObject({ path: 'inputs/selected.pdb', modelNumber: model, derivedFrom: { path, modelNumber: 2 } });
    await vi.waitFor(async () => { await settle(); expect([...document.querySelectorAll('button')].some(node => node.textContent?.trim() === 'Chain A (1 aa)')).toBe(true); });
    await click('Chain A (1 aa)');
    const residue = document.querySelector<HTMLButtonElement>('[aria-label="A42 (GLY)"]');
    expect(residue).not.toBeNull(); expect(document.querySelector('[aria-label="A42 (ALA)"]')).toBeNull();
    await act(async () => residue!.click());
    await vi.waitFor(async () => { await settle(); expect(draft.mock.lastCall![0].selected_residues).toEqual(['A42']); });
    const saved = JSON.parse(JSON.stringify(draft.mock.lastCall![0]));
    await act(async () => root.unmount()); client.clear(); document.body.replaceChildren();
    await mount(saved, { onDraftChange: draft }, undefined, derived);
    await vi.waitFor(async () => { await settle(); expect(document.querySelector('[aria-label="A42 (GLY)"]')?.getAttribute('aria-pressed')).toBe('true'); });
    expect(draft.mock.lastCall![0].target_model_number).toBe(model);
    expect(post).toHaveBeenCalledTimes(1); // Reopen uses retained bytes, not native rematerialization.
    const extraction = vi.spyOn(apiTransport, 'extractChain').mockResolvedValue({ data: { output_path: 'inputs/selected-A.pdb' } } as any);
    const launch = [...document.querySelectorAll<HTMLButtonElement>('button')].find(node => /^Launch (RFantibody Batch|De Novo Nanobody Pipeline)/.test(node.textContent?.trim() || ''))!;
    expect(launch.disabled).toBe(false); await act(async () => launch.click());
    await vi.waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(1));
    expect(extraction).toHaveBeenCalledExactlyOnceWith('inputs/selected.pdb', 'A', undefined, model);
    expect(mocks.submit.mock.calls[0][0]).toMatchObject({ params: { target_pdb: 'inputs/selected-A.pdb', target_model_number: model, epitope_residues: 'A42' } });
});

it('BC2 model-2 PDB scaffold is extracted once and retained as the actual RF framework', async () => {
    fileTextSupport(); const draft = vi.fn(); const path = 'inputs/two-models.pdb';
    await mount({ denovo_generator: 'bindcraft2', bindcraft2_settings: { binder_scaffold: path },
        bc2_source_references: { scaffold: { path, source: { name: 'Model two scaffold', path, modelNumber: 2 } } } }, { onDraftChange: draft });
    const post = sourceTransport(multiPdb, selectedPdb);
    await switchToRf(); await click('Use BC2 scaffold as RF framework');
    await vi.waitFor(async () => { await settle(); expect(draft.mock.lastCall?.[0]).toMatchObject({ framework_pdb: 'inputs/selected.pdb', custom_framework_path: 'inputs/selected.pdb' }); });
    expect(draft.mock.lastCall![0].custom_framework_source).toMatchObject({ path: 'inputs/selected.pdb', modelNumber: 2, derivedFrom: { path, modelNumber: 2 } });
    expect(post).toHaveBeenCalledTimes(1);
    const saved = JSON.parse(JSON.stringify(draft.mock.lastCall![0]));
    await act(async () => root.unmount()); client.clear(); document.body.replaceChildren();
    await mount(saved, { onDraftChange: draft }, undefined, selectedPdb);
    await settle(); expect(draft.mock.lastCall![0].custom_framework_path).toBe('inputs/selected.pdb');
    expect(post).toHaveBeenCalledTimes(1);
});
