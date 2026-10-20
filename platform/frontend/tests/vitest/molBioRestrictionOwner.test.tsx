// @vitest-environment jsdom
import './setup';
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import type { RestrictionAnalysisBatch } from '../../src/lib/restrictionAnalysis';
import fixture from '../fixtures/molBioTrafficPairing.json';

const shell = vi.hoisted(() => ({ visibility: {} as any, input: {} as any, digest: {} as any, viewer: {} as any, catalog: {} as any, requests: [] as any[] }));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({ SequenceViewer: (props: any) => { shell.viewer = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({ MolecularInputModal: (props: any) => { shell.input = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({ VisibilityPanel: (props: any) => { shell.visibility = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({ loadDemoPlasmids: async () => [] }));
vi.mock('../../src/components/MolBioToolkit/panels', () => ({
    ...Object.fromEntries(['AlignmentPanel','AssemblyPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name => [name, () => null])),
    DigestPanel: (props: any) => { shell.digest = props; return null; },
}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: () => '/' }) }));
vi.mock('../../src/lib/restrictionAnalysis', async importOriginal => ({
    ...await importOriginal<any>(),
    fetchRestrictionCatalogBrowse: async () => ({ catalog: shell.catalog, items: ['EcoRI', 'BamHI', 'BsaI'].map(enzyme_id => ({ enzyme_id, canonical_name: enzyme_id })) }),
    fetchRestrictionAnalysisBatch: (options: any) => new Promise((resolve, reject) => shell.requests.push({ ...options, resolve, reject })),
}));
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';

let root: Root, host: HTMLDivElement, client: QueryClient;
const adapter = api.defaults.adapter;
const seq = { name: 'Owner fixture', sequence: 'GAATTCGGATCCGGTCTC', circular: false, sequenceType: 'dna', features: [], primers: [] };
// Deliberately synthetic lifecycle receipts: no scientific output is inferred by these tests.
function batch(ids: string[], request = shell.requests.at(-1)): RestrictionAnalysisBatch {
    return { schema: 'bms.molbio.restriction-analysis-batch-view.v1', source: request.source, catalog: request.catalog, chunks: [], complete: true,
        analysis: { counts: {}, enzyme_summaries: ids.map(enzyme_id => ({ enzyme_id, canonical_name: enzyme_id })), occurrences: [], grouped_cleavages: [], warnings: [], limitations: [] } } as unknown as RestrictionAnalysisBatch;
}
async function click(text: string) {
    const button = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === text);
    expect(button, text).toBeTruthy(); await act(async () => button!.click());
}
async function mount() {
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter><MolBioToolkitV2 /></MemoryRouter></QueryClientProvider>));
    await act(async () => shell.input.onLoadDemo(seq));
    await click('Digest');
    expect(shell.requests.length).toBeGreaterThan(0);
}
async function finish(request: any, result: RestrictionAnalysisBatch) { await act(async () => request.resolve(result)); }
beforeEach(() => {
    shell.requests = []; shell.catalog = fixture.catalog.receipt; shell.digest = {};
    localStorage.clear(); window.innerWidth = 1400;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(fixture.products)));
    api.defaults.adapter = async config => ({ config, status: 200, statusText: 'OK', headers: {}, data: config.url?.endsWith('/primer-tm/options') ? { algorithms: [{ id: 'nn_santalucia_hicks_2004', sequence_types: ['dna', 'rna'] }], defaults: {} } : [] });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); });

it('returns and shares a full scan, keeps mapped results during progress, and reuses its coverage after group mapping', async () => {
    await mount();
    const initial = shell.requests.at(-1);
    const selected = batch(['EcoRI', 'BamHI'], initial);
    await finish(initial, selected);
    let first!: Promise<RestrictionAnalysisBatch | undefined>, second!: typeof first;
    await act(async () => { first = shell.digest.onAnalyzeAll(); second = shell.digest.onAnalyzeAll(); });
    expect(first).toBe(second);
    const full = shell.requests.at(-1), count = shell.requests.length;
    expect(full.enzymeIds).toBeUndefined();
    await act(async () => full.onProgress(batch(['BsaI'], full)));
    expect(shell.digest.analysis).toBe(selected);
    await act(async () => shell.digest.onEnzymesChange(['BsaI']));
    expect(shell.requests).toHaveLength(count); expect(full.signal.aborted).toBe(false);
    const result = batch(['EcoRI', 'BamHI', 'BsaI'], full);
    await finish(full, result); expect(await first).toBe(result);
    await act(async () => shell.digest.onEnzymesChange(['EcoRI']));
    expect(shell.requests).toHaveLength(count); expect(shell.digest.analysis).toBe(result);
    // Explicit Analyze is not silently turned into a cache-only operation.
    await act(async () => { void shell.digest.onAnalyzeAll(); });
    expect(shell.requests).toHaveLength(count + 1);
});

it('reuses an in-flight selected superset without cancellation', async () => {
    await mount(); const pending = shell.requests.at(-1), count = shell.requests.length;
    expect(pending.enzymeIds).toContain('EcoRI');
    await act(async () => shell.digest.onEnzymesChange(['EcoRI']));
    expect(shell.requests).toHaveLength(count); expect(pending.signal.aborted).toBe(false);
    await finish(pending, batch(pending.enzymeIds, pending));
    await act(async () => shell.digest.onEnzymesChange(['BamHI']));
    expect(shell.requests).toHaveLength(count);
});

it.each([
    ['DNA', { sequence: 'GAATTCGAATTC' }],
    ['topology', { circular: true }],
    ['construct identity with identical DNA', {}],
])('invalidates on changed %s and ignores late progress/completion', async (_label, change) => {
    await mount();
    await finish(shell.requests.at(-1), batch(['EcoRI']));
    let oldPromise!: Promise<RestrictionAnalysisBatch | undefined>;
    await act(async () => { oldPromise = shell.digest.onAnalyzeAll(); });
    const old = shell.requests.at(-1), staleCallback = shell.digest.onAnalyzeAll;
    await act(async () => shell.input.onLoadDemo({ ...seq, ...change }));
    expect(old.signal.aborted).toBe(true); expect(shell.digest.analysis).toBeNull();
    expect(await staleCallback()).toBeUndefined();
    const current = shell.requests.at(-1);
    expect(current).not.toBe(old);
    const currentResult = batch(['EcoRI'], current); await finish(current, currentResult);
    await act(async () => old.onProgress(batch(['BamHI'], old)));
    await finish(old, batch(['BamHI'], old));
    expect(await oldPromise).toBeUndefined(); expect(shell.digest.analysis).toBe(currentResult);
});

it('invalidates cached and pending results when the catalog binding changes', async () => {
    await mount(); const initial = batch(['EcoRI']); await finish(shell.requests.at(-1), initial);
    let promise!: Promise<RestrictionAnalysisBatch | undefined>;
    await act(async () => { promise = shell.digest.onAnalyzeAll(); });
    const old = shell.requests.at(-1);
    await click('View');
    if (shell.visibility.visibility.cutsites) await act(async () => shell.visibility.onChange('cutsites'));
    client.removeQueries({ queryKey: ['molbio-restriction-catalog'] });
    shell.catalog = { ...shell.catalog, catalog_sha256: 'a'.repeat(64) };
    await click('Digest');
    expect(old.signal.aborted).toBe(true);
    expect(shell.digest.analysis).toBeNull();
    const current = shell.requests.at(-1);
    expect(current.catalog.catalog_sha256).toBe('a'.repeat(64));
    await finish(old, batch(['EcoRI'], old)); expect(await promise).toBeUndefined();
    expect(shell.digest.analysis).toBeNull();
    const result = batch(['EcoRI'], current); await finish(current, result);
    expect(shell.digest.analysis).toBe(result);
});

it('aborts on unmount and does not return stale results to group callers', async () => {
    await mount(); let promise!: Promise<RestrictionAnalysisBatch | undefined>;
    await act(async () => { promise = shell.digest.onAnalyzeAll(); });
    const pending = shell.requests.at(-1);
    await act(async () => root.render(null));
    expect(pending.signal.aborted).toBe(true);
    await finish(pending, batch(['EcoRI'], pending)); expect(await promise).toBeUndefined();
});

it('retains successful results on failure and permits an explicit retry', async () => {
    await mount(); const selected = batch(['EcoRI']); await finish(shell.requests.at(-1), selected);
    let promise!: Promise<RestrictionAnalysisBatch | undefined>;
    await act(async () => { promise = shell.digest.onAnalyzeAll(); });
    await act(async () => shell.requests.at(-1).reject(new Error('Native unavailable')));
    expect(await promise).toBeUndefined(); expect(shell.digest.analysis).toBe(selected);
    expect(shell.digest.authorityError).toBe('Native unavailable'); expect(shell.digest.authorityLoading).toBe(false);
    const count = shell.requests.length;
    await act(async () => { void shell.digest.onAnalyzeAll(); }); expect(shell.requests).toHaveLength(count + 1);
});
