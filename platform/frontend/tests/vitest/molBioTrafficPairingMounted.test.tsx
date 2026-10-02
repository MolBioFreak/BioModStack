import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DigestPanel, getQuickMapEnzymeNames } from '../../src/components/MolBioToolkit/panels/DigestPanel';
import { GibsonDesignWorkspace } from '../../src/components/MolBioToolkit/panels/GibsonDesignWorkspace';
import { AssemblyPanel } from '../../src/components/MolBioToolkit/panels/AssemblyPanel';
import { api, saveGibsonAssembly, saveGoldenGateAssembly, saveDesignedGibsonAssembly } from '../../src/lib/api';
import { simulateRestrictionDigest, fetchRestrictionCatalogBrowse, fetchRestrictionCatalogDetails, parseRestrictionCatalogBrowsePage, parseRestrictionCatalogPage, parseRestrictionDigestSimulation, parseRestrictionDigestSaveAcknowledgement, type RestrictionCatalogSummary, type RestrictionRecord, type RestrictionAnalysisBatch } from '../../src/lib/restrictionAnalysis';
import fixture from '../fixtures/molBioTrafficPairing.json';

const shell = vi.hoisted(() => ({ input: {} as any, visibility: {} as any }));
vi.mock('../../src/components/MolBioToolkit/SequenceViewer', () => ({ SequenceViewer: () => null }));
vi.mock('../../src/components/MolBioToolkit/VisibilityPanel', () => ({ VisibilityPanel: (props: any) => { shell.visibility = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/MolecularInputModal', () => ({ MolecularInputModal: (props: any) => { shell.input = props; return null; } }));
vi.mock('../../src/components/MolBioToolkit/demoConstructs', () => ({ loadDemoPlasmids: async () => [] }));
vi.mock('../../src/components/MolBioToolkit/panels', async () => ({
    ...Object.fromEntries(['AlignmentPanel','AssemblyPanel','HistoryPanel','PCRPanel','PrimerPanel','RnaStructurePanel','FeaturePanel','EditPanel','SearchPanel'].map(name => [name, () => null])),
    DigestPanel: (await import('../../src/components/MolBioToolkit/panels/DigestPanel')).DigestPanel,
}));
vi.mock('../../src/components/experiments/GlobalExperimentContext', () => ({ useGlobalExperimentContext: () => ({ updateQueryParams: vi.fn(), contextHref: () => '/' }) }));
import { MolBioToolkitV2 } from '../../src/components/MolBioToolkit/MolBioToolkitV2';

// Independent receiving projection of the shipped authority, not production parser output.
const asset = JSON.parse(readFileSync('../api/config/molbio/restriction/restriction_enzyme_catalog_v1.json', 'utf8'));
const full: RestrictionRecord[] = asset.records.map((r: RestrictionRecord) => ({ ...r, golden_gate_compatible: fixture.catalog.golden_gate_compatible_ids.includes(r.enzyme_id) }));
function summary(r: RestrictionRecord): RestrictionCatalogSummary {
    return { enzyme_id: r.enzyme_id, canonical_name: r.canonical_name, aliases: r.aliases,
        site_iupac: r.recognition.site_iupac, site_alternatives_iupac: r.recognition.site_alternatives_iupac,
        palindromic: r.recognition.palindromic as boolean, cleavage_status: r.cleavage.status,
        overhang_kinds: [...new Set(r.cleavage.events.map(e => e.overhang_kind))].sort(), nick_strand: r.cleavage.nick?.strand ?? null,
        enzyme_kind: r.enzyme_kind, analysis_capability: r.analysis_capability, golden_gate_compatible: r.golden_gate_compatible,
        exclusion_reason: r.exclusion_reason as string | null, reported_commercial: r.supplier_provenance.reported_commercial,
        historical_supplier_codes: r.supplier_provenance.historical_supplier_codes };
}
const compact = full.map(summary);
const receipt = fixture.catalog.receipt;
const binding = { catalog_id: receipt.catalog_id, expected_catalog_sha256: receipt.catalog_sha256 };
const seq = { name: 'Synthetic input', sequence: 'ACGT'.repeat(20), circular: false, sequenceType: 'dna' as const, features: [], primers: [] };
const noop = () => {};
const bytes = (body: string) => new TextEncoder().encode(body).byteLength;

let root: Root, host: HTMLDivElement, client: QueryClient;
let requests: Array<{ url: string; requestBytes: number; responseBytes: number }>;
const adapter = api.defaults.adapter;
function page(view: 'full' | 'compact', offset: number, ids?: string[]) {
    const records = ids ? full.filter(r => ids.includes(r.enzyme_id)) : full;
    const rows = records.slice(offset, offset + 200);
    return { schema: view === 'full' ? 'bms.molbio.restriction-catalog-page.v1' : 'bms.molbio.restriction-catalog-browse-page.v1', catalog: receipt,
        items: view === 'full' ? rows : rows.map(summary), next_cursor: offset + 200 < records.length ? (ids ? String(offset + 200) : fixture.catalog.cursors[offset / 200]) : null };
}
async function transport(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = new URL(String(input), 'http://fixture.invalid');
    let body: string;
    if (url.pathname === '/api/molbio/restriction/catalog') {
        const view = url.searchParams.get('response_view') === 'compact' ? 'compact' : 'full';
        const ids = url.searchParams.getAll('enzyme_ids');
        const cursor = url.searchParams.get('cursor');
        const offset = ids.length ? Number(cursor ?? 0) : cursor ? (fixture.catalog.cursors.indexOf(cursor) + 1) * 200 : 0;
        body = JSON.stringify(page(view, offset, ids.length ? ids : undefined));
    } else if (url.pathname === '/api/molbio/restriction/digests/simulate') {
        body = fixture.digest.simulation;
    } else if (url.pathname === '/api/molbio/restriction/digests') {
        expect(url.searchParams.get('response_view')).toBe('compact');
        body = fixture.digest.compact;
    } else if (url.pathname === '/api/molbio/restriction/products') {
        // The workspace consumes the receipt only. This receiver is not a supplier-data test.
        body = fixture.products;
    } else throw Error(`Unexpected request ${url}`);
    requests.push({ url: String(input), requestBytes: bytes(String(init?.body ?? '')), responseBytes: bytes(body) });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } });
}
async function render(node: React.ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>)); }
async function click(text: string) { const button = [...host.querySelectorAll('button')].find(b => b.textContent?.trim() === text); expect(button, text).toBeTruthy(); await act(async () => button!.click()); }
async function input(value: string) { await act(async () => { const field = host.querySelector<HTMLInputElement>('input[placeholder="Search enzyme or recognition site…"]')!; Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value); field.dispatchEvent(new Event('input', { bubbles: true })); }); }
beforeEach(() => {
    requests = []; localStorage.clear(); window.innerWidth = 1400;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.stubGlobal('fetch', vi.fn(transport));
    api.defaults.adapter = async config => {
        if (config.url?.endsWith('/primer-tm/options')) return { config, status: 200, statusText: 'OK', headers: {}, data: { algorithms: [{ id: 'nn_santalucia_hicks_2004', sequence_types: ['dna', 'rna'] }], defaults: {} } };
        if (config.url === '/api/sequences') return { config, status: 200, statusText: 'OK', headers: {}, data: [] };
        throw Error(`Unexpected axios ${config.url}`);
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = adapter; vi.unstubAllGlobals(); });

it('strict compact and full parsers exhaust the shipped 1,092-record authority without invented geometry', () => {
    expect(full).toHaveLength(receipt.counts.total);
    for (const view of ['full', 'compact'] as const) {
        const total = Array.from({ length: fixture.catalog.cursors.length }, (_, i) => bytes(JSON.stringify(page(view, i * 200)))).reduce((a, n) => a + n, 0);
        expect(total).toBe(fixture.catalog.decoded_bytes[view]);
    }
    expect(new Set(compact.map(r => r.enzyme_id)).size).toBe(receipt.counts.total);
    for (let offset = 0; offset < full.length; offset += 200) {
        const f = parseRestrictionCatalogPage(page('full', offset));
        const c = parseRestrictionCatalogBrowsePage(page('compact', offset));
        expect(c.items).toEqual(f.items.map(summary));
        expect(() => parseRestrictionCatalogPage(page('compact', offset))).toThrow();
        expect(() => parseRestrictionCatalogBrowsePage(page('full', offset))).toThrow();
        for (const row of c.items) expect(Object.keys(row)).not.toEqual(expect.arrayContaining(['cleavage', 'source']));
    }
    for (const key of Object.keys(compact[0])) {
        const broken: Record<string, unknown> = { ...compact[0] }; delete broken[key];
        expect(() => parseRestrictionCatalogBrowsePage({ ...page('compact', 0), items: [broken] })).toThrow();
    }
    for (const mutant of [{ ...compact[0], cleavage_status: 'guessed' }, { ...compact[0], source: {} }, { ...compact[0], reported_commercial: 'true' }, { ...compact[0], site_iupac: 'XYZ' }]) {
        expect(() => parseRestrictionCatalogBrowsePage({ ...page('compact', 0), items: [mutant] })).toThrow();
    }
});
it('mounted workspace uses six compact pages, all inventory, zero warm-remount catalog bytes, and no detail fan-out', async () => {
    await render(<MolBioToolkitV2 />);
    expect(requests).toHaveLength(0);
    await click('Digest');
    const calls = requests.filter(r => r.url.includes('/catalog?'));
    expect(calls).toHaveLength(6);
    expect(calls.every(r => new URL(r.url, 'http://fixture').searchParams.get('response_view') === 'compact')).toBe(true);
    expect(calls.reduce((sum, r) => sum + r.responseBytes, 0)).toBe(fixture.catalog.decoded_bytes.compact);
    expect(fixture.catalog.decoded_bytes.compact).toBeLessThan(fixture.catalog.decoded_bytes.full);
    expect(host.textContent).toContain(`of ${receipt.counts.total} enzymes`);
    await input(full.at(-1)!.enzyme_id);
    expect(host.querySelector(`[data-enzyme-name="${full.at(-1)!.enzyme_id}"]`)).not.toBeNull();
    expect(requests.filter(r => r.url.includes('enzyme_ids'))).toHaveLength(0);
    await render(null); await render(<MolBioToolkitV2 />); await click('Digest');
    expect(requests.filter(r => r.url.includes('/catalog?'))).toHaveLength(6);
    const firstRow = host.querySelector('[data-enzyme-name]')!;
    const selectedId = firstRow.getAttribute('data-enzyme-name');
    await act(async () => [...firstRow.querySelectorAll('button')].find(b => b.textContent === 'Map')!.click());
    await click('Read selected enzyme details');
    const detailCalls = requests.filter(r => r.url.includes('enzyme_ids'));
    expect(detailCalls).toHaveLength(1);
    const requestedIds = new URL(detailCalls[0].url, 'http://fixture').searchParams.getAll('enzyme_ids');
    expect(requestedIds).toContain(selectedId); // Existing workspace map defaults remain selected.
    expect(host.querySelectorAll('[data-enzyme-detail]')).toHaveLength(requestedIds.length);
    await click('Read selected enzyme details');
    expect(requests.filter(r => r.url.includes('enzyme_ids'))).toHaveLength(1);
    console.info('PAIRING_CATALOG_METRICS', JSON.stringify({ count: full.length, requests: calls.length, decodedBytes: calls.reduce((n,r) => n+r.responseBytes,0), fullFixtureDecodedBytes: fixture.catalog.decoded_bytes.full, warmCatalogRequests: 0 }));
});
it('batch selected details exhaust continuation, preserve exact IDs and reject incomplete/changed authority', async () => {
    const ids = full.slice(0, 260).map(r => r.enzyme_id);
    const records = await fetchRestrictionCatalogDetails({ enzymeIds: ids, catalog: binding, transport });
    expect(records).toEqual(full.slice(0, 260)); expect(requests).toHaveLength(2);
    for (const call of requests) expect(new URL(call.url, 'http://fixture').searchParams.getAll('enzyme_ids')).toEqual([...ids].sort());
    expect(await fetchRestrictionCatalogDetails({ enzymeIds: [], catalog: binding, transport })).toEqual([]);
    expect(requests).toHaveLength(2);
    await expect(fetchRestrictionCatalogDetails({ enzymeIds: ['missing'], catalog: binding, transport })).rejects.toThrow('incomplete');
    await expect(fetchRestrictionCatalogDetails({ enzymeIds: ids, catalog: { ...binding, expected_catalog_sha256: '0'.repeat(64) }, transport })).rejects.toThrow('authority changed');
    await expect(fetchRestrictionCatalogBrowse({ transport: async () => new Response(JSON.stringify({ ...page('compact', 0), next_cursor: null })) })).rejects.toThrow('incomplete');
});
it('mounted full/compact inventory has identical filters, every bulk-map group and selected detail display', async () => {
    // Synthetic UI count fixture, not claimed scientific predictions.
    const analysis = { schema: 'bms.molbio.restriction-analysis-batch-view.v1', source: {}, catalog: receipt, chunks: [], analysis: { counts: { recognition_site_count_definite: full.length, recognition_site_count_possible: 0, double_strand_break_count: 0, nick_count: 0 }, enzyme_summaries: full.map((r, i) => ({ enzyme_id: r.enzyme_id, canonical_name: r.canonical_name, analysis_capability: r.analysis_capability, cleavage_status: r.cleavage.status, recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: i % 4, nick_count: r.analysis_capability === 'nicking_analysis' ? 1 : 0, limitations: [] })), occurrences: [], grouped_cleavages: [], warnings: [], limitations: [] } } as unknown as RestrictionAnalysisBatch;
    const map = vi.fn(), analyze = vi.fn();
    const props = { sequenceData: seq, sequenceId: null, onHighlight: noop, catalog: receipt, analysis, authorityLoading: false, authorityError: null, digestSimulation: null, digestLoading: false, digestError: null, onDigestSelectionChange: noop, onSimulateDigest: noop, onEnzymesChange: map, onAnalyzeAll: analyze };
    const expected = full.map((record, i) => ({ record, summary: analysis.analysis.enzyme_summaries[i], cuts: [], selectionCuts: 0 }));
    for (const records of [full, compact]) {
        await render(null); await render(<DigestPanel {...props} catalogRecords={records} />);
        for (const [group, label] of [['unique', 'Map all 1x'], ['double', 'Map all 2x'], ['three_plus', 'Map all 3x+'], ['nicking', 'Map nicking'], ['type_iis', 'Map Golden Gate']] as const) {
            await click(label); expect(map).toHaveBeenLastCalledWith(getQuickMapEnzymeNames(expected, group));
        }
        for (const [label, count] of [['1x',1],['2x',2],['3x+',3],['0x',0]] as const) {
            await click(label);
            const ids = expected.filter(r => r.summary.double_strand_break_count === count).map(r => r.record.enzyme_id);
            await click(`Map filtered (${ids.length})`); expect(map).toHaveBeenLastCalledWith(ids);
        }
        await click('All'); await click('Analyze full catalog'); expect(analyze).toHaveBeenCalled();
        await input('GAATTC'); const ids = expected.filter(r => [r.record.enzyme_id, r.record.canonical_name, r.record.recognition.site_iupac, ...r.record.aliases].some(s => s.toLowerCase().includes('gaattc'))).map(r => r.record.enzyme_id);
        await click(`Map filtered (${ids.length})`); expect(map).toHaveBeenLastCalledWith(ids);
    }
    await render(null);
    const selected = full.slice(0, 260).map(r => r.enzyme_id);
    await render(<DigestPanel {...props} catalogRecords={compact} selectedEnzymes={selected} onReadEnzymeDetails={enzymeIds => fetchRestrictionCatalogDetails({ enzymeIds, catalog: binding, transport })} />);
    expect(requests).toHaveLength(0);
    await click('Read selected enzyme details');
    expect(requests).toHaveLength(2); expect(host.querySelectorAll('[data-enzyme-detail]')).toHaveLength(260);
    const detailBytes = requests.reduce((n, r) => n + r.responseBytes, 0);
    expect(detailBytes).toBe(bytes(JSON.stringify(page('full', 0, selected))) + bytes(JSON.stringify(page('full', 200, selected))));
    console.info('PAIRING_DETAIL_METRICS', JSON.stringify({ count: selected.length, requests: requests.length, decodedBytes: detailBytes }));
});
it('strictly accepts the corrected real linear terminal coordinates', () => {
    const simulation = parseRestrictionDigestSimulation(JSON.parse(fixture.digest.simulation));
    expect(simulation.fragments.at(-1)?.top_end_boundary_normalized).toBe(simulation.source.content_length);
});
it('mounted digest compact save is identity-only, preserves held fragments and performs no eager GET', async () => {
    const simulation = await simulateRestrictionDigest({source:fixture.digest.request.source, catalog:fixture.digest.request.catalog, enzymeIds:fixture.digest.request.enzyme_ids});
    expect(requests).toHaveLength(1);
    requests = []; vi.mocked(fetch).mockClear();
    await render(<DigestPanel sequenceData={seq} sequenceId="source-document" onHighlight={noop} catalog={receipt} catalogRecords={compact} analysis={null} authorityLoading={false} authorityError={null} digestSimulation={simulation} digestLoading={false} digestError={null} onDigestSelectionChange={noop} onSimulateDigest={noop} />);
    await click('Save digest & fragments');
    expect(requests).toHaveLength(1);
    const call = requests[0]; expect(call.url).toBe('/api/molbio/restriction/digests?response_view=compact');
    expect(call.responseBytes).toBe(bytes(fixture.digest.compact)); expect(call.responseBytes).toBeLessThan(bytes(fixture.digest.full));
    const init = vi.mocked(fetch).mock.calls[0][1]!; const body = JSON.parse(String(init.body));
    expect(body).not.toHaveProperty('simulation'); expect(body.source).not.toHaveProperty('dna');
    expect(body.enzyme_ids).toEqual(simulation.selected_enzyme_ids);
    expect(host.querySelectorAll('[data-fragment-index]')).toHaveLength(simulation.fragments.length);
    expect(host.querySelector('a')?.getAttribute('href')).toContain(JSON.parse(fixture.digest.compact).operation_id);
    expect(() => parseRestrictionDigestSaveAcknowledgement(JSON.parse(fixture.digest.full))).toThrow();
    console.info('PAIRING_DIGEST_METRICS', JSON.stringify({ ...call, fullResponseBytes: bytes(fixture.digest.full), count: simulation.fragments.length, eagerGets: 0 }));
});
it.each(['ligation', 'gibson', 'golden-gate'] as const)('mounted %s save/load uses returned authoritative product and compact metadata with no refetch', async mode => {
    const load = vi.fn(); const captured: any[] = [];
    const savedFixture = fixture.saves[mode];
    api.defaults.adapter = async config => {
        if (config.url?.endsWith('/options')) return { config, status: 200, statusText: 'OK', headers: {}, data: JSON.parse(fixture.options) };
        captured.push(config); expect(config.url).toBe(`/api/molbio/assembly/${mode}/save`); expect(config.params).toEqual({ response_view: 'compact' }); return { config, status: 200, statusText: 'OK', headers: {}, data: JSON.parse(savedFixture.compact) };
    };
    await render(<AssemblyPanel sequenceData={{ ...seq, operationParams: { mode: mode === 'gibson' ? 'ligation' : mode.replace('-', '_'), assembly_request: savedFixture.request } }} selection={null} selectedSequenceId={null} onLoadProduct={load} onLoadSavedWorkup={noop} />);
    if (mode === 'gibson') { await click('Gibson'); await click('Validate purchased fragments'); }
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    await click('Validate + Save'); await click('Load product');
    const result = JSON.parse(savedFixture.compact);
    expect(result.saved_sequence).not.toHaveProperty('sequence');
    expect(load).toHaveBeenCalledWith(expect.objectContaining({ sequence: result.product.sequence, operationParams: result.saved_sequence.operation_params, version: result.saved_sequence.version }), result.saved_sequence.id);
    expect(captured).toHaveLength(1); expect(requests).toHaveLength(0);
    console.info('PAIRING_ASSEMBLY_METRICS', JSON.stringify({ requests: captured.length, requestBytes: bytes(captured[0].data), responseBytes: bytes(savedFixture.compact), fullResponseBytes: bytes(savedFixture.full), eagerGets: 0 }));
});
it('mounted retained Gibson design saves fixed selection and loads held product/primers with no detail GET', async () => {
    const calls: any[] = [], load = vi.fn();
    api.defaults.adapter = async config => {
        calls.push(config);
        const save = config.url?.endsWith('/save');
        expect(config.url).toBe(save ? '/api/molbio/assembly/gibson/design/save' : '/api/molbio/assembly/gibson/design');
        if (save) expect(config.params).toEqual({ response_view: 'compact' });
        return { config, status: 200, statusText: 'OK', headers: {}, data: JSON.parse(save ? fixture.design.compact : fixture.design.preview) };
    };
    const request = fixture.design.request;
    await render(<GibsonDesignWorkspace fragments={request.fragments} preparations={Object.fromEntries(request.fragments.map(f => [f.id, f.preparation]))} saveName="Retained design" saveDescription="" sequenceName="Synthetic" initialCircular={request.circular} onLoadProduct={load} />);
    // Match the captured native settings rather than substitute UI defaults.
    const settings = [request.overlap, request.target_tm, request.min_anneal];
    for (const [index, el] of [...host.querySelectorAll<HTMLInputElement>('input[type="number"]')].entries()) {
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, String(settings[index])); el.dispatchEvent(new Event('input', { bubbles: true })); });
    }
    await click('Design & Simulate'); await click('Save as new construct'); await click('Load preview');
    const response = JSON.parse(fixture.design.compact);
    expect(calls).toHaveLength(2); expect(requests).toHaveLength(0);
    expect(JSON.parse(calls[1].data)).toMatchObject({ computation_id: response.computation_id, selected_candidate_checksum: response.selected_candidate_checksum });
    expect(load).toHaveBeenCalledWith(expect.objectContaining({ sequence: response.selected_product.sequence, operationParams: response.saved_sequence.operation_params, primers: expect.arrayContaining(response.primers.map((p: any) => expect.objectContaining({ sequence: p.full_sequence }))) }), response.saved_sequence.id);
    expect(response.saved_sequence).not.toHaveProperty('sequence');
    expect(bytes(fixture.design.compact)).toBeLessThan(bytes(fixture.design.full));
    console.info('PAIRING_DESIGN_METRICS', JSON.stringify({ requests: 2, saveRequestBytes: bytes(calls[1].data), compactBytes: bytes(fixture.design.compact), fullBytes: bytes(fixture.design.full), eagerGets: 0 }));
});
it('Gibson, retained Gibson design and prepared Golden Gate wrappers explicitly opt in without changing request bodies', async () => {
    const calls: any[] = [];
    api.defaults.adapter = async config => { calls.push(config); const data = config.url?.includes('golden-gate') ? JSON.parse(fixture.saves['golden-gate'].compact) : config.url?.includes('/design/') ? JSON.parse(fixture.design.compact) : JSON.parse(fixture.saves.gibson.compact); return { config, status: 200, statusText: 'OK', headers: {}, data }; };
    const gibson = fixture.saves.gibson.request;
    await saveGibsonAssembly(gibson);
    await saveGoldenGateAssembly(fixture.saves['golden-gate'].request);
    const design = fixture.design.save_request;
    await saveDesignedGibsonAssembly(design);
    expect(calls.map(c => c.params)).toEqual(Array(3).fill({ response_view: 'compact' }));
    expect(calls.map(c => JSON.parse(c.data))).toEqual([gibson, fixture.saves['golden-gate'].request, design]);
    // This case proves transport selection only; mounted retained-design ownership is covered separately.
});
