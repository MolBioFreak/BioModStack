import React, { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
    meshMount: vi.fn(), moleculeMount: vi.fn(),
    geometries: vi.fn(), settings: vi.fn(), submit: vi.fn(), targets: vi.fn(),
}));
vi.mock('../../src/components/CanonicalMeshPreview', () => ({ default: ({ url }: { url: string }) => {
    useEffect(() => { mocks.meshMount(); }, []);
    return <div data-testid="mesh" data-url={url} />;
} }));
vi.mock('../../src/components/EpitopeMolstarViewer', () => ({ default: ({ structureUrl }: { structureUrl: string }) => {
    useEffect(() => { mocks.moleculeMount(); }, []);
    return <div data-testid="molecule" data-url={structureUrl} />;
} }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => <div data-testid="points" /> }));
vi.mock('../../src/lib/api', async (original) => ({
    ...await original<object>(),
    listShapeGeometries: mocks.geometries,
    fetchShapeSequenceSettings: mocks.settings,
    submitShapeBlueprint: mocks.submit,
    submitJob: mocks.submit,
    fetchExecutionTargets: mocks.targets,
    fetchSystemStatus: vi.fn(async () => ({ data: { gpus: [], gpu_error: null } })),
}));
import { api, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import ShapeBlueprintTemplate from '../../src/components/ShapeBlueprintTemplate';
import ProteinLocalRedesignTemplate from '../../src/components/ProteinLocalRedesignTemplate';
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate';
import { prepareDeNovoContinuation } from '../../src/lib/deNovoContinuation';
import { fetchSystemStatus } from '../../src/lib/api';

// Synthetic UI-only geometry metadata and minimal PDB, never submitted or persisted.
const geometry = (id: string) => ({
    geometry_id: id, source_format: 'obj', source_parser: 'obj', source_unit: 'angstrom', angstrom_per_unit: 1,
    dimensions_angstrom: [20, 30, 40], source_sha256: 'a'.repeat(64), geometry_sha256: 'b'.repeat(64),
    manifest_sha256: 'c'.repeat(64), preview_obj_sha256: 'd'.repeat(64), point_pool_sha256: 'e'.repeat(64),
    sdf_sha256: 'f'.repeat(64), sdf_sign: 'negative_inside', sdf_grid_shape: [10, 10, 10],
    vertex_count: 8, face_count: 12, point_count: 10,
});
const pdb = 'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C  \nATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00 20.00           C  \nTER\nEND\n';
const definition = { engine: 'proteinmpnn', initial_values: { temperature: 0.1, use_noise: true, omit: 'C' },
    contextual_defaults: {}, params: [
        { name: 'temperature', type: 'number', label: 'Temperature', default: 0.1 },
        { name: 'use_noise', type: 'boolean', label: 'Use noise', default: true },
        { name: 'omit', type: 'text', label: 'Omit', default: 'C' },
    ] };
let root: Root | undefined;
let client: QueryClient;
let container: HTMLDivElement;
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); }); };
async function waitForViewer() {
    for (let attempt = 0; attempt < 40 && !container.querySelector('[data-testid="molecule"]'); attempt++) await flush();
    expect(container.querySelector('[data-testid="molecule"]'), container.textContent ?? '').not.toBeNull();
}
async function mount(node: React.ReactNode) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
    await act(async () => root!.render(<MemoryRouter><QueryClientProvider client={client}>{node}</QueryClientProvider></MemoryRouter>));
    await flush();
}
async function unmount() { await act(async () => root?.unmount()); root = undefined; client?.clear(); }
function button(text: string) {
    const result = [...container.querySelectorAll<HTMLButtonElement>('button')].find(node => node.textContent?.trim() === text);
    if (!result) throw new Error(`Missing button ${text}`); return result;
}
async function click(text: string) { await act(async () => button(text).click()); }
async function edit(node: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
    const prototype = node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    await act(async () => {
        Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(node, value);
        node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
    });
}
function field(label: string) {
    const direct = container.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`);
    if (direct) return direct;
    const owner = [...container.querySelectorAll('label')].find(node => node.textContent?.startsWith(label));
    const input = owner?.querySelector<HTMLInputElement | HTMLSelectElement>('input,select');
    if (!input) throw new Error(`Missing field ${label}`); return input;
}
beforeEach(() => {
    vi.clearAllMocks(); sessionStorage.clear();
    mocks.targets.mockResolvedValue({ data: [] });
    mocks.geometries.mockResolvedValue({ data: { geometries: [geometry('first'), geometry('second')] } });
    mocks.settings.mockResolvedValue({ data: definition });
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ structures: [], cached: [] }) })));
    vi.stubGlobal('URL', Object.assign(URL, { revokeObjectURL: vi.fn(), createObjectURL: vi.fn(() => 'blob:editor-test') }));
    if (!File.prototype.text) Object.defineProperty(File.prototype, 'text', { configurable: true, value() {
        return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); });
    } });
});
afterEach(async () => { await unmount(); vi.unstubAllGlobals(); document.body.replaceChildren(); });

it('preserves explicitly cleared insertion bounds through serialized draft reopen', async () => {
    const changed = vi.fn();
    await mount(<ProteinLocalRedesignTemplate embedded onBack={vi.fn()} onDraftChange={changed}
        initialValues={{ redesign_mode: 'minimal_insertion', insertion_min_length: 3, insertion_max_length: 6 }} />);
    await click('Sampling');
    await edit(field('Minimum inserted length'), '');
    await edit(field('Maximum inserted length'), '');
    const draft = JSON.parse(JSON.stringify(changed.mock.lastCall![0]));
    expect(draft).toMatchObject({ insertion_min_length: '', insertion_max_length: '' });
    await unmount();
    await mount(<ProteinLocalRedesignTemplate embedded onBack={vi.fn()} onDraftChange={changed} initialValues={draft} />);
    expect(field('Minimum inserted length').value).toBe('');
    expect(field('Maximum inserted length').value).toBe('');
    expect(mocks.submit).not.toHaveBeenCalled();
});

it('keeps the real geometry controls and active preview mounted across sections, reporting a complete restorable draft', async () => {
    const changed = vi.fn();
    await mount(<ShapeBlueprintTemplate embedded onDraftChange={changed} runDetails={<details data-testid="policy"><summary>Transfer policy</summary></details>} />);
    expect(container.querySelector('h1')).toBeNull();
    const upload = field('Geometry file');
    await edit(field('Geometry'), 'second');
    const preview = container.querySelector('[data-testid="mesh"]');
    await click('Generation');
    await edit(field('Deterministic seed'), '0');
    await edit(field('Target length'), '140');
    await click('Optional next steps');
    await edit(field('Sequence policy'), 'skip');
    await edit(field('Job name'), '');
    await click('Geometry');
    expect(field('Geometry file')).toBe(upload);
    expect(container.querySelector('[data-testid="mesh"]')).toBe(preview);
    expect(mocks.meshMount).toHaveBeenCalledTimes(1);
    expect(mocks.geometries).toHaveBeenCalledTimes(1);
    const draft = changed.mock.lastCall![0];
    expect(draft).toMatchObject({ shape_geometry_id: 'second', shape_target_length: 140, shape_seed: 0,
        shape_name: '', shape_sequence_policy: 'skip', shape_sequence_settings_by_engine: { proteinmpnn: definition.initial_values },
        shape_geometry_sha256: geometry('second').geometry_sha256, shape_length_policy: { mode: 'fixed', min: 140, max: 140 } });
    const policy = container.querySelector('[data-testid="policy"]')!;
    expect(policy.compareDocumentPosition(container.querySelector('[aria-label="Execution target"]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(preview!.compareDocumentPosition(policy) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await unmount();
    await mount(<ShapeBlueprintTemplate embedded initialValues={draft} onDraftChange={changed} />);
    expect(field('Geometry').value).toBe('second'); expect(field('Job name').value).toBe('');
    expect(field('Target length').value).toBe('140'); expect(field('Sequence policy').value).toBe('skip');
    expect(mocks.submit).not.toHaveBeenCalled();
});

it('preserves Shape explicit false, zero, empty settings through late native definitions and across engine drafts', async () => {
    let resolve!: (value: unknown) => void;
    mocks.settings.mockImplementation(() => new Promise(done => { resolve = done; }));
    const changed = vi.fn();
    const initial = { shape_geometry_id: 'second', shape_sequence_settings: { temperature: 0, use_noise: false, omit: '' },
        shape_sequence_settings_by_engine: { proteinmpnn: { temperature: 0, use_noise: false, omit: '' }, fampnn: { temperature: 0.3 } } };
    await mount(<ShapeBlueprintTemplate initialValues={initial} onDraftChange={changed} />);
    expect(container.querySelector('h1')?.textContent).toBe('Shape Blueprint');
    expect(changed.mock.calls[0][0]).toMatchObject({ shape_geometry_id: 'second', shape_sequence_settings: initial.shape_sequence_settings });
    await act(async () => resolve({ data: definition })); await flush();
    expect(changed.mock.lastCall![0].shape_sequence_settings).toEqual(initial.shape_sequence_settings);
    expect(changed.mock.lastCall![0].shape_sequence_settings_by_engine).toEqual(initial.shape_sequence_settings_by_engine);
});

it('prepares and submits the exact current Shape request, keeping retry identity and source-free family preload', async () => {
    const target = { id: 'vast:shape', provider: 'vast', provider_instance_id: 'shape', active: true, state: 'ready', capabilities: {} };
    mocks.targets.mockResolvedValue({ data: [target] });
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, target.id);
    const posts: Array<{ url: string; body: any }> = [];
    const adapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
        const body = config.data ? JSON.parse(String(config.data)) : undefined;
        if (config.method === 'post') posts.push({ url: String(config.url), body });
        return { data: config.method === 'get' ? null : { selection: body, artifacts: [], total_bytes: 0,
            preview_sha256: 'b'.repeat(64), scientific_ready: false, scope: 'managed_asset_activation' },
            status: 200, statusText: 'OK', headers: {}, config };
    };
    mocks.submit.mockRejectedValue(new Error('Review cancelled'));
    try {
        await mount(<ShapeBlueprintTemplate initialValues={{ shape_sequence_policy: 'skip' }} />);
        expect(posts).toEqual([]); expect(mocks.submit).not.toHaveBeenCalled();
        await click('Generation'); await edit(field('Target length'), '140');
        const panel = () => container.querySelector('[aria-label="Unsaved workflow provisioning"]')!;
        const previewCurrent = async () => {
            const node = [...panel().querySelectorAll('button')].find(b => b.textContent === 'Preview artifact downloads')!;
            expect(node).toBeTruthy(); await act(async () => node.click()); await flush();
            return posts.at(-1)!.body.workflow_request.request;
        };
        const prepared = await previewCurrent();
        expect(prepared).toMatchObject({ target_length: 140, sequence_policy: 'skip', sequences_per_backbone: 0,
            execution_target_id: target.id, geometry_id: 'first' });
        await click('Launch Shape Blueprint'); await flush();
        const { execution_target_id, execution_policy, ...preparedScience } = prepared;
        expect(JSON.parse(JSON.stringify(mocks.submit.mock.lastCall![0]))).toEqual(preparedScience);
        expect(execution_target_id).toBe(target.id); expect(execution_policy).toBeTruthy();
        const submitted = mocks.submit.mock.lastCall![0];
        expect(submitted.client_request_id).toBe(prepared.client_request_id);
        await click('Optional next steps'); await click('Generation');
        await click('Launch Shape Blueprint'); await flush();
        expect(mocks.submit.mock.lastCall![0]).toEqual(submitted);
        await edit(field('Target length'), '160');
        const edited = await previewCurrent();
        expect(edited.target_length).toBe(160);
        expect(edited.client_request_id).not.toBe(prepared.client_request_id);
        const family = container.querySelector('[aria-label="Independent dependency preparation"]')!;
        const familyButton = [...family.querySelectorAll('button')].find(b => b.textContent === 'Preview artifact downloads')!;
        await act(async () => familyButton.click()); await flush();
        expect(posts.at(-1)!.body).toEqual({ kind: 'model', model_id: 'protein_modification_experimental' });
    } finally { api.defaults.adapter = adapter; }
});

it('preserves Redesign real pasted-source controls, viewer, ranges and blank seed across presentation toggles and remount', async () => {
    const changed = vi.fn();
    await mount(<ProteinLocalRedesignTemplate embedded onBack={vi.fn()} onDraftChange={changed} runDetails={<div data-testid="policy">Policy</div>} />);
    expect(container.querySelector('h1')).toBeNull();
    const paste = container.querySelector<HTMLTextAreaElement>('[placeholder="ATOM ... or data_entry"]')!;
    await edit(paste, pdb); await click('Use pasted structure in Mol*'); await waitForViewer();
    const preview = container.querySelector('[data-testid="molecule"]');
    expect(preview, container.textContent ?? '').not.toBeNull();
    await edit(field('Range string'), 'A1');
    await click('Sampling'); await edit(field('Seed'), '0');
    await click('Optional next steps'); await click('Source and regions');
    expect(container.querySelector('[placeholder="ATOM ... or data_entry"]')).toBe(paste);
    expect(container.querySelector('[data-testid="molecule"]')).toBe(preview);
    expect(mocks.moleculeMount).toHaveBeenCalledTimes(1);
    await click('Hide 3D'); await click('Show 3D');
    expect(mocks.moleculeMount).toHaveBeenCalledTimes(1);
    await click('Sampling'); await edit(field('Seed'), ''); await edit(field('Job name'), '');
    const draft = changed.mock.lastCall![0];
    expect(draft).toMatchObject({ seed: null, job_name: '', redesign_ranges: 'A1', dump_trajectories: false, context_chains: [] });
    expect(draft._redesign_source_text).toContain('ATOM');
    expect(draft._redesign_source.file).toBeUndefined();
    await unmount(); await mount(<ProteinLocalRedesignTemplate embedded initialValues={draft} onBack={vi.fn()} onDraftChange={changed} />);
    await waitForViewer();
    expect(field('Range string').value).toBe('A1'); expect(field('Job name').value).toBe('');
    expect(field('Seed').value).toBe(''); expect(container.querySelector('[data-testid="molecule"]')).not.toBeNull();
    expect(mocks.submit).not.toHaveBeenCalled();
});

it('does not emit Redesign defaults before saved hydration and preserves explicit clears during late source load', async () => {
    let read!: (value: unknown) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { read = resolve; })));
    const changed = vi.fn(); const back = vi.fn();
    await mount(<ProteinLocalRedesignTemplate onBack={back} initialValues={{ input_structure: 'saved.pdb',
        job_name: '', seed: 0, partial_t: 0, dump_trajectories: false, context_chains: [], redesign_ranges: 'A1',
        interactive_gating: false, fix_fixed_sidechains: false, region_padding: 0 }} onDraftChange={changed} />);
    expect(changed.mock.calls[0][0]).toMatchObject({ job_name: '', seed: 0, partial_t: 0, dump_trajectories: false,
        interactive_gating: false, fix_fixed_sidechains: false, region_padding: 0, redesign_ranges: 'A1' });
    await edit(field('Range string'), '');
    await act(async () => read({ ok: true, blob: async () => new Blob([pdb]) })); await waitForViewer();
    expect(field('Range string').value).toBe('');
    expect(changed.mock.lastCall![0]).toMatchObject({ redesign_ranges: '', context_chains: [], seed: 0 });
    await click('Back'); expect(back).toHaveBeenCalledTimes(1);
});

// These lifecycle cases use the real continuation and submission helpers and
// editors. Only HTTP, telemetry transport and the low-level viewers are inert.
const originalAdapter = api.defaults.adapter;
const source = { job_id: 'returned-parent', request_id: 'native-request', candidate_id: 'candidate-7', document: { artifact_id: 'artifact-7' }, output_format: 'native' as const };
const retained = { ...source, path: 'inputs/retained/selected.pdb', expected_sha256: '7'.repeat(64) };
const selectedMaterialization = {
    path: retained.path, format: 'pdb', sha256: retained.expected_sha256, model_number: 1,
    source_structure: retained,
    author_residues: [
        { model_number: 1, auth_asym_id: 'A', auth_seq_id: 1, residue_name: 'ALA' },
        { model_number: 1, auth_asym_id: 'A', auth_seq_id: 2, residue_name: 'GLY' },
        { model_number: 2, auth_asym_id: 'authorB', auth_seq_id: 9, residue_name: 'MET' },
        { model_number: 2, auth_asym_id: 'authorB', auth_seq_id: 10, residue_name: 'LYS' },
        { model_number: 2, auth_asym_id: 'DNA', auth_seq_id: 1, residue_name: 'DA' },
    ],
};
async function selectedTransport() {
    window.history.replaceState({}, '', '/submit');
    const real = await vi.importActual<typeof import('../../src/lib/api')>('../../src/lib/api');
    mocks.submit.mockImplementation(real.submitJob);
    const posts: Array<{ url: string; body: any }> = [];
    const target = { id: 'vast:chosen', name: 'chosen', provider: 'vast', active: true, state: 'ready', capabilities: { gpu_count: 1 } };
    mocks.targets.mockResolvedValue({ data: [target] });
    vi.mocked(fetchSystemStatus).mockResolvedValue({ data: { gpus: [{ index: 0, name: 'Fixture GPU', memory_total_mb: 24000 }], gpu_error: null } } as any);
    api.defaults.adapter = async config => {
        const body = config.data ? JSON.parse(String(config.data)) : undefined;
        if (config.method === 'post') posts.push({ url: String(config.url), body });
        let data: any = {};
        if (config.url === '/api/files/materialize-structure') data = selectedMaterialization;
        else if (config.url === '/api/jobs/execution-plan/preview') data = {
            schema: 'bms.job.execution-preview.v1', approval_digest: 'a'.repeat(64), admissible: true,
            request: body, plan: { requested_json: body.params, effective_json: body.params,
                source_identity: { revision: 'offline', tree: 'offline' },
                metadata: { static_components: [], dynamic_templates: [], external_services: [] } },
            deferred_preparation: [], blockers: [],
        };
        else if (config.url === '/api/jobs') throw new Error('Offline request captured; no Job created');
        else if (config.url === '/api/execution-targets/active/telemetry') data = {
            available: true, observed_at: new Date().toISOString(), target,
            gpus: [{ index: 0, execution_target_id: target.id, name: 'Fixture remote GPU', memory_total_mb: 24000 }],
        };
        else if (config.url?.includes('integration')) data = { workflows: { structure_prediction: { default_enabled: false } } };
        else if (config.url?.includes('msa')) data = { providers: {}, cache_entries: 0 };
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new Blob([pdb]), json: async () => ({ structures: [], cached: [] }) })));
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    return posts;
}
afterEach(() => { api.defaults.adapter = originalAdapter; vi.restoreAllMocks(); });

it('prediction selects exact author/model sequence, clears and reselects ancestry, and reopens edited saved metadata without pose conditioning', async () => {
    const posts = await selectedTransport(); const changed = vi.fn(); const save = vi.fn();
    const prepared = await prepareDeNovoContinuation(source, 'prediction');
    await mount(<StructurePredictionTemplate onBack={vi.fn()} initialValues={{ ...prepared, boltz_use_msa: false, run_frustrampnn: false }} onDraftChange={changed} onOpenTemplateManager={save} />);
    expect(posts.filter(p => p.url === '/api/jobs')).toEqual([]);
    const chooser = () => field('Selected candidate protein chain');
    expect(chooser().textContent).not.toContain('DNA');
    expect(chooser().value).toBe('');
    await edit(chooser(), '2:authorB');
    expect(changed.mock.lastCall![0]).toMatchObject({ sequence: 'MK', primary_chain_id: 'authorB', source_structure: { ...retained, model_number: 2 } });
    await click('Clear selected source');
    expect(changed.mock.lastCall![0].source_structure).toBeUndefined();
    expect(changed.mock.lastCall![0].sequence).toBe('');
    const cleared = JSON.parse(JSON.stringify(changed.mock.lastCall![0]));
    await unmount();
    await mount(<StructurePredictionTemplate onBack={vi.fn()} initialValues={cleared} onDraftChange={changed} onOpenTemplateManager={save} />);
    await edit(chooser(), '1:A');
    expect(changed.mock.lastCall![0]).toMatchObject({ sequence: 'AG', source_structure: { ...retained, model_number: 1 } });
    const sequenceInput = [...container.querySelectorAll<HTMLTextAreaElement>('textarea')].find(n => n.value === 'AG')!;
    expect(sequenceInput).toBeTruthy(); await edit(sequenceInput, 'AGM');
    const esmfold = [...container.querySelectorAll<HTMLButtonElement>('button')].find(n => n.querySelector('span')?.textContent === 'ESMFold2')!;
    expect(esmfold).toBeTruthy(); await act(async () => esmfold.click());
    expect(changed.mock.lastCall![0]).toMatchObject({ pred_method: 'esmfold2', sequence: 'AGM', source_structure: { ...retained, model_number: 1 } });
    await click('Save Template');
    const saved = JSON.parse(JSON.stringify(save.mock.lastCall![0].currentParams));
    expect(saved).toEqual(changed.mock.lastCall![0]);
    await unmount();
    await mount(<StructurePredictionTemplate onBack={vi.fn()} initialValues={saved} onDraftChange={changed} />);
    await click('Launch Prediction'); await flush();
    const request = posts.filter(p => p.url === '/api/jobs').at(-1)?.body;
    expect(request, container.textContent ?? '').toMatchObject({ execution_target_id: null, source_structure: { ...retained, model_number: 1 }, params: { sequence: 'AGM' } });
    expect(request.params.source_structure).toBeUndefined();
    expect(request.params.binder_chains).toBeUndefined();
    expect(request.params.fixed_target_source_path).toBeUndefined();
    expect(request.params.target_source).toBeUndefined();
    expect(request.parent_job_id).toBeFalsy();
    await unmount();
    await mount(<StructurePredictionTemplate onBack={vi.fn()} initialValues={{ ...request.params, source_structure: request.source_structure,
        pred_method: 'esmfold2', job_name: 'cloned prediction' }} onDraftChange={changed} />);
    expect(changed.mock.lastCall![0]).toMatchObject({ sequence: 'AGM', source_structure: request.source_structure });
    expect(posts.filter(p => p.url === '/api/jobs')).toHaveLength(1);
});

it.each(['native', 'validated'] as const)('selected %s Redesign submits retained path, placement and native settings through serialized reopen', async depth => {
    const posts = await selectedTransport(); const changed = vi.fn();
    const prepared = await prepareDeNovoContinuation(source, 'redesign');
    await mount(<ProteinLocalRedesignTemplate onBack={vi.fn()} initialValues={{ ...prepared, execution_depth: depth,
        redesign_ranges: 'A1', select_unfixed_sequence: 'A1', seq_method: 'mpnn', pinned_gpu: 0,
        seed: 0, partial_t: 0, dump_trajectories: false, interactive_gating: false, region_padding: 0,
        fix_fixed_sidechains: false, seqs_per_design: 2 }} onDraftChange={changed} />);
    await waitForViewer();
    expect(posts.filter(p => p.url === '/api/jobs')).toEqual([]);
    const draft = JSON.parse(JSON.stringify(changed.mock.lastCall![0]));
    expect(draft).toMatchObject({ source_structure: retained, _source_prepared: selectedMaterialization, seed: 0, partial_t: 0, dump_trajectories: false });
    await unmount();
    await mount(<ProteinLocalRedesignTemplate onBack={vi.fn()} initialValues={draft} onDraftChange={changed} />);
    await waitForViewer();
    const launch = depth === 'native' ? 'Launch Native RFD3' : 'Launch RFD3 + Sequence + Validation';
    await click(launch); await flush();
    let request = posts.filter(p => p.url === '/api/jobs').at(-1)?.body;
    expect(request, container.textContent ?? '').toMatchObject({ source_structure: { ...retained, model_number: 1 }, execution_target_id: null,
        model_id: depth === 'native' ? 'protein_local_redesign' : 'protein_modification_experimental', mode: depth === 'native' ? 'local_redesign' : 'region_redesign' });
    expect(request.params).toMatchObject(depth === 'native' ? { input_structure: retained.path, seed: 0, partial_t: 0, dump_trajectories: false }
        : { input_pdb: retained.path, seqs_per_design: 2, fix_fixed_sidechains: false, interactive_gating: false, region_padding: 0 });
    expect(request.params.source_structure).toBeUndefined(); expect(request.parent_job_id).toBeFalsy();
    await click('Vast · chosen'); await flush();
    await click(launch); await flush();
    let approval: HTMLButtonElement | undefined;
    for (let attempt = 0; attempt < 40 && !approval; attempt++) {
        await flush();
        approval = [...document.querySelectorAll<HTMLButtonElement>('button')].find(n => n.textContent === 'Approve and submit');
    }
    expect(approval, container.textContent ?? '').toBeTruthy();
    await act(async () => approval!.click()); await flush();
    request = posts.filter(p => p.url === '/api/jobs').at(-1)?.body;
    expect(request.execution_target_id).toBe('vast:chosen');
    expect(request.source_structure).toEqual({ ...retained, model_number: 1 });
    expect(request.execution_plan_approval).toBe('a'.repeat(64));
    await click('Local'); await flush(); await click(launch); await flush();
    expect(posts.filter(p => p.url === '/api/jobs').at(-1)?.body.execution_target_id).toBeNull();
});

it('Redesign clear ignores a late selected-source read and replacement drops old ancestry', async () => {
    await selectedTransport(); const changed = vi.fn();
    let finish!: (value: unknown) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { finish = resolve; })));
    const prepared = await prepareDeNovoContinuation(source, 'redesign');
    await mount(<ProteinLocalRedesignTemplate onBack={vi.fn()} initialValues={prepared} onDraftChange={changed} />);
    await click('Clear source');
    expect(container.textContent).not.toContain('Parsing structure');
    await act(async () => finish({ ok: true, blob: async () => new Blob([pdb]) })); await flush();
    expect(container.querySelector('[data-testid="molecule"]')).toBeNull();
    expect(changed.mock.lastCall![0].source_structure).toBeUndefined();
    await edit(container.querySelector<HTMLTextAreaElement>('[placeholder="ATOM ... or data_entry"]')!, pdb);
    await click('Use pasted structure in Mol*'); await waitForViewer();
    expect(changed.mock.lastCall![0].source_structure).toBeUndefined();
    expect(changed.mock.lastCall![0]._source_prepared).toBeUndefined();
});
