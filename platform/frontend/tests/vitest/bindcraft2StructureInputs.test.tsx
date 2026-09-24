import React, { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { BindCraft2StructureInputs } from '../../src/components/BindCraft2StructureInputs';
import { TargetAntigenSelector, type SelectedTarget } from '../../src/components/TargetAntigenSelector';
import EpitopeMolstarViewer from '../../src/components/EpitopeMolstarViewer';
import type { BC2Inventory, BC2Request } from '../../src/components/BindCraft2Settings';
import { acquireBC2Source, createBC2SourceSession, editVisualResidues, fastaChains, parseBC2Document, readBC2Source, residueKey, selectedChains, sliceBC2Structure, sourceDownloadUrl, visualResidues, type BC2InitialSources } from '../../src/lib/bindcraft2StructureInputs';

// Keep real TargetAntigenSelector, JobBrowser, EpitopeSelector, lazy Mol* adapter,
// source utilities and API helpers. Only the WebGL workbench boundary is replaced.
const viewer = vi.hoisted(() => ({ props: {} as Record<string, any> }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: (props: Record<string, any>) => {
    viewer.props = props;
    return <div data-testid="workbench"><button type="button" onClick={() => props.onResidueClick?.({ documentId: 'primary', authAsymId: 'A', authSeqId: 10 })}>Pick A10 in 3D</button></div>;
} }));
const inventory: BC2Inventory = { upstream_commit: 'pinned', fields: {}, presets: {}, paratope_conformations: [], registered_metrics: {} };
const atom = (serial: number, chain: string, number: number, name = 'ALA', insertion = '') => `ATOM  ${String(serial).padStart(5)}  CA  ${name} ${chain}${String(number).padStart(4)}${insertion || ' '}   ${'1.000'.padStart(8)}${'2.000'.padStart(8)}${'3.000'.padStart(8)}  1.00 20.00           C  `;
const pdb = `${atom(1, 'A', 10)}\n${atom(2, 'A', 11, 'GLY')}\n${atom(3, 'A', 12, 'SER')}\n${atom(4, 'B', 20, 'TYR')}\nEND\n`;
const multi = `MODEL        1\n${atom(1, 'A', 10)}\nENDMDL\nMODEL        2\n${atom(2, 'B', 20, 'TYR')}\nENDMDL\nEND\n`;
const cif = `data_fixture\n#\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n_atom_site.label_atom_id\n_atom_site.label_comp_id\n_atom_site.label_asym_id\n_atom_site.label_seq_id\n_atom_site.auth_asym_id\n_atom_site.auth_seq_id\n_atom_site.auth_comp_id\n_atom_site.pdbx_PDB_ins_code\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n_atom_site.pdbx_PDB_model_num\nATOM 1 C CA ALA X 1 AA 10 ALA ? 1.123456 2 3 1\nATOM 2 C CA GLY Y 1 BA 10 GLY A 4 5 6 1\nATOM 3 C CA SER X 2 AA 20 SER ? 7 8 9 2\n#\n`;
let root: Root | undefined;
let host: HTMLDivElement;
let client: QueryClient;
let request: BC2Request;
let preparedSources: Array<{ role: 'target' | 'scaffold'; targetIndex?: number; path: string; source: import('../../src/lib/bindcraft2StructureInputs').BC2Source }>;
let replace: (value: BC2Request) => void;
let uploaded: Array<{ path: string; file: File; content: string }>;
let sources: Map<string, string>;
let post: ReturnType<typeof vi.spyOn>;
let get: ReturnType<typeof vi.spyOn>;
let fetcher: ReturnType<typeof vi.fn>;
const response = (body: string) => ({ ok: true, status: 200, text: async () => body, json: async () => JSON.parse(body) });
async function settle(condition: () => boolean = () => true) {
    for (let i = 0; i < 100; i++) { await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); }); if (condition()) return; }
    throw new Error(`Condition did not settle: ${host?.textContent?.slice(-1000)}`);
}
async function mount(initial: BC2Request, initialSources?: BC2InitialSources) {
    request = initial;
    const seed = initialSources;
    function Harness() { const [value, setValue] = useState(initial); replace = setValue; request = value; return <QueryClientProvider client={client}><form onSubmit={event => { event.preventDefault(); throw new Error('Selection submitted the campaign'); }}><BindCraft2StructureInputs value={value} inventory={inventory} initialSources={seed} onChange={setValue} onSourcePrepared={entry => preparedSources.push(entry)} /></form></QueryClientProvider>; }
    await act(async () => { root = createRoot(host); root.render(React.createElement(Harness)); });
    await settle();
}
function button(text: string): HTMLButtonElement { const found = [...host.querySelectorAll('button')].find(node => node.textContent?.trim() === text); if (!found) throw new Error(`Button not found: ${text}`); return found; }
async function click(element: Element) { await act(async () => { element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true })); }); await settle(); }
async function field(label: string, next: string) { const node = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement; if (!node) throw new Error(`Missing field ${label}`); await act(async () => { Object.getOwnPropertyDescriptor(node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value')!.set!.call(node, next); node.dispatchEvent(new Event(node instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true })); }); await settle(); }
async function upload(label: string, content: string, name: string) { const node = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement; await act(async () => { Object.defineProperty(node, 'files', { configurable: true, value: [new File([content], name)] }); node.dispatchEvent(new Event('change', { bubbles: true })); }); await settle(() => !host.textContent?.includes('Preparing target') && !host.textContent?.includes('Preparing scaffold')); }
beforeEach(() => {
    Object.defineProperty(Blob.prototype, 'text', { configurable: true, value() { return new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(this); }); } });
    preparedSources = [];
    host = document.createElement('div'); document.body.appendChild(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    sources = new Map([['inputs/target.pdb', pdb], ['inputs/scaffold.pdb', pdb], ['inputs/multi.pdb', multi], ['inputs/source.cif', cif]]); uploaded = [];
    post = vi.spyOn(api, 'post').mockImplementation(async (url, data) => {
        if (url !== '/api/files/upload') throw new Error(`Unexpected mutation ${url}`);
        expect((data as FormData).get('path')).toBe('inputs');
        const file = (data as FormData).get('file') as File;
        const content = await file.text(); const path = `inputs/${file.name}`;
        uploaded.push({ path, file, content }); sources.set(path, content);
        return { data: { path, filename: file.name, size: file.size } } as any;
    });
    get = vi.spyOn(api, 'get').mockImplementation(async url => {
        if (url === '/api/jobs') return { data: { jobs: [], total: 0 } } as any;
        if (String(url).includes('/presets')) return { data: [] } as any;
        if (url === '/api/files') return { data: { entries: [] } } as any;
        if (String(url).includes('/filters')) return { data: { species: ['human'] } } as any;
        if (url === '/api/frameworks/library') return { data: { frameworks: [{ pdb_code: 'fixture', scheme: 'imgt', file_path: 'inputs/scaffold.pdb' }] } } as any;
        if (String(url).includes('/attribution')) return { data: { citation: 'SAbDab', license: 'fixture' } } as any;
        if (String(url).includes('/sabdab/search')) return { data: { results: [], total: 0 } } as any;
        if (String(url).includes('/rcsb/cached')) return { data: { cached: [] } } as any;
        throw new Error(`Unexpected GET ${url}`);
    });
    fetcher = vi.fn(async (url: string) => {
        if (url.startsWith('/api/files/download/')) { const path = decodeURIComponent(url.slice('/api/files/download/'.length)); if (!sources.has(path)) throw new Error(`Unmapped source ${path}`); return response(sources.get(path)!); }
        throw new Error(`Unexpected fetch ${url}`);
    }); vi.stubGlobal('fetch', fetcher);
});
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('shared full inspection and source lifecycle', () => {
    it('evicts failed reads for retry and never renders coordinates for FASTA', async () => {
        const session = createBC2SourceSession();
        fetcher.mockRejectedValueOnce(new Error('offline'));
        await expect(session.read({ name: 'target', path: 'inputs/target.pdb' })).rejects.toThrow('offline');
        expect((await session.read({ name: 'target', path: 'inputs/target.pdb' })).format).toBe('pdb');
        sources.set('inputs/sequence.fasta', '>record\nAGS\n');
        await mount({ targets: [{ name: 'sequence', target_path: 'inputs/sequence.fasta' }] });
        await settle(() => !!host.querySelector('[title="A1 (A)"]'));
        expect(host.querySelector('[data-testid="workbench"]')).toBeNull();
        expect([...host.querySelectorAll('button')].some(node => node.textContent === 'Open full viewer')).toBe(false);
    });

    it('opens the full existing workbench without remounting or changing target selections', async () => {
        await mount({ binder_scaffold: 'inputs/scaffold.pdb', targets: [{ name: 'target', target_path: 'inputs/target.pdb', hotspots: 'A10' }] });
        await settle(() => !!host.querySelector('[data-testid="workbench"]'));
        const scene = host.querySelector('[data-testid="workbench"]');
        expect(viewer.props.mode).toBe('standard');
        expect(viewer.props.hideControls).toBe(false);
        await click(button('Open full viewer'));
        expect(viewer.props.hideControls).toBe(false);
        expect(viewer.props.workbenchCollapsed).toBe(false);
        expect(viewer.props.showSequenceTrack).toBe(true);
        expect(host.querySelector('[data-testid="workbench"]')).toBe(scene);
        await click(host.querySelector('[title="A11 (GLY)"]')!);
        expect(viewer.props.residueSelections.map((row: any) => row.authSeqId)).toEqual([10, 11]);
        await click(button('Sequence')); await click(button('3D + sequence'));
        expect(host.querySelector('[data-testid="workbench"]')).toBe(scene);
        await click(button('Close full viewer'));
        await click(button('Inspect scaffold')); await settle(() => !!host.querySelector('[aria-label="Scaffold sequences"]'));
        await click(button('Open full viewer'));
        expect(viewer.props.hideControls).toBe(false);
        await click(button('Pick A10 in 3D'));
        expect((request.targets as any[])[0].hotspots).toBe('A10,A11');
        expect(request.binder_scaffold).toBe('inputs/scaffold.pdb');
    });
    it('keeps canonical document and author/label identities distinct from compatibility keys', async () => {
        await act(async () => { root = createRoot(host); root.render(<EpitopeMolstarViewer pdbData={pdb} documentId="exact-doc" selectedResidueRefs={[
            { documentId: 'exact-doc', authAsymId: 'aA', labelAsymId: 'X', authSeqId: 10, labelSeqId: 1, insertionCode: 'B' },
            { documentId: 'other-doc', authAsymId: 'aA', labelAsymId: 'Y', authSeqId: 10, labelSeqId: 2, insertionCode: 'B' },
        ]} selectedResidues={new Set(['aA:10B'])} />); });
        await settle(() => viewer.props.structureDocumentId === 'exact-doc');
        expect(viewer.props.residueSelections).toHaveLength(2);
        expect(viewer.props.residueSelections.map((row: any) => row.labelAsymId)).toEqual(['X', 'Y']);
    });
    it('shares pending reads and materialization per exact source, not per filename', async () => {
        const session = createBC2SourceSession();
        const source = { name: 'saved', url: '/saved-native' };
        fetcher.mockResolvedValue(response(cif));
        const [a, b] = await Promise.all([session.acquire(source), session.acquire({ ...source })]);
        expect(a.path).toBe(b.path); expect(fetcher).toHaveBeenCalledTimes(1); expect(uploaded).toHaveLength(1);
        expect(a.document.content).toBe(cif);
        await session.acquire({ ...source });
        expect(uploaded).toHaveLength(1);
        await session.acquire({ file: new File([pdb], 'same.pdb'), name: 'same.pdb' });
        await session.acquire({ file: new File([multi], 'same.pdb'), name: 'same.pdb' });
        expect(uploaded).toHaveLength(3);
        expect(uploaded[1].content).not.toBe(uploaded[2].content);
    });
    it('does not reupload previously selected model slices when inspecting back and forth', async () => {
        await mount({ targets: [{ name: 'models', target_path: 'inputs/multi.pdb' }] });
        await settle(() => !!host.querySelector('[aria-label="Use source model"]'));
        await field('Use source model', '2'); await settle(() => uploaded.length === 1);
        await field('Use source model', '1'); await settle(() => uploaded.length === 2);
        await field('Use source model', '2'); await settle();
        expect(uploaded).toHaveLength(2);
        expect((request.targets as any[])[0].target_path).toBe(uploaded[0].path);
        expect(preparedSources.at(-1)).toMatchObject({ role: 'target', targetIndex: 0, path: uploaded[0].path, source: { modelNumber: 2, derivedFrom: { path: 'inputs/multi.pdb' } } });
    });
    it('reuses a pending preview after switching away and back without repainting the wrong role', async () => {
        let release!: (value: unknown) => void;
        const original = fetcher.getMockImplementation()! as (url: string) => Promise<any>;
        fetcher.mockImplementation((url: string) => url === sourceDownloadUrl('inputs/target.pdb') ? new Promise(resolve => { release = resolve; }) : original(url));
        await mount({ targets: [{ name: 'slow', target_path: 'inputs/target.pdb' }], binder_scaffold: 'inputs/scaffold.pdb' });
        await click(button('Inspect scaffold'));
        const selector = host.querySelector('#bc2-inspection-source')!;
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(selector, '0'); selector.dispatchEvent(new Event('change', { bubbles: true })); });
        await act(async () => release(response(pdb)));
        await settle(() => !!host.querySelector('[title="A11 (GLY)"]'));
        expect(fetcher.mock.calls.filter(call => call[0] === sourceDownloadUrl('inputs/target.pdb'))).toHaveLength(1);
        expect(request.binder_scaffold).toBe('inputs/scaffold.pdb');
    });
    it('preserves an away-and-back restored source against late upload completion', async () => {
        let release!: () => void;
        const original = post.getMockImplementation()!;
        post.mockImplementation(async (...args: any[]) => { await new Promise<void>(resolve => { release = resolve; }); return original(...args); });
        const saved = { targets: [{ name: 'old', target_path: 'inputs/target.pdb', hotspots: 'A11' }] };
        await mount(saved); await click(button('Replace source'));
        const node = host.querySelector('[aria-label="Upload target source"]')!;
        await act(async () => { Object.defineProperty(node, 'files', { value: [new File([pdb], 'late.pdb')] }); node.dispatchEvent(new Event('change', { bubbles: true })); });
        await settle(() => !!release);
        await act(async () => replace({ targets: [] }));
        await act(async () => replace(saved));
        await act(async () => release()); await settle();
        expect(request).toEqual(saved);
    });
});

describe('mounted shared acquisition owner', () => {
    async function mountSelector(initial: SelectedTarget | null = null, tab: 'runs' | 'rcsb' = 'runs') {
        let selected = initial;
        function Harness() {
            const [value, setValue] = useState<SelectedTarget | null>(initial);
            selected = value;
            return <QueryClientProvider client={client}><form onSubmit={event => { event.preventDefault(); throw new Error('Source gesture submitted form'); }}><TargetAntigenSelector selectedTarget={value} onSelect={setValue} initialTab={tab} /></form></QueryClientProvider>;
        }
        await act(async () => { root = createRoot(host); root.render(<Harness />); }); await settle();
        return () => selected;
    }
    it('emits governed path and download URL for managed sources consumed by legacy role handlers', async () => {
        const original = get.getMockImplementation()!;
        get.mockImplementation(async (url: string, config: any) => url === '/api/files/browse' ? { data: { entries: [{ path: 'inputs/source.cif', name: 'source.cif', is_directory: false }] } } as any : original(url, config));
        const selected = await mountSelector();
        await click(button('Upload')); await click(button('Browse managed files')); await click(button('source.cif'));
        expect(selected()).toMatchObject({ type: 'upload', name: 'source.cif', path: 'inputs/source.cif', url: sourceDownloadUrl('inputs/source.cif') });
        expect(post).not.toHaveBeenCalled();
    });
    it('selects only the explicit producer document URL and retains native state metadata', async () => {
        const document = { artifact_id: 'artifact-native', target_state: 'state-B', logical_path: 'native/not-a-join.cif', download_url: '/exact-owned-native', sha256: 'byte-identity', format: 'cif', primary: false };
        get.mockImplementation(async (url: string) => ({ data: url === '/api/jobs' ? { jobs: [{ id: 'parent-job', name: 'Fixture run', model_name: 'fixture', status: 'completed', design_count: 1, created_at: '2026-01-01' }], total: 1 } : url === '/api/designs' ? { designs: [{ id: 'design-fixture', job_id: 'child-owner', name: 'Fixture design' }], total: 1 } : url === '/api/blind-pose/child-owner/selection-context' ? { candidate_documents: { 'design-fixture': [document, { artifact_id: 'unlinked', target_state: 'no-url', logical_path: 'native/missing.cif' }] } } : [] }) as any);
        const selected = await mountSelector();
        await settle(() => host.textContent!.includes('Fixture run'));
        await click([...host.querySelectorAll('h4')].find(node => node.textContent === 'Fixture run')!);
        await settle(() => host.textContent!.includes('Fixture design'));
        await click(button('Documents / states for Fixture design'));
        await settle(() => host.textContent!.includes('not-a-join.cif'));
        expect(get).toHaveBeenCalledWith('/api/blind-pose/child-owner/selection-context');
        expect([...host.querySelectorAll('button')].some(node => node.textContent?.includes('missing.cif'))).toBe(false);
        await click([...host.querySelectorAll('button')].find(node => node.textContent?.includes('not-a-join.cif'))!);
        expect(selected()).toMatchObject({ type: 'run', designId: 'design-fixture', jobId: 'child-owner', url: '/exact-owned-native', document });
        expect(selected()?.path).toBeUndefined();
        expect(post).not.toHaveBeenCalled();
    });
    it('ignores a late RCSB success after a deliberate clear', async () => {
        let release!: (value: unknown) => void;
        fetcher.mockImplementation(() => new Promise(resolve => { release = resolve; }));
        const selected = await mountSelector({ type: 'preset', name: 'kept until clear', path: 'inputs/target.pdb' }, 'rcsb');
        const input = host.querySelector('input[maxlength="4"]')!;
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'TEST'); input.dispatchEvent(new Event('input', { bubbles: true })); });
        await click(button('Fetch')); await settle(() => !!release);
        await click(button('Clear'));
        await act(async () => release(response(JSON.stringify({ path: 'inputs/target.pdb', pdb_id: 'TEST' })))); await settle();
        expect(selected()).toBeNull();
        expect(host.textContent).not.toContain('RCSB: TEST');
    });
});

