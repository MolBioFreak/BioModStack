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

import { api } from '../../src/lib/api';
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
    const input = document.querySelector<HTMLInputElement>('input[type="file"][accept=".pdb,.cif"]')!;
    expect(input).not.toBeNull();
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
