import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createBC2SourceSession, preparePdbStructureSource } from '../../src/lib/bindcraft2StructureInputs';
import { api } from '../../src/lib/api';
import { NativeBinderSource } from '../../src/components/NativeBinderSource';
import { TargetAntigenSelector, type SelectedTarget } from '../../src/components/TargetAntigenSelector';

// Real source controls, API helpers, parsers and viewer facade. Only transport
// and the low-level WebGL workbench are substituted; no inference claim.
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: any) => <output data-viewer data-format={props.format}>{props.structureData}</output> }));
const pdb = 'ATOM      1  CA  ALA a  42B      7.000   2.000   3.000  1.00 20.00           C  \nEND\n';
const cif = `data_fixture\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n_atom_site.label_atom_id\n_atom_site.label_comp_id\n_atom_site.label_asym_id\n_atom_site.label_seq_id\n_atom_site.auth_asym_id\n_atom_site.auth_seq_id\n_atom_site.auth_comp_id\n_atom_site.pdbx_PDB_ins_code\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n_atom_site.pdbx_PDB_model_num\nATOM 1 C CA ALA X 1 a 42 ALA B 7 2 3 7\n#\n`;
const materialization = { path: 'inputs/derived.pdb', format: 'pdb', sha256: 'derived-sha', native_path: 'inputs/native.cif', native_format: 'cif', native_sha256: 'native-sha', model_numbers: [7], model_number: null, author_residues: [{ model_number: 7, auth_asym_id: 'a', auth_seq_id: 42, insertion_code: 'B', residue_name: 'ALA' }], source_identity: {}, source_path: 'inputs/native.cif' };
let root: Root; let host: HTMLDivElement; let client: QueryClient;
let values: Record<string, any>; let post: ReturnType<typeof vi.spyOn>; let get: ReturnType<typeof vi.spyOn>;
let selected: SelectedTarget | null;
const button = (text: string) => [...host.querySelectorAll('button')].find(node => node.textContent?.trim() === text)!;
async function settle(condition: () => boolean = () => true) {
    for (let i = 0; i < 100; i++) {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
        if (condition()) return;
    }
    throw new Error(`Source did not settle: ${host.textContent}`);
}
async function click(text: string) { expect(button(text), text).toBeTruthy(); await act(async () => button(text).click()); await settle(); }
async function mount(model: 'ppiflow' | 'boltzgen' | 'selector', format: 'native' | 'pdb' = 'native') {
    function Harness() {
        const [state, setState] = useState<Record<string, any>>({}); values = state;
        const [source, setSource] = useState<SelectedTarget | null>(null); selected = source;
        return <QueryClientProvider client={client}>{model === 'selector'
            ? <TargetAntigenSelector label="RFantibody target" requiredFormat={format} selectedTarget={source} onSelect={setSource} />
            : <NativeBinderSource model={model} mode="protein_binder" field="target_pdb" label="Target" values={state} onPatch={patch => setState(old => ({ ...old, ...patch }))} onChains={() => {}} />}</QueryClientProvider>;
    }
    await act(async () => { root.render(<Harness />); }); await settle();
}
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    post = vi.spyOn(api, 'post').mockImplementation(async (url, payload: any) => {
        expect(url).toBe('/api/files/materialize-structure');
        return { data: payload.output_format === 'pdb' ? materialization : { ...materialization, path: 'inputs/native.cif', format: 'cif', sha256: 'native-sha' } } as any;
    });
    get = vi.spyOn(api, 'get').mockImplementation(async url => {
        if (String(url).includes('presets')) return { data: [{ id: 'native', name: 'Native CIF fixture', path: 'inputs/native.cif', category: 'test', description: 'Non-science parser fixture' }] } as any;
        throw new Error(`Unexpected GET ${url}`);
    });
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, text: async () => url.includes('derived.pdb') ? pdb : cif })));
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('PPIFlow real selector invokes checked boundary and saves native/derived author identity without viewer remount', async () => {
    await mount('ppiflow'); await click('Presets'); await click('Native CIF fixture');
    await settle(() => values.target_pdb === 'inputs/derived.pdb');
    expect(post).toHaveBeenCalledWith('/api/files/materialize-structure', expect.objectContaining({ path: 'inputs/native.cif', output_format: 'pdb' }));
    expect(values.target_pdb).toBe('inputs/derived.pdb');
    expect(values.target_pdb_source_reference.materialization).toEqual(materialization);
    expect(values.target_pdb_source_reference.derivedFrom.path).toBe('inputs/native.cif');
    expect(host.querySelector('[data-viewer]')?.getAttribute('data-format')).toBe('pdb');
    const scene = host.querySelector('[data-viewer]');
    await click('Inspect source'); await click('Close full viewer'); await click('Inspect source');
    expect(host.querySelector('[data-viewer]')).toBe(scene);
    expect(post).toHaveBeenCalledTimes(1);
});

it('BoltzGen real source controls retain native CIF without crossing the PDB-only boundary', async () => {
    await mount('boltzgen'); await click('Presets'); await click('Native CIF fixture');
    expect(values.target_pdb).toBe('inputs/native.cif'); expect(post).not.toHaveBeenCalled();
    expect(host.querySelector('[data-viewer]')?.getAttribute('data-format')).toBe('cif');
});