describe('BC2 source / residue authority', () => {
    it('reads canonical paths rather than a conflicting preview URL, without rewriting identifiers', async () => {
        expect(await readBC2Source({ path: 'inputs/target.pdb', url: '/wrong-preview', name: 'saved' })).toBe(pdb);
        expect(fetcher).toHaveBeenCalledWith(sourceDownloadUrl('inputs/target.pdb'), { signal: undefined });
        expect(post).not.toHaveBeenCalled();
    });
    it('materializes URL-only selections once through the real upload helper and previews the uploaded bytes', async () => {
        fetcher.mockResolvedValueOnce(response(pdb));
        const result = await acquireBC2Source({ name: 'prior run', url: '/api/designs/d1/pdb' });
        expect(fetcher).toHaveBeenCalledTimes(1);
        expect(uploaded[0].content).toBe(pdb);
        expect(result.document.content).toBe(uploaded[0].content);
        expect(result.path).toBe(uploaded[0].path);
        expect(uploaded[0].file.name).toMatch(/\.pdb$/);
    });
    it('preserves mmCIF multi-character author IDs, insertion codes, models and coordinate precision', async () => {
        const document = await parseBC2Document(cif, 'source.cif');
        expect(document.models.map(model => model.number)).toEqual([1, 2]);
        expect(document.models[0].chains.map(chain => chain.id)).toEqual(['AA', 'BA']);
        expect(document.models[0].chains[1].residues[0]).toMatchObject({ chainId: 'BA', resNum: 10, iCode: 'A' });
        const slice = await sliceBC2Structure(document, ['AA']);
        expect(slice).toContain('1.123456');
        const again = await parseBC2Document(slice, 'slice.cif');
        expect(again.models[0].chains.map(chain => chain.id)).toEqual(['AA']);
        expect(again.models[0].chains[0].residues.map(row => row.resNum)).toEqual([10]);
        expect((await acquireBC2Source({ file: new File([cif], 'long-chain.mmcif'), name: 'long-chain.mmcif' })).path).toMatch(/\.cif$/);
        expect(uploaded[0].content).toBe(cif);
    });
    it('edits native ranges by delta without dropping hidden-chain / unknown expressions', async () => {
        const chains = (await parseBC2Document(pdb, 'x.pdb')).models[0].chains;
        const before = visualResidues('A10-12,B20,future:span', chains, 'A');
        const after = new Set(before); after.delete('A11');
        expect(editVisualResidues('A10-12,B20,future:span', before, after, chains, 'A')).toBe('A10,A12,B20,future:span');
        expect(visualResidues('10-12', chains, 'A')).toEqual(new Set(['A10', 'A11', 'A12']));
        expect(selectedChains('AB', chains)).toEqual(['A', 'B']);
    });
    it('keeps native PDB chain order and blank identities instead of inventing A', async () => {
        const document = await parseBC2Document(`${atom(1, 'B', 20)}\n${atom(2, 'A', 10)}\n${atom(3, ' ', 30)}\nEND`, 'x.pdb');
        const chains = document.models[0].chains;
        expect(chains.map(chain => chain.id)).toEqual(['B', 'A', '']);
        expect(visualResidues('20', chains, chains[0].id)).toEqual(new Set(['B20']));
    });
    it('uses native insertion-number grouping, never emits invented insertion syntax', async () => {
        const chains = (await parseBC2Document(`${atom(1, 'A', 10)}\n${atom(2, 'A', 10, 'GLY', 'A')}\nEND`, 'x.pdb')).models[0].chains;
        const expression = editVisualResidues('', new Set(), new Set(['A10A']), chains, 'A');
        expect(expression).toBe('A10');
        expect(visualResidues(expression, chains, 'A')).toEqual(new Set(['A10', 'A10A']));
    });
    it('hydrates FASTA records using native record identity and one-based sequence positions', async () => {
        const document = await parseBC2Document('>AA description\nAG\n>BB\nSY\n', 'x.fasta');
        expect(fastaChains(document, '')).toEqual([]);
        expect(fastaChains(document, 'BB')[0].residues.map(residueKey)).toEqual(['BB1', 'BB2']);
        const single = await parseBC2Document('>arbitrary\nAG\n', 'x.fasta');
        expect(fastaChains(single, '')[0].id).toBe('A');
    });
});

