import { CohortMetricPicker } from '../../src/components/CohortMetricPicker';
import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { ResultsViewer } from '../../src/components/ResultsViewer';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { ThemeProvider } from '../../src/components/ThemeProvider';

// Inert renderer only. The query owners, native workbench, structure parser,
// operation controls and submission serializers run unchanged.
const engines = vi.hoisted(() => ({ plotImports: 0, mounts: 0, urls: [] as string[] }));
vi.mock('react-plotly.js', () => { engines.plotImports++; return { default: () => <div data-plot /> }; });
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: (props: any) => {
    React.useEffect(() => { engines.mounts++; engines.urls.push(props.structureUrl); }, []);
    return <div data-native-url={props.structureUrl} />;
} }));
vi.mock('../../src/components/EpitopeMolstarViewerImpl', () => ({ default: () => <div /> }));
let tree: ReactTestRenderer | undefined;
let client: QueryClient;
const original = api.defaults.adapter;
const calls: Array<{ url: string; params: any; body: any }> = [];
const downloads = vi.fn(async (_url: string, _options?: unknown) => ({ ok: false, status: 404 }));
const job = { id: 'parent', name: 'TEST native generation', model_id: 'ppiflow', mode: 'protein_binder', status: 'completed', params: {}, design_count: 1, created_at: '2026-09-01T00:00:00Z' };
const doc = { artifact_id: 'native-primary', primary: true, sha256: 'a'.repeat(64), target_state: 'A', logical_path: 'native/a.pdb', download_url: '/api/files/native/a.pdb' };
const alternate = { artifact_id: 'native-alternate', sha256: 'b'.repeat(64), target_state: 'B', logical_path: 'native/b.cif', download_url: '/api/files/native/b.cif' };
const record = { candidate_key: 'producer:exact', design_id: 'exact', metrics: { count: 0, flag: false, absent: null }, structures: [doc, alternate] };
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (label: string) => tree!.root.findAllByType('button').find(node => text(node) === label)!;
const flush = async () => { for (let i = 0; i < 12; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); }); };
const click = async (label: string) => { await act(async () => button(label).props.onClick()); await flush(); };
function transport(records = [record], jobs: any[] = [job]) {
    calls.length = 0;
    api.defaults.adapter = async config => {
        const url = String(config.url), params = config.params ?? {}, body = config.data ? JSON.parse(config.data) : undefined;
        calls.push({ url, params, body });
        let data: any = [];
        if (url === '/api/jobs') data = { jobs, total: jobs.length };
        else if (url === '/api/jobs/parent') data = job;
        else if (url === '/api/designs') data = { designs: [], total: 0, model_counts: {} };
        else if (url.endsWith('/generation-results')) data = { records: records.slice(params.offset, params.offset + params.limit), total: records.length, offset: params.offset, limit: params.limit, receipt: {}, publication: {}, artifacts: [] };
        else if (url.endsWith('/binder-evidence')) data = { records: [], total: 0, offset: 0, limit: 100 };
        else if (url.endsWith('/round')) data = { job_id: 'parent', state: 'not_requested', steps: {}, errors: {} };
        else if (url.endsWith('/selection-context')) data = { candidate_documents: { exact: [doc, alternate] }, targets: [] };
        else if (url.includes('/integration')) data = { enabled: false };
        else if (url.startsWith('/api/models/')) data = { params: [
            { name: 'maturation_repack_enabled', label: 'Repack', type: 'boolean', default: false },
            { name: 'samples', label: 'Samples', type: 'integer', default: 1, minimum: 0 },
        ] };
        else if (url === '/api/designs/exact') data = { id: 'exact', job_id: 'parent', name: 'exact', pdb_path: 'primary-is-not-native.pdb', provenance: { model_id: 'ppiflow' }, viewer_capabilities: [], supported_analyzers: [] };
        else if (url === '/api/binder-continuation/selected') data = { launched_jobs: [{ id: 'child', name: 'native child' }] };
        return { config, data, status: 200, statusText: 'OK', headers: {} };
    };
}
async function mount(element: React.ReactNode, route = '/') {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<MemoryRouter initialEntries={[route]}><QueryClientProvider client={client}><ThemeProvider>{element}</ThemeProvider></QueryClientProvider></MemoryRouter>); });
    await flush();
}
beforeEach(() => {
    downloads.mockClear(); engines.mounts = 0; engines.urls = []; vi.stubGlobal('fetch', downloads);
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
});
afterEach(async () => { await act(async () => tree?.unmount()); tree = undefined; client?.clear(); api.defaults.adapter = original; vi.unstubAllGlobals(); sessionStorage.clear(); });

