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
import { BatchComparePane } from '../../src/components/BatchComparePane';
import { parseScientificNativeMetric } from '../../src/lib/scientificViewerIdentity';
import { esmfold2Fixture } from '../fixtures/scientificViewerEsmfold2Fixture';
vi.mock('react-plotly.js', () => ({ default: (props: any) => <div data-comparison-plot onClick={props.onClick} data-traces={JSON.stringify(props.data)} /> }));

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
it('comparison Open candidate retains selected MODEL and the exact native record key', async () => {
    await mount(<NativeBinderComparison jobIds={['a']} selectedNativeSources={[{ job_id: 'a', artifact_id: 'a-draw-0', document: { artifact_id: 'a-draw-0', model: 7, target_state: 'open' } }]} />);
    await vi.waitFor(async () => { await flush(); expect(renderer!.root.findAllByProps({ 'data-comparison-plot': true })).toHaveLength(1); });
    const plot = renderer!.root.findByProps({ 'data-comparison-plot': true });
    const traces = JSON.parse(plot.props['data-traces']);
    const exact = traces[0].customdata[0];
    await act(async () => plot.props.onClick({ points: [{ customdata: exact }] }));
    const link = renderer!.root.findAllByType('a').find(node => text(node) === 'Open candidate')!;
    const query = new URL(link.props.href, 'http://fixture').searchParams;
    expect(query.get('model_number')).toBe('7');
    expect(query.get('native_candidate')).toBe(JSON.parse(exact)[1]);
    expect(query.get('artifact_id')).toBe('a-draw-0');
});
