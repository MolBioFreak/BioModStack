import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// Only transport and the WebGL renderer are replaced. Target/source widgets,
// sequence selection, the Mol* compatibility boundary and campaign composition are real.
const state = vi.hoisted(() => ({ submit: vi.fn(async (_request: unknown) => ({ data: {} })), load: null as null | ((template: any) => void), scenes: [] as any[] }));
vi.mock('../../src/lib/api', async original => ({
    ...await original<typeof import('../../src/lib/api')>(),
    materializeStructureTarget: vi.fn(async (source: { file?: File; path?: string; name: string }) => source.path || `inputs/campaign/${source.file?.name || source.name}`),
    uploadFile: vi.fn(async (_folder: string, file: File) => ({ data: { path: `inputs/campaign/${file.name}` } })),
    uploadImmutableFile: vi.fn(async (_folder: string, file: File) => ({ data: { path: `inputs/campaign/${file.name}` } })),
    fetchInputPresets: vi.fn(async () => ({ data: [] })),
    listCachedRcsbPdbs: vi.fn(async () => ({ data: { cached: [] } })),
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })),
    submitJob: state.submit,
    completeCurrentLaunchContext: vi.fn(async () => null),
}));
vi.mock('../../src/components/useLiveGpuCatalog', () => ({ useLiveGpuCatalog: () => ({ gpuOptions: [], isLoading: false, isError: false }) }));
vi.mock('../../src/components/ModelIntegrationControl', () => ({ ModelIntegrationControl: () => null, useModelIntegrationConfig: () => ({ data: { workflows: {} }, isFetching: false, isError: false }) }));
vi.mock('../../src/components/TemplateManagerModal', () => ({ TemplateManagerModal: ({ currentParams, onSelect }: any) => { state.load = onSelect; return <output data-campaign-saved>{JSON.stringify(currentParams)}</output>; } }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: any) => {
    state.scenes.push(props);
    return <div data-scene><output data-selected-residues>{JSON.stringify(props.residueSelections)}</output>
        <button type="button" onClick={() => props.onResidueClick?.({ documentId: 'primary', authAsymId: 'A', authSeqId: 2 })}>Pick A2 in test renderer</button>
    </div>;
} }));

import { api, fetchInputPresets, materializeStructureTarget } from '../../src/lib/api';
import { AntibodyDenovoTemplate } from '../../src/components/AntibodyDenovoTemplate';