it('empty Results imports neither Plotly nor the native structure engine and makes no file requests', async () => {
    transport([], []);
    await mount(<ResultsViewer />);
    expect(engines.plotImports).toBe(0);
    expect(downloads).not.toHaveBeenCalled();
    expect(tree!.root.findAllByProps({ 'data-plot': true })).toHaveLength(0);
    expect(calls.filter(call => /generation-results|selection-context|execution-targets|\/round$/.test(call.url))).toHaveLength(0);
});

it('zero-yield native publication initializes no Plotly or structure workbench', async () => {
    transport([]);
    await mount(<NativeBinderGenerationResults jobId="parent" status="completed" />);
    expect(text(tree!.root)).toContain('published zero-yield result');
    expect(engines.plotImports).toBe(0);
    expect(engines.mounts).toBe(0);
    expect(downloads).not.toHaveBeenCalled();
});

it('dashboard defers candidate PDB work until first inspection, then retains the opened session and axes', async () => {
    transport(); await mount(<NativeBinderGenerationResults jobId="parent" status="completed" />);
    expect(downloads).not.toHaveBeenCalled();
    expect(tree!.root.findAllByProps({ 'aria-label': 'Published document' })).toHaveLength(0);
    await click('Plotly Lab');
    const axis = () => tree!.root.findAllByType(CohortMetricPicker).find(node => node.props.label === '2D X metric')!;
    await act(async () => axis().props.onChange('count'));
    await click('producer:exact');
    expect(engines.mounts).toBe(1);
    expect(engines.urls).toEqual([doc.download_url]);
    const inspector = tree!.root.findByProps({ 'aria-label': 'Published document' });
    await click('Plotly Lab');
    expect(tree!.root.findByProps({ 'aria-label': 'Published document' })).toBe(inspector);
    expect(axis().props.value).toBe('count');
    await click('Structure');
    expect(engines.mounts).toBe(1);
    expect(calls.filter(call => call.url.endsWith('/generation-results'))).toHaveLength(1);
});

it('deep links beyond the first cohort page demand only the exact alternate document', async () => {
    const rows = Array.from({ length: 1001 }, (_, i) => ({ ...record, candidate_key: `row:${i}`, design_id: i === 1000 ? 'exact' : `other-${i}` }));
    transport(rows);
    const plotImportsBefore = engines.plotImports;
    await mount(<NativeBinderGenerationResults jobId="parent" status="completed" selectedDesignId="exact" artifactId={alternate.artifact_id} targetState="B" />);
    expect(calls.filter(call => call.url.endsWith('/generation-results')).map(call => call.params.offset)).toEqual([0, 1000]);
    expect(engines.mounts).toBe(1);
    expect(engines.urls).toEqual([alternate.download_url]);
    expect(tree!.root.findByProps({ 'aria-label': 'Published document' }).props.value).toBe('1');
    expect(tree!.root.findAllByProps({ 'data-plot': true })).toHaveLength(0);
    expect(engines.plotImports).toBe(plotImportsBefore);
    expect(text(tree!.root)).toContain('row:1000');
});

it('collapsed candidate operations do no picker discovery; first open preserves drafts and exact submission across close/reopen', async () => {
    transport();
    await mount(<Routes><Route path="/designs/:jobId" element={<ResultsViewer />} /></Routes>, '/designs/parent?design_id=exact&artifact_id=native-alternate&target_state=B&launch_context_id=destination');
    const disclosure = () => tree!.root.findAllByType('details').find(node => text(node.findAllByType('summary')[0]).startsWith('Selected candidate operations'))!;
    expect(calls.filter(call => /selection-context|execution-targets/.test(call.url))).toHaveLength(0);
    expect(button('Run selected operation')).toBeUndefined();
    await act(async () => disclosure().props.onToggle({ currentTarget: { open: true } })); await flush();
    expect(calls.some(call => call.url.endsWith('/selection-context'))).toBe(true);
    const field = () => tree!.root.findAll(node => typeof node.type === 'function' && node.props.param?.name === 'samples')[0].findByType('input');
    await act(async () => field().props.onChange({ target: { value: '0' } }));
    const requestCount = calls.length;
    await act(async () => disclosure().props.onToggle({ currentTarget: { open: false } }));
    await act(async () => disclosure().props.onToggle({ currentTarget: { open: true } })); await flush();
    expect(field().props.value).toBe(0);
    expect(calls).toHaveLength(requestCount);
    await click('Run selected operation');
    expect(calls.find(call => call.url === '/api/binder-continuation/selected')!.body).toMatchObject({
        source_job_id: 'parent', design_ids: ['exact'], launch_context_id: 'destination',
        candidate_documents: { exact: { artifact_id: 'native-alternate', target_state: 'B' } },
        params: { samples: 0, maturation_repack_enabled: false },
    });
    expect(downloads.mock.calls.every(call => call[0] === alternate.download_url)).toBe(true);
});