describe('mounted BC2 visual preparation workspace', () => {
    it('restores saved native targets/scaffold without emission and composes real sequence + Mol* adapters', async () => {
        const saved = { untouched: { native: true }, binder_scaffold: 'inputs/scaffold.pdb', targets: [{ name: 'saved', target_path: 'inputs/target.pdb', chains: 'A', hotspots: 'A10-12', coldspots: 'B20', weight: 0, objective: 'detarget', future: 'keep' }] };
        await mount(saved, { target: { name: 'legacy', path: 'ignored.pdb' }, scaffold: { name: 'legacy', path: 'ignored-scaffold.pdb' } });
        await settle(() => !!host.querySelector('[title="A11 (GLY)"]') && !!host.querySelector('[data-testid="workbench"]'));
        expect(request).toEqual(saved); expect(post).not.toHaveBeenCalled();
        expect(viewer.props.structureData).toContain(atom(1, 'A', 10));
        expect(viewer.props.residueSelections.map((row: any) => row.authSeqId)).toEqual([10, 11, 12]);
        await click(host.querySelector('[title="A11 (GLY)"]')!);
        expect((request.targets as any[])[0]).toEqual({ ...saved.targets[0], hotspots: 'A10,A12' });
        await click(button('Coldspots')); await click(button('Pick A10 in 3D'));
        expect((request.targets as any[])[0].coldspots).toBe('B20,A10');
        expect((request.targets as any[])[0].hotspots).toBe('A10,A12');
        await click(button('Inspect scaffold')); await settle(() => host.textContent!.includes('Scaffold chains'));
        expect(host.querySelector('[aria-label="Scaffold sequences"]')?.textContent).toContain('AGS');
        expect(request.untouched).toEqual({ native: true });
    });
    it('mounts mmCIF sequence controls without truncating author chain identifiers', async () => {
        await mount({ targets: [{ name: 'CIF target', target_path: 'inputs/source.cif', chains: 'AA,BA', hotspots: 'AA10', future: 'untouched' }] });
        await settle(() => !!host.querySelector('[title="BA10A (GLY)"]'));
        await click(host.querySelector('[title="BA10A (GLY)"]')!);
        expect((request.targets as any[])[0].hotspots).toBe('AA10,BA10');
        expect((request.targets as any[])[0].future).toBe('untouched');
        await settle(() => viewer.props.format === 'cif');
        expect(viewer.props.residueSelections.map((row: any) => [row.authAsymId, row.authSeqId, row.insertionCode])).toEqual([['AA', 10, undefined], ['BA', 10, 'A']]);
        expect(post).not.toHaveBeenCalled();
    });
    it('keeps explicit empty targets, preset source choice and cleared scaffold authoritative', async () => {
        const legacy = { target: { name: 'legacy', path: 'inputs/target.pdb' }, scaffold: { name: 'legacy', path: 'inputs/scaffold.pdb' } };
        await mount({ targets: [], binder_scaffold: null }, legacy);
        expect(request).toEqual({ targets: [], binder_scaffold: null }); expect(fetcher).not.toHaveBeenCalled();
        await act(async () => replace({ target: ['native-preset'], binder_scaffold: '' })); await settle();
        expect(request).toEqual({ target: ['native-preset'], binder_scaffold: '' }); expect(fetcher).not.toHaveBeenCalled();
    });
    it('adopts initial File/sliced target and scaffold once, preserving compatible chain/residue fields', async () => {
        await mount({ extra: 7 }, { target: { name: 'legacy', path: 'wrong-original.pdb', modelPdb: pdb, chains: 'A', hotspots: 'A11' }, scaffold: { name: 'custom', file: new File([pdb], 'custom.pdb') } });
        await settle(() => !!request.binder_scaffold && !!request.targets);
        expect(uploaded).toHaveLength(2);
        expect(uploaded.every(entry => entry.content === pdb)).toBe(true);
        expect((request.targets as any[])[0]).toMatchObject({ name: 'legacy', chains: 'A', hotspots: 'A11' });
        expect(request.extra).toBe(7); expect(fetcher).not.toHaveBeenCalled();
        await click(button('Use native default'));
        expect(Object.hasOwn(request, 'binder_scaffold')).toBe(false);
        await settle(); expect(uploaded).toHaveLength(2);
    });
    it('replaces a source through governed upload and detaches only old source-coordinate selections', async () => {
        await mount({ future: 9, targets: [{ name: 'custom name', target_path: 'inputs/target.pdb', chains: 'A', hotspots: 'A10', coldspots: 'B20', weight: -0.5, objective: 'detarget', extra: 12 }] });
        await click(button('Replace source')); await upload('Upload target source', '>record\nAGST\n', 'new.fasta');
        expect((request.targets as any[])[0]).toEqual({ name: 'custom name', target_path: uploaded[0].path, weight: -0.5, objective: 'detarget', extra: 12 });
        expect(host.querySelector('[title="A1 (A)"]')).not.toBeNull();
        await click(host.querySelector('[title="A1 (A)"]')!);
        expect((request.targets as any[])[0].hotspots).toBe('A1');
        expect(request.future).toBe(9);
    });
    it('edits every target native field without erasing unknown fields, and supports several targets', async () => {
        await mount({ targets: [{ name: 'first', target_path: 'inputs/target.pdb', extra: true }], binder_scaffold: 'inputs/scaffold.pdb' });
        await field('targets.0.name', 'renamed'); await field('targets.0.objective', 'detarget'); await field('targets.0.weight', '-2.5'); await field('targets.0.chains', 'A,B'); await field('targets.0.hotspots', 'A10-11'); await field('targets.0.coldspots', 'B20');
        expect((request.targets as any[])[0]).toEqual({ name: 'renamed', objective: 'detarget', weight: -2.5, chains: 'A,B', hotspots: 'A10-11', coldspots: 'B20', target_path: 'inputs/target.pdb', extra: true });
        await click(button('+ Add target')); await upload('Upload target source', pdb, 'second.pdb');
        expect(request.targets).toHaveLength(2); expect((request.targets as any[])[0].extra).toBe(true);
        await click(host.querySelector('[aria-label="Remove target 1"]')!);
        expect(request.targets).toHaveLength(1); expect((request.targets as any[])[0].target_path).toBe(uploaded[0].path);
    });
    it('materializes chosen model bytes, keeps the model family switchable, and clears old selections', async () => {
        await mount({ targets: [{ name: 'ensemble', target_path: 'inputs/multi.pdb', hotspots: 'A10' }] });
        await settle(() => !!host.querySelector('[aria-label="Use source model"]'));
        await field('Use source model', '2'); await settle(() => uploaded.length === 1);
        expect(uploaded[0].content).toContain(atom(2, 'B', 20, 'TYR'));
        expect(uploaded[0].content).not.toContain(atom(1, 'A', 10));
        expect((request.targets as any[])[0].hotspots).toBeUndefined();
        expect((request.targets as any[])[0].target_path).toBe(uploaded[0].path);
        expect(viewer.props.structureData).toBe(uploaded[0].content);
        await field('Use source model', '1'); await settle(() => uploaded.length === 2);
        expect(uploaded[1].content).toContain(atom(1, 'A', 10));
    });
    it('uses the actual managed file browser and library APIs, then saves an explicit scaffold chain subset', async () => {
        await mount({ targets: [], binder_scaffold: '' });
        await click(button('Choose scaffold')); await click(button('Antibody library'));
        await settle(() => !![...host.querySelectorAll('button')].find(node => node.textContent === 'fixture · imgt'));
        await click(button('fixture · imgt')); await settle(() => request.binder_scaffold === 'inputs/scaffold.pdb');
        expect(post).not.toHaveBeenCalled();
        await click(button('B · 1 aa')); await click(button('Use selected scaffold chains')); await settle(() => uploaded.length === 1);
        expect(uploaded[0].content).toContain(atom(1, 'A', 10)); expect(uploaded[0].content).not.toContain(atom(4, 'B', 20, 'TYR'));
        expect(request.binder_scaffold).toBe(uploaded[0].path);
    });
    it('uses the real preset selector and RCSB path/url identity without duplicate uploads', async () => {
        get.mockImplementation(async (url: string) => ({ data: String(url).includes('presets') ? [{ id: 'fixture', name: 'Fixture preset', category: 'Fixtures', path: 'inputs/target.pdb', description: 'Test structure' }] : String(url).includes('/rcsb') ? { cached: [] } : { jobs: [], total: 0 } }) as any);
        await mount({ targets: [{ name: 'one', target_path: '' }] });
        await click(button('Choose source')); await click(button('Runs / presets / RCSB')); await click(button('Presets'));
        await settle(() => host.textContent!.includes('Fixture preset'));
        const preset = [...host.querySelectorAll('button')].find(node => node.textContent?.includes('Fixture preset'))!;
        await click(preset); await settle(() => (request.targets as any[])[0].target_path === 'inputs/target.pdb');
        expect(post).not.toHaveBeenCalled(); expect(fetcher.mock.calls.some(call => call[0] === sourceDownloadUrl('inputs/target.pdb'))).toBe(true);
    });
    it('browses governed folders with the real API helper and selects the exact file', async () => {
        const original = get.getMockImplementation()!;
        get.mockImplementation(async (url: string, config: any) => url === '/api/files/browse' ? { data: { entries: config.params.path === '/' ? [{ path: 'inputs', name: 'Inputs', is_directory: true }] : [{ path: 'inputs/target.pdb', name: 'target.pdb', is_directory: false }] } } as any : original(url, config));
        await mount({ targets: [{ name: 'file', target_path: '' }] });
        await click(button('Choose source')); await click(button('Browse managed files')); await click(button('▸ Inputs')); await click(button('target.pdb'));
        await settle(() => (request.targets as any[])[0].target_path === 'inputs/target.pdb');
        expect(get).toHaveBeenCalledWith('/api/files/browse', { params: { path: 'inputs' } });
        expect(post).not.toHaveBeenCalled();
    });
    it('fetches an RCSB selection through the real catalog and honors its canonical path', async () => {
        const original = fetcher.getMockImplementation()! as (url: string) => Promise<any>;
        fetcher.mockImplementation(async (url: string) => url === '/api/rcsb/TEST' ? response(JSON.stringify({ pdb_id: 'TEST', path: 'inputs/target.pdb', url: '/deliberately-different-preview' })) : original(url));
        await mount({ targets: [{ name: 'rcsb', target_path: '' }] });
        await click(button('Choose source')); await click(button('Runs / presets / RCSB')); await click(button('RCSB'));
        const input = host.querySelector('input[placeholder="e.g., 4HHB"]') || host.querySelector('input[maxlength="4"]');
        expect(input).not.toBeNull();
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'TEST'); input!.dispatchEvent(new Event('input', { bubbles: true })); });
        await click(button('Fetch')); await settle(() => (request.targets as any[])[0].target_path === 'inputs/target.pdb');
        expect(fetcher.mock.calls.map(call => call[0])).toContain('/api/rcsb/TEST');
        expect(fetcher.mock.calls.map(call => call[0])).not.toContain('/deliberately-different-preview');
        expect(post).not.toHaveBeenCalled();
    });
    it('selects a prior-run design through real JobBrowser and materializes exactly its downloaded bytes', async () => {
        get.mockImplementation(async (url: string) => ({ data: url === '/api/jobs' ? { jobs: [{ id: 'job-fixture', name: 'Fixture run', model_name: 'fixture', status: 'completed', design_count: 1, created_at: '2026-01-01' }], total: 1 } : url === '/api/designs' ? { designs: [{ id: 'design-fixture', name: 'Fixture design', metrics: {} }], total: 1 } : [] }) as any);
        const original = fetcher.getMockImplementation()! as (url: string) => Promise<any>;
        fetcher.mockImplementation(async (url: string) => url === '/api/designs/design-fixture/pdb' ? response(pdb) : original(url));
        post.mockImplementation(async (url, data) => {
            expect(url).toBe('/api/files/materialize-structure');
            expect(data).toMatchObject({ design_id: 'design-fixture', output_format: 'native' });
            sources.set('inputs/exact-run.pdb', pdb);
            return { data: { path: 'inputs/exact-run.pdb', native_path: 'inputs/exact-run.pdb', format: 'pdb', source_identity: { design_id: 'design-fixture' } } } as any;
        });
        await mount({ targets: [{ name: 'run', target_path: '' }] });
        await click(button('Choose source')); await click(button('Runs / presets / RCSB'));
        await settle(() => host.textContent!.includes('Fixture run'));
        const title = [...host.querySelectorAll('h4')].find(node => node.textContent?.trim() === 'Fixture run')!;
        await click(title); await settle(() => host.textContent!.includes('Fixture design'));
        await click([...host.querySelectorAll('button')].find(node => node.textContent?.includes('Fixture design'))!);
        await settle(() => (request.targets as any[])[0].target_path === 'inputs/exact-run.pdb');
        expect(uploaded).toHaveLength(0);
        expect(fetcher.mock.calls.map(call => call[0])).toContain(sourceDownloadUrl('inputs/exact-run.pdb'));
        expect(preparedSources[0].source.materialization?.source_identity).toEqual({ design_id: 'design-fixture' });
    });
    it('returns to the native target set without reviving legacy sources', async () => {
        await mount({ target: ['native-preset'], targets: [{ name: 'override', target_path: 'inputs/target.pdb' }], other: 3 }, { target: { name: 'legacy', path: 'ignored.pdb' } });
        await click(button('Use native target set'));
        expect(request).toEqual({ target: ['native-preset'], other: 3 });
        expect(fetcher.mock.calls.map(call => call[0])).not.toContain(sourceDownloadUrl('ignored.pdb'));
    });
    it('late preview loads cannot repaint a newly restored source', async () => {
        let resolveOld!: (value: unknown) => void;
        fetcher.mockImplementation((url: string) => url === sourceDownloadUrl('inputs/old.pdb') ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(response(pdb)));
        await mount({ targets: [{ name: 'old', target_path: 'inputs/old.pdb' }] });
        await act(async () => replace({ targets: [{ name: 'new', target_path: 'inputs/target.pdb', hotspots: 'A11' }] }));
        await settle(() => !!host.querySelector('[title="A11 (GLY)"]'));
        await act(async () => resolveOld(response(`${atom(1, 'Z', 99)}\nEND\n`))); await settle();
        expect(host.querySelector('[title="Z99 (ALA)"]')).toBeNull();
        expect((request.targets as any[])[0]).toEqual({ name: 'new', target_path: 'inputs/target.pdb', hotspots: 'A11' });
    });
    it('late initial adoption cannot overwrite a later explicit target set', async () => {
        let resolveOld!: (value: unknown) => void;
        fetcher.mockImplementation((url: string) => url === sourceDownloadUrl('inputs/old.pdb') ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(response(pdb)));
        await mount({}, { target: { name: 'old', path: 'inputs/old.pdb' } });
        await click(button('+ Add target'));
        await act(async () => resolveOld(response(pdb))); await settle();
        expect(request.targets).toEqual([{ name: 'target_1', target_path: '' }]);
    });
    it('late upload completion cannot overwrite a later replacement or a reopened request', async () => {
        let release!: () => void;
        const original = post.getMockImplementation()!;
        post.mockImplementation(async (...args: any[]) => { await new Promise<void>(resolve => { release = resolve; }); return original(...args); });
        await mount({ targets: [{ name: 'old', target_path: 'inputs/target.pdb' }] });
        await click(button('Replace source'));
        const node = host.querySelector('[aria-label="Upload target source"]')!;
        await act(async () => { Object.defineProperty(node, 'files', { value: [new File([pdb], 'slow.pdb')] }); node.dispatchEvent(new Event('change', { bubbles: true })); });
        await settle(() => !!release);
        await act(async () => replace({ targets: [{ name: 'restored', target_path: 'inputs/scaffold.pdb', coldspots: 'B20' }], keep: 'exact' }));
        await act(async () => release()); await settle();
        expect(request).toEqual({ targets: [{ name: 'restored', target_path: 'inputs/scaffold.pdb', coldspots: 'B20' }], keep: 'exact' });
    });
});
