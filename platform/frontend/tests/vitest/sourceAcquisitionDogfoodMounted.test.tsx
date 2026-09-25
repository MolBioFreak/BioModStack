import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { TargetAntigenSelector, type SelectedTarget } from '../../src/components/TargetAntigenSelector';

// Actual picker, Project browser, source session and parsers; transport only.
const presets = [
    { id: 'lyz', name: 'Hen Lysozyme (1lyz)', path: 'rcsb/1lyz.pdb', category: 'Test', description: '' },
    { id: 'ubq', name: 'Ubiquitin', path: 'rcsb/1ubq.pdb', category: 'Test', description: '' },
    { id: 'local', name: 'Local structure', path: 'inputs/local.pdb', category: 'Test', description: '' },
];
const pdb = 'ATOM      1  CA  ALA A   1       7.000   2.000   3.000  1.00 20.00           C  \nEND\n';
const cif = 'data_fixture\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n_atom_site.label_atom_id\n_atom_site.label_comp_id\n_atom_site.label_asym_id\n_atom_site.label_seq_id\n_atom_site.auth_asym_id\n_atom_site.auth_seq_id\n_atom_site.auth_comp_id\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n_atom_site.pdbx_PDB_model_num\nATOM 1 C CA ALA A 1 A 1 ALA 7 2 3 1\n#\n';
const acquired = (id = '1LYZ', path = 'rcsb/owned-lysozyme.pdb') => ({ ok: true, json: async () => ({ pdb_id: id, path, url: `/api/files/download/${encodeURIComponent(path)}` }) });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
let host: HTMLDivElement; let root: Root; let client: QueryClient;
let selected: SelectedTarget | null; let selections: Array<SelectedTarget | null>;
let get: ReturnType<typeof vi.spyOn>; let post: ReturnType<typeof vi.spyOn>; let fetchMock: ReturnType<typeof vi.fn>;
const button = (name: string) => [...host.querySelectorAll('button')].find(node => node.textContent?.trim() === name);
async function settle(until: () => boolean = () => true) {
    for (let i = 0; i < 100; i++) {
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
        if (until()) return;
    }
    throw new Error(`Did not settle: ${host.textContent}`);
}
async function click(name: string) { expect(button(name), name).toBeTruthy(); await act(async () => button(name)!.click()); await settle(); }
async function mount(format: 'native' | 'pdb' = 'native', initial: SelectedTarget | null = null) {
    function Harness() {
        const [source, setSource] = useState(initial); selected = source;
        return <QueryClientProvider client={client}><TargetAntigenSelector initialTab="presets" selectedTarget={source} requiredFormat={format} onSelect={value => { selections.push(value); setSource(value); }} /></QueryClientProvider>;
    }
    await act(async () => root.render(<Harness />)); await settle(() => !!button('Hen Lysozyme (1lyz)'));
}
beforeEach(() => {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    selected = null; selections = []; client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    get = vi.spyOn(api, 'get').mockImplementation(async url => {
        if (String(url).includes('presets')) return { data: presets } as any;
        if (String(url) === '/api/rcsb') return { data: { cached: [{ pdb_id: '1UBQ', size_bytes: 100 }] } } as any;
        throw new Error(`Unexpected GET ${url}`);
    });
    post = vi.spyOn(api, 'post').mockRejectedValue(new Error('Unexpected POST'));
    fetchMock = vi.fn(async (url: string) => {
        if (url === '/api/rcsb/1LYZ') return acquired();
        if (url === '/api/rcsb/1UBQ') return acquired('1UBQ', 'rcsb/1ubq.pdb');
        if (url.startsWith('/api/files/download/')) return { ok: true, text: async () => pdb };
        throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('acquires an uncached RCSB preset before emitting the returned owned path and preserves its preset identity', async () => {
    const pending = deferred<any>(); fetchMock.mockReturnValueOnce(pending.promise);
    await mount(); await click('Hen Lysozyme (1lyz)');
    expect(selected).toBeNull(); expect(selections).toEqual([]); expect(fetchMock).toHaveBeenCalledWith('/api/rcsb/1LYZ');
    await act(async () => pending.resolve(acquired())); await settle(() => !!selected);
    expect(selected).toEqual({ type: 'preset', name: 'Hen Lysozyme (1lyz)', pdbId: '1LYZ', path: 'rcsb/owned-lysozyme.pdb', url: '/api/files/download/rcsb%2Fowned-lysozyme.pdb' });
    expect(host.textContent).toContain('Preset: Hen Lysozyme (1lyz)');
    expect(post).not.toHaveBeenCalled();
});
it('keeps cache-hit RCSB presets on the cache authority and local presets on their original path', async () => {
    await mount(); await click('Ubiquitin'); await settle(() => selected?.path === 'rcsb/1ubq.pdb');
    expect(selected?.type).toBe('preset'); expect(selected?.name).toBe('Ubiquitin');
    expect(fetchMock).toHaveBeenCalledWith('/api/rcsb/1UBQ');
    fetchMock.mockClear(); await click('Local structure');
    expect(selected).toEqual({ type: 'preset', name: 'Local structure', path: 'inputs/local.pdb', url: '/api/files/pdb/inputs/local.pdb' });
    expect(fetchMock).not.toHaveBeenCalled();
});
it('runs acquired CIF through the actual checked PDB preparation for PDB-only consumers', async () => {
    fetchMock.mockImplementation(async (url: string) => url === '/api/rcsb/1LYZ' ? acquired('1LYZ', 'rcsb/owned.cif') : { ok: true, text: async () => url.includes('owned.cif') ? cif : pdb });
    post.mockResolvedValue({ data: { path: 'inputs/checked.pdb', format: 'pdb', native_path: 'rcsb/owned.cif', sha256: 'fixture' } });
    await mount('pdb'); await click('Hen Lysozyme (1lyz)'); await settle(() => selected?.path === 'inputs/checked.pdb');
    expect(post).toHaveBeenCalledWith('/api/files/materialize-structure', expect.objectContaining({ path: 'rcsb/owned.cif', output_format: 'pdb' }));
    expect(selected?.type).toBe('preset'); expect(selected?.name).toBe('Hen Lysozyme (1lyz)');
    expect(selected?.materialization?.native_path).toBe('rcsb/owned.cif');
    expect(fetchMock.mock.calls.some(([url]: unknown[]) => String(url).includes('rcsb%2F1lyz.pdb'))).toBe(false);
});
it('shows acquisition failure on Presets without emitting a nonexistent path and can retry', async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, json: async () => ({ detail: 'RCSB source unavailable' }) });
    await mount(); await click('Hen Lysozyme (1lyz)'); await settle(() => !!host.querySelector('[role="alert"]'));
    expect(host.querySelector('[role="alert"]')?.textContent).toBe('RCSB source unavailable'); expect(selections).toEqual([]);
    await click('Hen Lysozyme (1lyz)'); await settle(() => !!selected);
    expect(host.querySelector('[role="alert"]')).toBeNull();
});
it.each(['clear', 'newer', 'unmount'] as const)('ignores late acquisition success after %s', async action => {
    const pending = deferred<any>(); fetchMock.mockReturnValueOnce(pending.promise);
    await mount('native', { type: 'upload', name: 'Existing source', path: 'inputs/existing.pdb' });
    await click('Hen Lysozyme (1lyz)');
    if (action === 'clear') await click('Clear');
    if (action === 'newer') await click('Local structure');
    if (action === 'unmount') await act(async () => root.render(null));
    const before = [...selections]; await act(async () => pending.resolve(acquired())); await settle();
    expect(selections).toEqual(before);
    if (action === 'clear') expect(selected).toBeNull();
    if (action === 'newer') expect(selected?.path).toBe('inputs/local.pdb');
});
it('keeps the newer acquired preset when concurrent RCSB replies arrive out of order', async () => {
    const older = deferred<any>(); const newer = deferred<any>();
    fetchMock.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    await mount(); await click('Hen Lysozyme (1lyz)'); await click('Ubiquitin');
    await act(async () => newer.resolve(acquired('1UBQ', 'rcsb/1ubq.pdb'))); await settle(() => selected?.pdbId === '1UBQ');
    await act(async () => older.resolve(acquired())); await settle();
    expect(selected?.name).toBe('Ubiquitin'); expect(selected?.path).toBe('rcsb/1ubq.pdb'); expect(selections).toHaveLength(1);
});
it('ignores an acquired preset after the parent explicitly replaces or clears its source', async () => {
    const pending = deferred<any>(); fetchMock.mockReturnValueOnce(pending.promise);
    const onSelect = vi.fn();
    const render = (source: SelectedTarget | null) => <QueryClientProvider client={client}><TargetAntigenSelector initialTab="presets" selectedTarget={source} onSelect={onSelect} /></QueryClientProvider>;
    await act(async () => root.render(render({ type: 'preset', path: 'inputs/default.pdb', name: 'Default' })));
    await settle(() => !!button('Hen Lysozyme (1lyz)')); await click('Hen Lysozyme (1lyz)');
    await act(async () => root.render(render(null)));
    await act(async () => pending.resolve(acquired())); await settle();
    expect(onSelect).not.toHaveBeenCalled(); expect(host.textContent).not.toContain('Preset: Hen Lysozyme');
});
it('does not show a stale failure over a newer source selection', async () => {
    const pending = deferred<any>(); fetchMock.mockReturnValueOnce(pending.promise);
    await mount(); await click('Hen Lysozyme (1lyz)'); await click('Local structure');
    await act(async () => pending.resolve({ ok: false, json: async () => ({ detail: 'Old failure' }) })); await settle();
    expect(selected?.path).toBe('inputs/local.pdb'); expect(host.querySelector('[role="alert"]')).toBeNull();
});
it('renders an acquired RCSB badge once without changing source name or identity', async () => {
    await mount(); await click('RCSB'); await settle(() => host.textContent!.includes('1UBQ'));
    const cached = [...host.querySelectorAll('button')].find(node => node.textContent?.includes('1UBQ'))!;
    await act(async () => cached.click()); await settle(() => !!selected);
    expect(selected?.name).toBe('RCSB: 1UBQ'); expect(selected?.type).toBe('rcsb'); expect(selected?.pdbId).toBe('1UBQ');
    expect(host.textContent).toContain('RCSB: 1UBQ'); expect(host.textContent).not.toContain('RCSB: RCSB:');
});
it.each(['1UBQ', 'RCSB: 1UBQ'])('shows a single provenance prefix for reopened name %s', async name => {
    await mount('native', { type: 'rcsb', name, pdbId: '1UBQ', path: 'rcsb/1ubq.pdb' });
    expect(host.textContent).toContain('RCSB: 1UBQ'); expect(host.textContent).not.toContain('RCSB: RCSB:'); expect(selected?.name).toBe(name);
});
it.each(['design has no authoritative review/producer manifest', { reason: 'not a string' }, null])('shows safe Project error detail %j and supports back/recovery', async detail => {
    get.mockImplementation(async (url, options: any) => {
        if (String(url).includes('presets')) return { data: presets } as any;
        expect(url).toBe('/api/files/structure-sources');
        if (options?.params?.receipt_id) throw Object.assign(new Error('Request failed with status code 422'), { response: { data: { detail } } });
        const items = options?.params?.project_id
            ? [{ kind: 'resource', name: 'Source receipt', project_id: 'project', receipt_id: 'receipt' }]
            : [{ kind: 'project', name: 'Benign Project', project_id: 'project' }];
        return { data: { items, total: 1, offset: 0, limit: 20 } } as any;
    });
    await mount(); await click('Project resources'); await settle(() => !!button('Benign Project'));
    await click('Benign Project'); await settle(() => !!button('Source receipt')); await click('Source receipt');
    await settle(() => !!host.querySelector('[role="alert"]'));
    expect(host.querySelector('[role="alert"]')?.textContent).toBe(typeof detail === 'string' ? detail : 'Request failed with status code 422');
    expect(selections).toEqual([]); await click('Back to source list'); await settle(() => !!button('Source receipt'));
    expect(host.querySelector('[role="alert"]')).toBeNull(); await click('Presets'); await click('Local structure');
    expect(selected?.path).toBe('inputs/local.pdb'); expect(post).not.toHaveBeenCalled();
});