// Explicit synthetic coordinates for UI selection, not native inference evidence.
const PDB = 'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C  \nATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00 20.00           C  \nTER\nEND\n';
const field = (native_key: string, type: string, native_default?: unknown) => ({ native_key, observed_types: [type], has_native_default: native_default !== undefined, native_default: native_default ?? null, status: 'typed' as const });
const inventory = {
    upstream_commit: 'ui-fixture',
    fields: {
        modality: field('modality', 'array'), core: field('core', 'array'), target: field('target', 'array'),
        targets: field('targets', 'array'), binder_scaffold: field('binder_scaffold', 'string'),
        max_trajectories: field('max_trajectories', 'integer'), trajectory_only: field('trajectory_only', 'boolean', true),
        binder_lengths: field('binder_lengths', 'array'), losses: field('losses', 'object', {}),
    },
    presets: { modality: { binder: {}, VHH: {} }, core: {}, target: {} },
    paratope_conformations: [],
    registered_metrics: { filters: {}, losses: { induced_fit_interface: { params: {
        interface_mask: { default_literal: null, source_default: 'None', request_types: ['array', 'null'] },
    } } } },
};
let root: Root | undefined;
let client: QueryClient;
function saved() { return JSON.parse(document.querySelector('[data-campaign-saved]')!.textContent!); }
function getButton(text: string) { return [...document.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent?.trim() === text); }
async function click(text: string) { const button = getButton(text); expect(button, text).toBeTruthy(); await act(async () => button!.click()); }
async function mount(initialValues?: Record<string, unknown>) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit']}><AntibodyDenovoTemplate onBack={() => {}} initialValues={initialValues} /></MemoryRouter></QueryClientProvider>));
}
beforeEach(() => {
    state.scenes = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request) => String(url).includes('/native-settings')
        ? { ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: inventory }) }
        : { ok: true, text: async () => PDB, blob: async () => new Blob([PDB], { type: 'chemical/x-pdb' }), json: async () => ({}) }));
    vi.spyOn(api, 'post').mockImplementation(async (_url, body: any) => ({ data: { preview_digest: 'ui-preview', requested_settings: body.params.bindcraft2_settings, effective_settings: body.params.bindcraft2_settings } }));
    if (!Blob.prototype.text) Object.defineProperty(Blob.prototype, 'text', { configurable: true, value: function () { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
});
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it('keeps an actual source and residue selection when moving from the existing workflow into BC2', async () => {
    await mount();
    const input = document.querySelector<HTMLInputElement>('input[type="file"][accept*=".pdb"]')!;
    expect(input).not.toBeNull();
    expect(input.accept.split(',')).toEqual(expect.arrayContaining(['.pdb', '.cif', '.mmcif']));
    const file = new File([PDB], 'selected-target.pdb', { type: 'chemical/x-pdb' });
    await act(async () => { Object.defineProperty(input, 'files', { configurable: true, value: [file] }); input.dispatchEvent(new Event('change', { bubbles: true })); });
    await vi.waitFor(() => expect(document.querySelector('button[title="A2 (GLY)"]')).not.toBeNull());
    await act(async () => (document.querySelector('button[title="A2 (GLY)"]') as HTMLButtonElement).click());
    await click('Target 3D');
    await vi.waitFor(() => expect(state.scenes.some(scene => scene.residueSelections?.some((residue: any) => residue.authAsymId === 'A' && residue.authSeqId === 2))).toBe(true));
    const modality = document.querySelector<HTMLSelectElement>('[aria-label="Binder format / objective"]')!;
    await act(async () => { modality.value = 'protein'; modality.dispatchEvent(new Event('change', { bubbles: true })); });
    await click('BindCraft2 campaign');
    await vi.waitFor(() => expect(saved().bindcraft2_settings?.targets?.[0]?.hotspots).toBe('A2'));
    const transferred = saved().bindcraft2_settings.targets[0];
    expect(transferred.target_path).toContain('selected-target');
    expect(transferred.chains).toBe('A');
    expect(document.querySelector('[aria-label="BindCraft2 campaign"]')).not.toBeNull();
    await click('Preview native campaign');
    expect(api.post).toHaveBeenLastCalledWith('/api/models/bindcraft2/campaign/preview', expect.objectContaining({ params: { bindcraft2_settings: expect.objectContaining({ targets: [transferred] }) } }));
    await click('Launch BindCraft2 campaign');
    expect(state.submit).toHaveBeenLastCalledWith(expect.objectContaining({ model_id: 'bindcraft2', mode: 'campaign', params: expect.objectContaining({ bindcraft2_settings: expect.objectContaining({ targets: [transferred] }), bc2_preview_digest: 'ui-preview' }) }));
});

it('reopens source-bound native values and the custom mask without inventing overrides', async () => {
    const settings = { modality: ['binder'], max_trajectories: 2, trajectory_only: false, targets: [{ name: 'saved target', target_path: 'inputs/saved.pdb', chains: 'A', hotspots: 'A2', coldspots: '', objective: 'bind', weight: 0 }], losses: { induced_fit_interface: { params: { interface_mask: [0, 0.25, 1] } } } };
    await mount({ model_id: 'bindcraft2', mode: 'campaign', job_name: 'saved native campaign', bindcraft2_settings: settings });
    await vi.waitFor(() => expect(document.querySelector('[aria-label="BindCraft2 settings"]')).not.toBeNull());
    expect(saved().bindcraft2_settings).toEqual(settings);
    await click('Preview native campaign');
    expect(api.post).toHaveBeenLastCalledWith('/api/models/bindcraft2/campaign/preview', { model_id: 'bindcraft2', mode: 'campaign', params: { bindcraft2_settings: settings } });
    await act(async () => state.load?.({ name: 'reopened', model_id: 'bindcraft2', mode: 'campaign', params: { bindcraft2_settings: settings } }));
    expect(saved().bindcraft2_settings).toEqual(settings);
    expect(document.querySelector('[aria-label="Campaign review and launch"]')).not.toBeNull();
    // Navigation changes presentation only: controls stay mounted and the preview
    // remains valid while the same scientific request is shown in another section.
    const budget = document.querySelector('[aria-label="max_trajectories"]')!;
    expect(budget.closest('[hidden]')).not.toBeNull();
    await click('Campaign');
    expect(budget.closest('[hidden]')).toBeNull();
    await click('Objectives');
    expect(document.querySelector('[aria-label="Design objectives"]')?.hasAttribute('hidden')).toBe(false);
    await click('Expert');
    expect(document.querySelector('[aria-label="Expert settings"]')?.hasAttribute('hidden')).toBe(false);
    await click('Binder design');
    expect(document.querySelector('[aria-label="Format & profiles"]')?.hasAttribute('hidden')).toBe(false);
    await click('Sources');
    expect(document.querySelector('[aria-label="max_trajectories"]')).toBe(budget);
    expect(saved().bindcraft2_settings).toEqual(settings);
    expect(getButton('Launch BindCraft2 campaign')?.disabled).toBe(false);
});

it('native CIF URL handoff retains author chains, model bytes and derived source identity through campaign save/reopen', async () => {
    // Reuse the source-owner parser fixture (not native inference evidence).
    const cif = `data_fixture\n#\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n_atom_site.label_atom_id\n_atom_site.label_comp_id\n_atom_site.label_asym_id\n_atom_site.label_seq_id\n_atom_site.auth_asym_id\n_atom_site.auth_seq_id\n_atom_site.auth_comp_id\n_atom_site.pdbx_PDB_ins_code\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n_atom_site.pdbx_PDB_model_num\nATOM 1 C CA ALA X 1 AA 10 ALA ? 1.123456 2 3 1\nATOM 2 C CA GLY Y 1 BA 10 GLY A 4 5 6 1\nATOM 3 C CA SER X 2 AA 20 SER ? 7 8 9 2\n#\n`;
    vi.mocked(fetchInputPresets).mockResolvedValue({ data: [{ id: 'cif', name: 'Native CIF', path: 'inputs/native.cif', description: 'Parser fixture', category: 'test' }] } as any);
    vi.mocked(fetch).mockImplementation(async url => String(url).includes('/native-settings')
        ? { ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: inventory }) } as Response
        : { ok: true, text: async () => cif, blob: async () => new Blob([cif]), json: async () => ({}) } as Response);
    await mount();
    await click('Presets');
    await vi.waitFor(() => expect(getButton('Native CIF')).toBeTruthy());
    await click('Native CIF');
    await vi.waitFor(() => expect(document.querySelector('button[title="BA10A (GLY)"]')).not.toBeNull());
    expect(saved().target_source.path).toBe('inputs/native.cif');
    await click('Target 3D');
    await vi.waitFor(() => expect(state.scenes.some(scene => scene.format === 'cif')).toBe(true));
    const model = [...document.querySelectorAll('select')].find(select => [...select.options].some(option => option.textContent?.includes('Model 2')))!;
    expect(model).toBeTruthy();
    await act(async () => { model.value = '2'; model.dispatchEvent(new Event('change', { bubbles: true })); });
    await vi.waitFor(() => expect(document.querySelector('button[title="AA20 (SER)"]')).not.toBeNull());
    const modality = document.querySelector<HTMLSelectElement>('[aria-label="Binder format / objective"]')!;
    await act(async () => { modality.value = 'protein'; modality.dispatchEvent(new Event('change', { bubbles: true })); });
    await click('BindCraft2 campaign');
    await vi.waitFor(() => expect(saved().bindcraft2_settings?.targets?.[0]?.target_path).toBe('inputs/campaign/model-2.cif'));
    const acquisition = vi.mocked(materializeStructureTarget).mock.calls.find(([source]) => source.file?.name === 'model-2.cif')![0];
    const consumed = await acquisition.file!.text();
    expect(consumed).toContain('_atom_site.');
    expect(consumed).toContain('SER');
    expect(consumed).not.toContain('1.123456');
    expect(saved().bc2_source_references['target:0']).toMatchObject({ path: 'inputs/campaign/model-2.cif', source: { modelNumber: 2, derivedFrom: { path: 'inputs/native.cif' } } });
    const draft = saved();
    await act(async () => state.load?.({ name: 'Reopened CIF', model_id: 'bindcraft2', mode: 'campaign', params: draft }));
    expect(saved().bc2_source_references).toEqual(draft.bc2_source_references);
    expect(saved().bindcraft2_settings).toEqual(draft.bindcraft2_settings);
});
