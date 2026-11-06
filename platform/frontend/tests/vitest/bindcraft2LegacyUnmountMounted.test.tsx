import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// Only transport and the WebGL renderer are replaced. Target/source widgets,
// sequence selection, the Mol* compatibility boundary and campaign composition are real.
const state = vi.hoisted(() => ({ submit: vi.fn(async (_request: unknown) => ({ data: {} })), load: null as null | ((template: any) => void), scenes: [] as any[], draft: undefined as any }));
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
    subbatch_size: { native_key: 'subbatch_size', observed_types: ['string', 'integer', 'null'], has_native_default: true, native_default: 'auto', recommended_default: null, recommended_default_reason: 'No chunking by default for new BMS campaigns; uses more VRAM.', status: 'typed' as const },
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
function saved() { return state.draft; }
async function settleDraft() { await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); expect(state.draft).toBeDefined(); }); }
function getButton(text: string) { return [...document.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent?.trim() === text); }
async function click(text: string) { const button = getButton(text); expect(button, text).toBeTruthy(); await act(async () => button!.click()); await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); }
async function mount(initialValues?: Record<string, unknown>) {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root!.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/submit']}><AntibodyDenovoTemplate onBack={() => {}} initialValues={initialValues} onDraftChange={draft => { state.draft = draft; }} /></MemoryRouter></QueryClientProvider>));
}
const originalAdapter = api.defaults.adapter;
beforeEach(() => {
    api.defaults.adapter = async config => ({ data: config.url?.includes("models") ? { params: [], modes: [] } : [], status: 200, statusText: "OK", headers: {}, config });
    state.scenes = []; state.draft = undefined;
    vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request) => String(url).includes('/native-settings')
        ? { ok: true, json: async () => ({ model_id: 'bindcraft2', launch_available: true, settings: inventory }) }
        : { ok: true, text: async () => PDB, blob: async () => new Blob([PDB], { type: 'chemical/x-pdb' }), json: async () => ({}) }));
    vi.spyOn(api, 'post').mockImplementation(async (_url, body: any) => ({ data: { preview_digest: 'ui-preview', requested_settings: body.params.bindcraft2_settings, effective_settings: body.params.bindcraft2_settings } }));
    if (!Blob.prototype.text) Object.defineProperty(Blob.prototype, 'text', { configurable: true, value: function () { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    if (!URL.revokeObjectURL) URL.revokeObjectURL = () => {};
});
afterEach(async () => { api.defaults.adapter = originalAdapter; if (root) await act(async () => root!.unmount()); root = undefined; client?.clear(); document.body.replaceChildren(); localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it('BC2 unmounts the legacy workspace while retaining campaign and inactive authoring drafts through reopen', async () => {
    const initial = { model_id: 'bindcraft2', mode: 'campaign', denovo_generator: 'bindcraft2', job_name: 'retained campaign',
        bindcraft2_settings: { subbatch_size: null, max_trajectories: 7, trajectory_only: false },
        boltzgen_num_designs: 13, ppiflow_seed_complex_path: 'inputs/retained-seed.pdb',
        binder_round: null };
    await mount(initial); await settleDraft();
    expect(document.querySelector('[aria-label="BindCraft2 campaign"]')).not.toBeNull();
    expect(document.body.textContent).not.toContain('De Novo Binder Design');
    expect(document.body.textContent).not.toContain('Launch De Novo Nanobody Pipeline');
    await click('Save campaign draft');
    const persisted = JSON.parse(document.querySelector('[data-campaign-saved]')!.textContent!);
    expect(persisted).toMatchObject({ bindcraft2_settings: initial.bindcraft2_settings, boltzgen_num_designs: 13, ppiflow_seed_complex_path: 'inputs/retained-seed.pdb' });
    await act(async () => root!.unmount()); client.clear(); document.body.replaceChildren(); state.draft = undefined;
    await mount(persisted); await settleDraft();
    expect(saved()).toMatchObject({ bindcraft2_settings: initial.bindcraft2_settings, boltzgen_num_designs: 13, ppiflow_seed_complex_path: 'inputs/retained-seed.pdb' });
    expect(document.body.textContent).not.toContain('De Novo Binder Design');
    await click('Preview native campaign'); await click('Launch BindCraft2 campaign');
    expect(state.submit).toHaveBeenLastCalledWith(expect.objectContaining({ model_id: 'bindcraft2', params: expect.objectContaining({ bindcraft2_settings: initial.bindcraft2_settings }) }));
});
