import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { LaProteinaMotifInput } from '../../src/components/LaProteinaMotifInput';
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: any) => <output data-viewer>{props.structureData}</output> }));
const atom = (n: number) => `ATOM      1  CA  ALA A${String(n).padStart(4)}       7.000   2.000   3.000  1.00 20.00           C  `;
const pdb = [atom(1), atom(2), atom(3), 'END', ''].join('\n');
let host: HTMLDivElement, root: Root, client: QueryClient;
let value: { path: string; contig: string; segmentOrder: string };
let externalPatch: (patch: Record<string, unknown>) => void;
let post: ReturnType<typeof vi.spyOn>;
let fetchMock: ReturnType<typeof vi.fn>;
const button = (name: string) => [...host.querySelectorAll('button')].find(node => node.textContent?.trim() === name)!;
async function settle(condition: () => boolean = () => true) {
    for (let i = 0; i < 100; i++) { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); if (condition()) return; }
    throw new Error(`Unsettled: ${host.textContent}`);
}
async function click(name: string) { expect(button(name), name).toBeTruthy(); await act(async () => button(name).click()); await settle(); }
async function mount(initial = { path: '', contig: '5/A1/2/A3/5', segmentOrder: 'A' }) {
    function Harness() { const [state, setState] = useState(initial); externalPatch = patch => setState(previous => ({ ...previous, ...patch })); value = state; return <QueryClientProvider client={client}><LaProteinaMotifInput {...state} onChange={patch => setState(previous => ({ ...previous, ...patch }))} /></QueryClientProvider>; }
    await act(async () => root.render(<Harness />)); await settle();
}
async function select() {
    await click('Presets'); await settle(() => !!button('Local structure')); await click('Local structure');
    await settle(() => !!host.querySelector('[title="A3 (ALA)"]'));
    await act(async () => { (host.querySelector('[title="A1 (ALA)"]') as HTMLButtonElement).click(); });
    await act(async () => { (host.querySelector('[title="A3 (ALA)"]') as HTMLButtonElement).click(); });
}
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.spyOn(api, 'get').mockImplementation(async url => ({ data: String(url).includes('presets') ? [{ id: 'local', name: 'Local structure', path: 'inputs/source.pdb', category: 'Test', description: '' }] : { cached: [] } }) as any);
    post = vi.spyOn(api, 'post').mockResolvedValue({ data: { path: 'inputs/exact-returned-motif.pdb' } });
    fetchMock = vi.fn(async () => ({ ok: true, text: async () => pdb })); vi.stubGlobal('fetch', fetchMock);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('uses the real source controls and residue grid, retaining only selected source atoms in native order', async () => {
    await mount(); expect([...host.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent === 'Manual native input compatibility')?.open).toBe(false);
    expect(host.querySelector('input[type="file"]')).toBeTruthy();
    for (const name of ['Your Runs', 'Presets', 'RCSB', 'Project resources']) expect(button(name)).toBeTruthy();
    await select(); await settle(() => !!host.querySelector('[data-viewer]'));
    expect(value.path).toBe(''); await click('Use selected residues');
    await settle(() => value.path === 'inputs/exact-returned-motif.pdb');
    expect(value).toEqual({ path: 'inputs/exact-returned-motif.pdb', contig: '5/A1/2/A3/5', segmentOrder: 'A' });
    const form = post.mock.calls[0][1] as FormData;
    const file = form.get('file') as File;
    const contents = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(file); });
    expect(contents).toBe([atom(1), atom(3), 'END', ''].join('\n'));
    expect(post.mock.calls[0][0]).toBe('/api/files/upload');
});
it('uses checked CIF-to-PDB materialization before residue selection', async () => {
    const cif = 'data_fixture\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n_atom_site.label_atom_id\n_atom_site.label_comp_id\n_atom_site.label_asym_id\n_atom_site.label_seq_id\n_atom_site.auth_asym_id\n_atom_site.auth_seq_id\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n_atom_site.pdbx_PDB_model_num\nATOM 1 C CA ALA A 1 A 1 7 2 3 1\n#\n';
    vi.mocked(api.get).mockResolvedValue({ data: [{ id: 'local', name: 'Local structure', path: 'inputs/source.cif', category: 'Test', description: '' }] });
    fetchMock.mockImplementation(async (url: string) => ({ ok: true, text: async () => url.endsWith('source.cif') ? cif : pdb }));
    post.mockImplementation(async url => ({ data: { path: url === '/api/files/materialize-structure' ? 'inputs/checked.pdb' : 'inputs/exact-returned-motif.pdb', format: 'pdb', native_path: 'inputs/source.cif' } }));
    await mount(); await select(); await click('Use selected residues');
    await settle(() => value.path === 'inputs/exact-returned-motif.pdb');
    expect(post).toHaveBeenCalledWith('/api/files/materialize-structure', expect.objectContaining({ path: 'inputs/source.cif', output_format: 'pdb' }));
});
it('ignores a late motif upload after explicit clear', async () => {
    let resolve!: (value: any) => void; post.mockReturnValue(new Promise(yes => { resolve = yes; }));
    await mount(); await select(); await click('Use selected residues'); await click('Clear motif input');
    await act(async () => resolve({ data: { path: 'inputs/stale.pdb' } })); await settle();
    expect(value.path).toBe(''); expect(host.querySelector('[title="A1 (ALA)"]')).toBeNull();
});
it('external A→B→A contig drafts cannot revive an old motif adoption', async () => {
    let resolve!: (value: any) => void; post.mockReturnValue(new Promise(yes => { resolve = yes; }));
    await mount(); await select(); await click('Use selected residues');
    const original = value.contig;
    await act(async () => externalPatch({ contig: '5/A1/2/A3/6' }));
    await act(async () => externalPatch({ contig: original }));
    await act(async () => resolve({ data: { path: 'inputs/stale-A.pdb' } })); await settle();
    expect(value.path).toBe('');
    expect(value.contig).toBe(original);
});
it('ignores source acquisition completing after clear', async () => {
    let resolve!: (value: any) => void; fetchMock.mockReturnValue(new Promise(yes => { resolve = yes; }));
    await mount(); await click('Presets'); await settle(() => !!button('Local structure')); await click('Local structure'); await click('Clear motif input');
    await act(async () => resolve({ ok: true, text: async () => pdb })); await settle();
    expect(value.path).toBe(''); expect(host.querySelector('[title="A1 (ALA)"]')).toBeNull();
});
it('catalog key and four extraction scopes stay separate from custom source, checkpoint and explicit atom spec', async () => {
    let retained: any;
    const catalog = { '1YCR_AA': { contig_string: '10-40/B19-27/10-40', motif_pdb_path: 'motif_benchmark_pdb_files/1ycr_aa.pdb', motif_only: true, motif_min_length: 40, motif_max_length: 100, segment_order: 'A', atom_selection_mode: 'all_atom' } };
    function Harness() {
        const [state, setState] = useState<any>({ path: 'inputs/manual-exact.pdb', contig: 'A1-2', segmentOrder: 'B,A', taskName: 'manual-key', atomSelectionMode: 'ca', atomSpec: 'A1: [CB]' }); retained = state;
        return <QueryClientProvider client={client}><LaProteinaMotifInput {...state} taskCatalog={catalog} onTaskChange={taskName => setState((s: any) => ({ ...s, taskName }))} onAtomSelectionModeChange={atomSelectionMode => setState((s: any) => ({ ...s, atomSelectionMode }))} onChange={patch => setState((s: any) => ({ ...s, ...patch }))} /></QueryClientProvider>;
    }
    await act(async () => root.render(<Harness />)); await settle();
    const original = structuredClone(retained);
    const edit = async (label: string, next: string) => {
        const control = host.querySelector<HTMLInputElement | HTMLSelectElement>(`[aria-label="${label}"]`)!;
        await act(async () => { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(control), 'value')!.set!.call(control, next); control.dispatchEvent(new Event(control.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
    };
    await edit('La native motif task', '1YCR_AA');
    expect(retained).toEqual({ ...original, taskName: '1YCR_AA' });
    expect(host.textContent).toContain('Custom motif PDB takes precedence');
    expect(host.textContent).toContain('10-40/B19-27/10-40');
    for (const scope of ['all_atom', 'ca', 'bb3o', 'tip_atoms']) {
        await edit('La motif extraction scope', scope);
        expect(retained).toEqual({ ...original, taskName: '1YCR_AA', atomSelectionMode: scope });
        expect(host.textContent).toContain('Explicit motif atom specification takes precedence');
    }
    await click('Clear atom specification (native null)');
    expect(retained.atomSpec).toBeNull();
    expect(retained.path).toBe(original.path);
    expect(retained.contig).toBe(original.contig);
    expect(retained.segmentOrder).toBe(original.segmentOrder);
    expect(host.textContent).toContain('contig-based extraction');
    await edit('La motif extraction scope', '');
    expect(retained.atomSelectionMode).toBeUndefined();
    expect(post).not.toHaveBeenCalled();
});

it('reopens exact retained paths and preserves manual contig and order verbatim', async () => {
    const initial = { path: 'inputs/manual-exact.pdb', contig: ' custom/contig ', segmentOrder: ' B,A ' };
    await mount(initial); await settle(() => !!host.querySelector('[title="A1 (ALA)"]'));
    expect(value).toEqual(initial); expect(fetchMock.mock.calls[0][0]).toBe('/api/files/download/inputs%2Fmanual-exact.pdb');
    expect(host.querySelector<HTMLInputElement>('[aria-label="Motif contig"]')?.value).toBe(initial.contig);
    expect(post).not.toHaveBeenCalled();
});
