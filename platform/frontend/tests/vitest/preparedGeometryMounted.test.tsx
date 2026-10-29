import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { webcrypto, createHash } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NativeSourceInput } from '../../src/components/DeNovoNativeSources';
import { PreparedGeometryContext, uploadPreparedGeometry } from '../../src/lib/preparedGeometry';
import { emptyConstructionDraft, type MolecularConstructionDraft } from '../../src/lib/molecularConstruction';
import { api } from '../../src/lib/api';
vi.mock('../../src/components/DeNovoDocumentEditors', () => ({ useDeNovoDocumentEditors: () => null }));
vi.mock('../../src/components/MolstarViewer', () => ({ default: () => null }));
const text = 'ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C  \nEND\n';
const sha = createHash('sha256').update(text).digest('hex');
const draft = () => ({ ...emptyConstructionDraft(), imported: { nil: null, zero: 0 }, geometry: { choices: {}, request: { authored: true }, result: { format: 'pdb', coordinate_text: text, coordinate_sha256: sha, coordinate_size_bytes: new TextEncoder().encode(text).length, graph: { bonds: [{ order: 1 }], atoms: [{ formal_charge: -1 }] }, atom_map: [{ graph_atom_id: 'exact' }], provenance: { owner: 'fixture' }, request: { normalized: true } } } } as unknown as MolecularConstructionDraft);
let root: Root | undefined, adapter: typeof api.defaults.adapter, generation: number;
let release: (() => void) | undefined, hold: boolean, corrupt: boolean, failMaterialize: boolean, holdMaterialize: boolean;
const acquired = vi.fn(() => 'new'), retain = vi.fn(), calls: string[] = [];
beforeEach(() => {
    failMaterialize = false; holdMaterialize = false;
    generation = 0; hold = false; corrupt = false; release = undefined; calls.length = 0; acquired.mockClear(); retain.mockClear();
    vi.stubGlobal('crypto', webcrypto); adapter = api.defaults.adapter;
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, arrayBuffer: async () => new TextEncoder().encode(corrupt ? 'corrupt' : text).buffer })));
    api.defaults.adapter = async config => {
        calls.push(config.url!);
        if (config.url === '/api/files/upload-immutable-structure') {
            expect(config.data.get('file').name).toBe(`constructed-${sha}.pdb`);
            expect(config.data.get('sha256')).toBe(sha);
            const uploaded = await new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsText(config.data.get('file')); });
            expect(uploaded).toBe(text);
            if (hold) await new Promise<void>(resolve => { release = resolve; });
            return { data: { path: `inputs/protein_local_redesign/${sha}.pdb`, sha256: sha, size: new TextEncoder().encode(text).length }, config, status: 200, statusText: 'OK', headers: {} };
        }
        expect(config.url).toBe('/api/files/materialize-structure');
        if (failMaterialize) throw Error('materialization unavailable after upload');
        if (holdMaterialize) await new Promise<void>(resolve => { release = resolve; });
        const source = JSON.parse(config.data);
        expect(source).toMatchObject({ expected_sha256: sha, output_format: 'native' });
        return { data: { source_structure: source, path: source.path, format: 'pdb', native_format: 'pdb', author_residues: [], model_numbers: [1] }, config, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.replaceChildren(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); });
async function mount(value = '') {
    const host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await render(value);
    if (!value) await click('Choose / import document');
}
async function render(value: string) { await act(async () => root!.render(<PreparedGeometryContext.Provider value={{ current: draft(), generation: () => generation, retain }}><NativeSourceInput label="Context" value={value} bindings={{ old: { path: 'inputs/old.pdb' }, other: { path: 'inputs/other.pdb' } }} onChange={() => {}} onAcquire={acquired} /></PreparedGeometryContext.Provider>)); }
async function wait(check: () => void) { await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); check(); }); }
async function click(text: string) { await act(async () => [...document.querySelectorAll('button')].find(b => b.textContent === text)!.click()); }
it('explicit picker adopts only after immutable PDB readback and existing materialization, retaining exact companion', async () => {
    await mount(); expect(calls).toEqual([]); await click('Use constructed geometry');
    await wait(() => expect(acquired).toHaveBeenCalledTimes(1));
    expect(calls).toEqual(['/api/files/upload-immutable-structure', '/api/files/materialize-structure']);
    expect(retain.mock.calls[0][0]).toMatchObject({ format: 'pdb', construction: draft(), geometry: draft().geometry!.result });
    expect(acquired.mock.calls[0][0]).not.toHaveProperty('geometry');
});
it('corrupt readback is advisory and never replaces a source', async () => {
    corrupt = true; await mount(); await click('Use constructed geometry');
    await wait(() => expect(document.body.textContent).toContain('readback does not match'));
    expect(acquired).not.toHaveBeenCalled(); expect(retain).not.toHaveBeenCalled();
});
it.each(['construction A-B-A', 'source replacement', 'unmount'])('fences late uploads after %s', async mode => {
    hold = true; await mount(); await click('Use constructed geometry'); await wait(() => expect(release).toBeTruthy());
    if (mode === 'construction A-B-A') generation += 2;
    if (mode === 'source replacement') { await render('other'); await render(''); }
    if (mode === 'unmount') { await act(async () => root!.unmount()); root = undefined; }
    await act(async () => { release!(); await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(acquired).not.toHaveBeenCalled(); expect(retain).not.toHaveBeenCalled(); expect(calls).toHaveLength(1);
});
it('materialization failure leaves the existing source and companion untouched', async () => {
    failMaterialize = true; await mount(); await click('Use constructed geometry');
    await wait(() => expect(document.body.textContent).toContain('materialization unavailable after upload'));
    expect(acquired).not.toHaveBeenCalled(); expect(retain).not.toHaveBeenCalled();
});
it('construction A-B-A during materialization cannot adopt already uploaded bytes', async () => {
    holdMaterialize = true; await mount(); await click('Use constructed geometry');
    await wait(() => expect(release).toBeTruthy()); generation += 2;
    await act(async () => { release!(); await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(acquired).not.toHaveBeenCalled(); expect(retain).not.toHaveBeenCalled();
});
it('rejects inconsistent original receipt before upload', async () => {
    const value = draft(); value.geometry!.result!.coordinate_size_bytes++;
    await expect(uploadPreparedGeometry(value)).rejects.toThrow('geometry receipt'); expect(calls).toEqual([]);
});
