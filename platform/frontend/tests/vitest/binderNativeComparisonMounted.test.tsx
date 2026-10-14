import React from 'react';
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { NativeBinderComparison } from '../../src/components/NativeBinderComparison';
import { BinderResultComparison } from '../../src/components/BinderResultComparison';
import { readNativeComparison, nativeComparisonSelection, nativeComparisonValues, nativeComparisonKey } from '../../src/lib/nativeBinderComparison';
import { api, downloadDesignPdb } from '../../src/lib/api';
import { DesignComparePane } from '../../src/components/DesignComparePane';
import { parseScientificNativeMetric } from '../../src/lib/scientificViewerIdentity';
import { esmfold2Fixture } from '../fixtures/scientificViewerEsmfold2Fixture';
vi.mock('react-plotly.js', () => ({ default: (props: any) => <div data-comparison-plot data-traces={JSON.stringify(props.data)} /> }));

// Explicitly inert native-shaped records: no model or runtime is executed.
const job = (id: string) => ({ id, name: `Test ${id}`, model_id: 'bindcraft2', mode: 'campaign', status: 'completed', created_at: '2026-01-01T00:00:00Z', params: {} });
const draw = (jobId: string, index: number) => ({ design: `native-${index}`, outcome: index % 2 ? 'rejected' : 'passed',
    values: { design: `native-${index}`, i_pTM: index === 0 ? '0' : index === 1 ? '' : String(index / 100), Unbound_Binder_pLDDT: '0.8', bindcraft_version: 'inert-native-v1' },
    structures: [{ artifact_id: `${jobId}-draw-${index}`, target_state: 'open', primary: true }] });
const retained = (jobId: string) => ({ design: 'ranked-native-0', design_id: `${jobId}-design-0`, values: { bms_scored_design: 'native-0' },
    structures: [{ artifact_id: `${jobId}-ranked-0`, target_state: 'open', primary: true }] });
