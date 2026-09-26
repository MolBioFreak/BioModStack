import React, { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
    meshMount: vi.fn(), moleculeMount: vi.fn(),
    geometries: vi.fn(), settings: vi.fn(), submit: vi.fn(),
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
    fetchExecutionTargets: vi.fn(async () => ({ data: [] })),
    fetchSystemStatus: vi.fn(async () => ({ data: { gpus: [], gpu_error: null } })),
}));
import ShapeBlueprintTemplate from '../../src/components/ShapeBlueprintTemplate';
import ProteinLocalRedesignTemplate from '../../src/components/ProteinLocalRedesignTemplate';

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
