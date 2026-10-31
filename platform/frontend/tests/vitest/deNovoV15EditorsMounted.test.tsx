import React, { act, useEffect } from 'react';
import { createHash } from 'node:crypto';
import { isolateDeNovoTransport } from './deNovoTransport';
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
import { StructurePredictionTemplate } from '../../src/components/StructurePredictionTemplate';
import { prepareDeNovoContinuation } from '../../src/lib/deNovoContinuation';
import recipes from './fixtures/deNovoContinuationRecipes.json';
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
let restoreTransport: () => void;
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
    const direct = container.querySelector<HTMLInputElement | HTMLSelectElement>(`input[aria-label="${label}"],select[aria-label="${label}"]`);
    if (direct) return direct;
    const owner = [...container.querySelectorAll('label')].find(node => node.textContent?.startsWith(label));
    const input = owner?.querySelector<HTMLInputElement | HTMLSelectElement>('input,select');
    if (!input) throw new Error(`Missing field ${label}`); return input;
}
beforeEach(() => {
    restoreTransport = isolateDeNovoTransport();
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
afterEach(async () => { await unmount(); vi.unstubAllGlobals(); document.body.replaceChildren(); restoreTransport(); });

it('keeps the real geometry controls and active preview mounted across sections, reporting a complete restorable draft', async () => {
    const changed = vi.fn();
    await mount(<ShapeBlueprintTemplate embedded onDraftChange={changed} runDetails={<details data-testid="policy"><summary>Transfer policy</summary></details>} />);
    expect(container.querySelector('h1')).toBeNull();
    const upload = field('Geometry file');
    await edit(field('Geometry'), 'second');
    const preview = container.querySelector('[data-testid="mesh"]');
    await click('RFD3');
    await edit(field('Deterministic seed'), '0');
    await edit(field('Target length'), '140');
    await click('Sequence design');
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
    expect(preview!.closest('.grid')?.contains(policy)).toBe(true); // Existing side-by-side workbench, not the retired vertical layout.
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
        await click('RFD3'); await edit(field('Target length'), '140');
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
        await click('Sequence design'); await click('RFD3');
        await click('Launch Shape Blueprint'); await flush();
        expect(mocks.submit.mock.lastCall![0]).toEqual(submitted);
        await edit(field('Target length'), '160');
        const edited = await previewCurrent();
        expect(edited.target_length).toBe(160);
        expect(edited.client_request_id).not.toBe(prepared.client_request_id);
        const family = container.querySelector('[aria-label="Independent dependency preparation"]')!;
        const familyButton = [...family.querySelectorAll('button')].find(b => b.textContent === 'Preview artifact downloads')!;
        await act(async () => familyButton.click()); await flush();
        expect(posts.at(-1)!.body).toEqual({ kind: 'workflow_pack', workflow_id: 'de_novo_design' });
    } finally { api.defaults.adapter = adapter; }
});

// These lifecycle cases use the real continuation and submission helpers and
// editors. Only HTTP, telemetry transport and the low-level viewers are inert.
const originalAdapter = api.defaults.adapter;
const source = { job_id: 'returned-parent', request_id: 'native-request', candidate_id: 'candidate-7', document: { artifact_id: 'artifact-7' }, output_format: 'native' as const };
const retained = { ...source, path: 'inputs/retained/selected.pdb', expected_sha256: '7'.repeat(64) };
const selectedMaterialization = {
    path: retained.path, format: 'pdb', sha256: retained.expected_sha256, model_number: 1,
    source_structure: retained, native_recipe: recipes.legacyMulti,
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
    expect(changed.mock.lastCall![0].editor_state).toMatchObject({ sequence: 'MK', primary_chain_id: 'authorB', source_structure: { ...retained, model_number: 2 } });
    await click('Clear selected source');
    expect(changed.mock.lastCall![0].editor_state.source_structure).toBeUndefined();
    expect(changed.mock.lastCall![0].editor_state.sequence).toBe('');
    const cleared = JSON.parse(JSON.stringify(changed.mock.lastCall![0]));
    await unmount();
    await mount(<StructurePredictionTemplate onBack={vi.fn()} initialValues={cleared.editor_state} onDraftChange={changed} onOpenTemplateManager={save} />);
    await edit(chooser(), '1:A');
    expect(changed.mock.lastCall![0].editor_state).toMatchObject({ sequence: 'AG', source_structure: { ...retained, model_number: 1 } });
    const sequenceInput = [...container.querySelectorAll<HTMLTextAreaElement>('textarea')].find(n => n.value === 'AG')!;
    expect(sequenceInput).toBeTruthy(); await edit(sequenceInput, 'AGM');
    const esmfold = [...container.querySelectorAll<HTMLButtonElement>('button')].find(n => n.querySelector('span')?.textContent === 'ESMFold2')!;
    expect(esmfold).toBeTruthy(); await act(async () => esmfold.click());
    expect(changed.mock.lastCall![0].editor_state).toMatchObject({ pred_method: 'esmfold2', sequence: 'AGM', source_structure: { ...retained, model_number: 1 } });
    await click('Save Template');
    const saved = JSON.parse(JSON.stringify(save.mock.lastCall![0].currentParams));
    expect(saved).toEqual(changed.mock.lastCall![0].editor_state);
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
    expect(changed.mock.lastCall![0].editor_state).toMatchObject({ sequence: 'AGM', source_structure: request.source_structure });
    expect(posts.filter(p => p.url === '/api/jobs')).toHaveLength(1);
});

// Retained browser counterexample's exact native CIF (historical UI fixture,
// SHA a1916e81afe2cb0511e68fc3af3c39a8801a665f14fa11ae4fe176a2dfebc5d5).
const retainedEnsembleCif = `data_ensemble
#
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_asym_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA A GLY A ? 1 B 1.000 2.000 3.000 0.5 20.0 12 A 1${' '}
ATOM 1 C CA A GLY A ? 1 B 9.000 2.000 3.000 0.5 20.0 12 A 2${' '}
#
loop_
_ma_qa_metric.id
_ma_qa_metric.name
_ma_qa_metric.type
_ma_qa_metric.mode
1 pLDDT pLDDT local
#
loop_
_ma_qa_metric_local.model_id
_ma_qa_metric_local.label_asym_id
_ma_qa_metric_local.label_seq_id
_ma_qa_metric_local.label_comp_id
_ma_qa_metric_local.metric_id
_ma_qa_metric_local.metric_value
1 A 1 GLY 1 0
#
`;