const design = (jobId: string) => ({ id: `${jobId}-design-0`, job_id: jobId, name: 'Native candidate', plddt_overall: null, scientific_structure_document: null });
let calls: Array<{ job: string; stage: string; offset: number }>;
let apiCalls: Array<{ url: string; params: any }>;
let total = 37;
let failLater = false;
let duplicateLater = false;
let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
const original = api.defaults.adapter;
const text = (node: ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const flush = async () => { for (let i = 0; i < 14; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
async function mount(node: React.ReactNode) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { renderer = create(<ThemeProvider><QueryClientProvider client={client!}>{node}</QueryClientProvider></ThemeProvider>); });
    await flush();
}
beforeEach(() => {
    calls = []; apiCalls = []; total = 37; failLater = false; duplicateLater = false; localStorage.clear(); sessionStorage.clear();
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://fixture');
        const id = url.pathname.split('/')[3], stage = url.searchParams.get('stage')!, offset = Number(url.searchParams.get('offset'));
        expect(url.pathname).toBe(`/api/jobs/${id}/bindcraft2-results`);
        calls.push({ job: id, stage, offset });
        if (failLater && stage === 'draw' && offset > 0) throw Error('retained page unavailable');
        const start = duplicateLater && offset ? 0 : offset;
        const rows = stage === 'retained' ? [retained(id)] : Array.from({ length: Math.max(0, Math.min(32, total - start)) }, (_, index) => draw(id, start + index));
        return { ok: true, json: async () => ({ schema: 'bindcraft2.native-readback.v1', arm: null, arms: [{ name: null }], stage, offset, limit: 100, total: stage === 'retained' ? 1 : total, rows, accounting: {}, analytics: {}, metadata: {}, artifacts: [] }) };
    }));
    api.defaults.adapter = async config => {
        const url = String(config.url); apiCalls.push({ url, params: config.params });
        let data: unknown;
        if (url === '/api/designs') data = { designs: [design(config.params.job_id)], total: 1, offset: config.params.offset, limit: config.params.limit };
        else if (/\/api\/jobs\/[^/]+$/.test(url)) data = job(url.split('/').at(-1)!);
        else if (url.endsWith('/residue-metrics')) data = { status: 'unavailable', reason: 'fixture has no native QA' };
        else if (/\/api\/designs\/[^/]+$/.test(url)) data = design(url.split('/').at(-1)!.split('-')[0]);
        else throw Error(`Unexpected fixture endpoint ${url}`);
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
});
afterEach(async () => { if (renderer) await act(async () => renderer!.unmount()); renderer = undefined; client?.clear(); api.defaults.adapter = original; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('loads the full native population beyond top30 and keeps zero separate from absent data', async () => {
    const result = await readNativeComparison(job('a'));
    expect(result.complete).toBe(true); expect(result.total).toBe(37); expect(result.records).toHaveLength(37);
    expect(calls).toEqual([{ job: 'a', stage: 'draw', offset: 0 }, { job: 'a', stage: 'draw', offset: 32 }, { job: 'a', stage: 'retained', offset: 0 }]);
    expect(nativeComparisonValues(result.records[0]).i_pTM).toBe(0);
    expect(nativeComparisonValues(result.records[1]).i_pTM).toBeUndefined();
    expect(nativeComparisonValues(result.records[1]).outcome).toBe('rejected');
});
it('uses only explicit producer ranked-to-scored binding and exact document identities', async () => {
    const result = await readNativeComparison(job('a'));
    const byDesign = nativeComparisonSelection(result, ['a-design-0'], []);
    const byRankedArtifact = nativeComparisonSelection(result, [], [{ job_id: 'a', artifact_id: 'a-ranked-0' }]);
    expect(byDesign).toHaveLength(1); expect(byRankedArtifact).toEqual(byDesign);
    expect((byDesign[0].native_record as any).design).toBe('native-0');
    expect(nativeComparisonSelection(result, [], [{ job_id: 'a', artifact_id: 'a-draw-1' }])).toHaveLength(1);
    expect(nativeComparisonSelection(result, [], [{ job_id: 'foreign', artifact_id: 'a-draw-1' }])).toEqual([]);
    expect(nativeComparisonSelection(result, ['native-0'], [])).toEqual([]);
    expect(nativeComparisonKey('a', result.records[0], 0)).not.toBe(nativeComparisonKey('b', result.records[0], 0));
});
it('retains loaded native rows after later-page failure without claiming complete coverage', async () => {
    failLater = true;
    const result = await readNativeComparison(job('a'));
    expect(result.complete).toBe(false); expect(result.records).toHaveLength(32); expect(result.total).toBe(37); expect(result.error).toContain('page unavailable');
});
it('terminates repeated pagination without duplicated observations or an infinite loop', async () => {
    duplicateLater = true;
    const result = await readNativeComparison(job('a'));
    expect(result.complete).toBe(false); expect(result.records).toHaveLength(32); expect(calls).toHaveLength(2);
});
it('renders selected ranked and rejected observations together without embedding a campaign dashboard', async () => {
    await mount(<NativeBinderComparison jobIds={['a']} selectedDesignIds={['a-design-0']} selectedNativeSources={[{ job_id: 'a', artifact_id: 'a-draw-1' }]} />);
    expect(text(renderer!.root)).toContain('Selected candidates');
    expect(text(renderer!.root)).not.toContain('Campaign results');
    expect(text(renderer!.root)).not.toContain('Native campaign actions');
    const row = renderer!.root.findAllByType('tr')[1];
    expect(row.findAllByType('td')[1].children).toEqual(['2']);
    const before = calls.length;
    await act(async () => renderer!.root.findByProps({ 'aria-label': 'Comparison population' }).props.onChange({ target: { value: 'rejected' } }));
    expect(renderer!.root.findAllByType('tr')[1].findAllByType('td')[1].children).toEqual(['1']);
    expect(calls).toHaveLength(before);
});
it('keeps comparison demand-loaded and passes native selection and nonrecursive Design scope through its actual parent', async () => {
    await mount(<BinderResultComparison jobId="a" selectedDesignIds={['a-design-0']} selectedNativeSources={[{ job_id: 'a', artifact_id: 'a-draw-1' }]} />);
    expect(calls).toHaveLength(0); expect(apiCalls).toHaveLength(0);
    const button = renderer!.root.findAllByType('button').find(node => text(node) === 'Compare result sets')!;
    await act(async () => button.props.onClick()); await flush();
    expect(renderer!.root.findByType(NativeBinderComparison).props.selectedDesignIds).toEqual(['a-design-0']);
    expect(renderer!.root.findByType(NativeBinderComparison).props.selectedNativeSources).toEqual([{ job_id: 'a', artifact_id: 'a-draw-1' }]);
    expect(apiCalls.filter(call => call.url === '/api/designs').every(call => call.params.include_children === false)).toBe(true);
    const saved = JSON.parse(localStorage.getItem('binder-result-comparison::a')!);
    expect(saved.native).toEqual([{ job_id: 'a', artifact_id: 'a-draw-1' }]);
    expect(saved.designs).toEqual(['a-design-0']);
});

// Shared native wire-shaped fixtures only; no model inference or measured biology.
const nativeSource = { job_id: 'a', artifact_id: 'native-qa' };
function profileWire(id: string, artifact: string, native = false) {
    const value: any = esmfold2Fixture(id).confidence;
    value.document.documentId = `artifact:${artifact}`;
    value.axis.producer_version = 'inert-native-profile';
    if (native) {
        value.design_id = null;
        value.producer_binding.candidate_id = artifact;
        value.axis.candidate_id = artifact;
    }
    return value;
}
it('accepts null Design identity only in the selected exact native artifact context', () => {
    const id = 'native:a:native-qa', wire = profileWire(id, 'native-qa', true);
    const parsed = parseScientificNativeMetric(wire, wire.document, 'residue_plddt', id, nativeSource);
    expect(parsed.status).toBe('ok');
    if (parsed.status !== 'ok') throw Error('fixture not received');
    expect(parsed.values).toEqual([.005, .8, .4]);
    expect(parsed.residues[0].insertionCode).toBe('A');
    expect(parseScientificNativeMetric(wire, wire.document, 'residue_plddt', id).status).toBe('unavailable');
    for (const source of [{ job_id: 'foreign', artifact_id: 'native-qa' }, { job_id: 'a', artifact_id: 'foreign' }]) {
        expect(parseScientificNativeMetric(wire, wire.document, 'residue_plddt', id, source).status).toBe('unavailable');
    }
    for (const change of ['hash', 'document', 'binding', 'design']) {
        const altered = structuredClone(wire);
        if (change === 'hash') altered.axis.source_sha256 = 'b'.repeat(64);
        if (change === 'document') altered.document.documentId = 'primary';
        if (change === 'binding') { altered.producer_binding.candidate_id = 'foreign'; altered.axis.candidate_id = 'foreign'; }
        if (change === 'design') altered.design_id = id;
        expect(parseScientificNativeMetric(altered, wire.document, 'residue_plddt', id, nativeSource).status).toBe('unavailable');
    }
});
it('compares exact alternate Design and native QA profiles without borrowing primary confidence', async () => {
    const selected = { artifact_id: 'alternate-qa', target_state: 'open' };
    const native = { ...nativeSource, document: { target_state: 'open' } } as any;
    const designWire = profileWire('a-design-0', 'alternate-qa'), nativeWire = profileWire('native:a:native-qa', 'native-qa', true);
    api.defaults.adapter = async config => {
        const url = String(config.url); apiCalls.push({ url, params: config.params });
        let data: unknown;
        if (url === '/api/designs/a-design-0') data = { ...design('a'), scientific_structure_document: designWire.document };
        else if (url === '/api/designs/a-design-0/residue-metrics') data = designWire;
        else if (url === '/api/designs/native/a/native-qa/document') data = { status: 'ok', document: nativeWire.document, candidate_ref: { kind: 'native_document', ...nativeSource } };
        else if (url === '/api/designs/native/a/native-qa/residue-metrics') data = nativeWire;
        else throw Error(`Unexpected profile request ${url}`);
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
    await mount(<DesignComparePane designs={[design('a') as any]} selectedDesignIds={['a-design-0']} candidateDocuments={{ 'a-design-0': selected }} selectedNativeSources={[native]} />);
    const profiles = () => JSON.parse(decodeURIComponent(renderer!.root.findByProps({ download: 'native-confidence-comparison.json' }).props.href.split(',').slice(1).join(','))).profiles;
    expect(profiles()).toHaveLength(2);
    expect(profiles().map((p: any) => p.metric.status)).toEqual(['ok', 'ok']);
    expect(profiles().map((p: any) => p.metric.document.documentId)).toEqual(['artifact:alternate-qa', 'artifact:native-qa']);
    expect(apiCalls.filter(c => c.url.startsWith('/api/designs/a-design-0')).every(c => c.params.artifact_id === 'alternate-qa' && c.params.target_state === 'open')).toBe(true);
    expect(apiCalls.filter(c => c.url.startsWith('/api/designs/native/')).every(c => c.params.target_state === 'open')).toBe(true);
    const traces = JSON.parse(renderer!.root.findByProps({ 'data-comparison-plot': true }).props['data-traces']);
    expect(traces).toHaveLength(6);
    expect(traces[0].y).toEqual([.5]);
    expect(traces[1].y).toEqual([80]);
    await act(async () => renderer!.root.findByProps({ 'aria-label': 'Comparison chain' }).props.onChange({ target: { value: 'B' } }));
    expect(JSON.parse(renderer!.root.findByProps({ 'data-comparison-plot': true }).props['data-traces'])).toHaveLength(2);
    const expectedDownload = new URL(downloadDesignPdb('a-design-0', selected), 'http://fixture');
    expect(expectedDownload.searchParams.get('artifact_id')).toBe('alternate-qa');
    expect(expectedDownload.searchParams.get('target_state')).toBe('open');
    // A different exact document without QA must not keep the previously cached vector.
    api.defaults.adapter = async config => ({ config, status: 200, statusText: 'OK', headers: {}, data: String(config.url).endsWith('/residue-metrics')
        ? { schema_name: 'core_protein_viewer_metric', schema_version: 1, contract_revision: 1, design_id: 'a-design-0', design_name: 'No QA', metric: 'residue_plddt', status: 'unavailable', reason: 'native_qa_absent',
            ...Object.fromEntries(['document','artifact_sha256','producer_binding','row_axis','column_axis','native_shape','native_row_positions','native_column_positions','sampled_row_indices','sampled_column_indices','pae_matrix','size'].map(key => [key, null])) }
        : { ...design('a'), scientific_structure_document: { ...designWire.document, documentId: 'artifact:scored-no-qa' } } });
    await act(async () => renderer!.update(<ThemeProvider><QueryClientProvider client={client!}><DesignComparePane designs={[design('a') as any]} selectedDesignIds={['a-design-0']} candidateDocuments={{ 'a-design-0': { artifact_id: 'scored-no-qa' } }} /></QueryClientProvider></ThemeProvider>));
    await flush();
    expect(profiles()).toHaveLength(1);
    expect(profiles()[0].metric).toEqual({ status: 'unavailable', reason: 'native_qa_absent' });
    expect(renderer!.root.findAllByProps({ 'data-comparison-plot': true })).toHaveLength(0);
});