it('PPIFlow conversion failure retains exact native source, never an old primary or a renamed CIF', async () => {
    post.mockRejectedValue(new Error('Selected CIF chain/residue identifiers cannot be represented in PDB'));
    await mount('ppiflow'); await click('Presets'); await click('Native CIF fixture');
    expect(values.target_pdb).toBe('');
    expect(values.target_pdb_source_reference.path).toBe('inputs/native.cif');
    expect(host.querySelector('[data-viewer]')?.getAttribute('data-format')).toBe('cif');
    expect(host.querySelector('[role="alert"]')).toBeTruthy();
});

it('RFantibody receiving selector emits checked PDB with original source metadata', async () => {
    await mount('selector', 'pdb'); await click('Presets'); await click('Native CIF fixture');
    expect(selected?.path).toBe('inputs/derived.pdb');
    expect(selected?.materialization?.native_path).toBe('inputs/native.cif');
    expect(selected?.url).toBe('/api/files/download/inputs%2Fderived.pdb');
});

it('checked conversion coalesces identical acquisition and reopens retained bytes without reacquiring the producer', async () => {
    const session = createBC2SourceSession();
    const source = { name: 'Exact alternate', designId: 'design', jobId: 'owner', document: { artifact_id: 'alt', sha256: 'native-sha' } };
    const [first, second] = await Promise.all([preparePdbStructureSource(source, session), preparePdbStructureSource(source, session)]);
    expect(first.path).toBe(second.path);
    expect(post).toHaveBeenCalledTimes(2); // one exact native snapshot, one checked conversion
    const reopened = await preparePdbStructureSource(first.document.source!);
    expect(reopened.path).toBe(first.path);
    expect(post).toHaveBeenCalledTimes(2);
});

it('RFantibody selector asks for the exact multi-model CIF conformation before checked conversion', async () => {
    const multi = cif.replace('\n#\n', '\nATOM 2 C CA ALA X 1 a 42 ALA B 3 2 3 3\n#\n');
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, text: async () => url.includes('derived.pdb') ? pdb : multi })));
    await mount('selector', 'pdb'); await click('Presets'); await click('Native CIF fixture');
    await settle(() => !!host.querySelector('[aria-label="Source conformation"]'));
    expect(post).not.toHaveBeenCalled(); expect(selected).toBeNull();
    const conformation = host.querySelector<HTMLSelectElement>('[aria-label="Source conformation"]')!;
    await act(async () => { conformation.value = '7'; conformation.dispatchEvent(new Event('change', { bubbles: true })); });
    await settle(() => selected?.path === 'inputs/derived.pdb');
    expect(post).toHaveBeenCalledWith('/api/files/materialize-structure', expect.objectContaining({ model_number: 7, output_format: 'pdb' }));
    expect(selected?.modelNumber).toBe(7);
});

it('late checked conversion cannot resurrect a cleared PPIFlow source', async () => {
    let resolve!: (result: any) => void;
    post.mockImplementation(() => new Promise(done => { resolve = done; }));
    await mount('ppiflow'); await click('Presets'); await click('Native CIF fixture');
    // Clear is available from the native path as well while preparation is pending.
    const field = host.querySelector<HTMLInputElement>('[aria-label="target_pdb"]')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, 'inputs/explicit.pdb'); field.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => resolve({ data: materialization })); await settle();
    expect(values.target_pdb).toBe('inputs/explicit.pdb'); expect(values.target_pdb_source_reference).toBeNull();
});

it('real Project and pinned Dataset controls resolve an exact alternate document with separate source ancestry', async () => {
    const requests: any[] = [];
    get.mockImplementation(async (url: string, config: any) => {
        expect(url).toBe('/api/files/structure-sources'); const q = config.params; requests.push(q);
        let items: any[];
        if (!q.project_id) items = [{ kind: 'project', name: 'Source project', project_id: 'source-project' }];
        else if (q.collection === 'datasets') items = [{ kind: 'dataset', name: 'Pinned dataset', project_id: q.project_id, dataset_id: 'dataset', revision_id: 'revision-4' }];
        else if (q.dataset_id && !q.receipt_id) items = [{ kind: 'resource', name: 'Attached candidate', project_id: q.project_id, dataset_id: q.dataset_id, revision_id: q.revision_id, receipt_id: 'receipt' }];
        else if (q.receipt_id) items = [{ kind: 'document', name: 'Alternate state', ...q, design_id: 'design', job_id: 'producer', path: 'bms_results/alt.cif', document: { artifact_id: 'alt', target_state: 'stateB', sha256: 'native-sha', download_url: '/api/files/download/bms_results/alt.cif' } }];
        else items = [];
        return { data: { items, total: items.length, offset: 0, limit: 50 } } as any;
    });
    await mount('boltzgen'); await click('Project resources'); await click('Source project'); await click('Datasets'); await click('Pinned dataset · revision revision-4'); await click('Attached candidate'); await click('Alternate state');
    expect(post).toHaveBeenCalledWith('/api/files/materialize-structure', expect.objectContaining({ design_id: 'design', job_id: 'producer', document: { artifact_id: 'alt', target_state: 'stateB' }, output_format: 'native', expected_sha256: 'native-sha' }));
    expect(values.target_pdb).toBe('inputs/native.cif');
    expect(values.target_pdb_source_reference.projectSource).toMatchObject({ project_id: 'source-project', revision_id: 'revision-4', receipt_id: 'receipt' });
    expect(values).not.toHaveProperty('launch_context_id');
    expect(requests.some(q => q.revision_id === 'revision-4')).toBe(true);
});
