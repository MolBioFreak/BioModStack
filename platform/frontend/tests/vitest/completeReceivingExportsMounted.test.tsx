import React from 'react';
import { readFileSync, writeFileSync } from 'node:fs';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import { DesignComparePane } from '../../src/components/DesignComparePane';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { api } from '../../src/lib/api';
import { writeRoundStorage, readRoundStorage } from '../../src/lib/binderSelectedRound';

vi.mock('react-plotly.js', () => ({ default: () => <div data-comparison-renderer /> }));
vi.mock('../../src/components/MolstarViewerImpl', () => ({ default: () => <div data-inert-viewer /> }));

// Run API test_complete_receiving_exports.py first: this is its actual emitted/read page,
// not an invented model response. Source coordinates are explicitly inert test atoms.
const emitted = () => {
    if (!process.env.BMS_RECEIVING_PAGE) throw Error('Set BMS_RECEIVING_PAGE to API emitter-reader-partial.json from the receiving API tests');
    return JSON.parse(readFileSync(process.env.BMS_RECEIVING_PAGE, 'utf8'));
};
let tree: ReactTestRenderer;
let client: QueryClient;
let blobs: Map<string, Blob>;
const original = api.defaults.adapter;
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const flush = async () => { for (let i = 0; i < 16; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
async function mount(node: React.ReactNode) {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<ThemeProvider><QueryClientProvider client={client}>{node}</QueryClientProvider></ThemeProvider>); });
    await flush();
}
async function download(label: string, file?: string) {
    const link = tree.root.findAllByType('a').find(node => text(node) === label)!;
    expect(link.props.href).toMatch(/^blob:/);
    const blob = blobs.get(link.props.href)!;
    const content = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob); });
    if (file && process.env.BMS_RECEIVING_EVIDENCE) writeFileSync(`${process.env.BMS_RECEIVING_EVIDENCE}/${file}`, content);
    return content;
}
beforeEach(() => {
    localStorage.clear(); sessionStorage.clear(); window.history.replaceState({}, '', '/');
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    blobs = new Map(); vi.spyOn(URL, 'createObjectURL').mockImplementation(blob => { const url = `blob:receiving-${blobs.size}`; blobs.set(url, blob as Blob); return url; });
    api.defaults.adapter = async config => {
        if (config.method !== 'get' || config.url !== '/api/jobs/job') throw Error(`Unexpected ${config.method} ${config.url}`);
        return { config, status: 200, statusText: 'OK', headers: {}, data: { id: 'job', saved_selection_sets: [] } };
    };
});
afterEach(async () => { await act(async () => tree?.unmount()); client?.clear(); api.defaults.adapter = original; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('downloads actual emitted native values with failed-page scope, exact refs and CSV companion; partial export stays enabled', async () => {
    const source = emitted(), page = source.page, row = page.records[0];
    const missing = { ...row.candidate_ref, artifact_id: 'unavailable-selected', model: 2 };
    writeRoundStorage('job', 'selection', [row.candidate_ref, missing]);
    const offsets: number[] = [];
    await mount(<NativeBinderGenerationResults jobId="job" adapter={{ key: 'receiving-real-emitter', title: 'Native rows', label: () => 'Emitted candidate', fetchPage: async offset => {
        offsets.push(offset); if (offset) throw Error('retained page two unavailable'); return page;
    } }} />);
    expect(offsets).toEqual([0, 1]);
    const envelope = JSON.parse(await download('Native JSON', 'download-partial.json'));
    const metadata = JSON.parse(await download('CSV scope metadata', 'download-partial.metadata.json'));
    const csv = await download('Metrics CSV', 'download-partial.csv');
    for (const value of [envelope, metadata]) {
        expect(value.schema).toBe('bms.native-generation-export.v1');
        expect(value.model_id).toBe('rfd4_proteina');
        expect(value.counts).toEqual({ total_known: 3, loaded: 1, exported: 1 });
        expect(value.scope).toBe('filtered');
        expect(value.filter).toEqual({ search: '', population: 'all', filters: [], selected_only: false });
        expect(value.rank).toMatchObject({ scope: 'matching', missing: 'last' });
        expect(value.completeness).toMatchObject({ loaded_complete: false, execution_status: 'failed', native_execution_status: 'failed', read_error: 'Error: retained page two unavailable' });
        expect(value.unresolved_selected_refs).toContainEqual(missing);
        expect(value.records[0].candidate_ref).toEqual(row.candidate_ref);
        expect(value.records[0].documents).toEqual(row.structures);
    }
    expect(envelope.records[0].native_record.metrics).toEqual(source.native_records[0].metrics);
    expect(envelope.records[0].native_record.metrics).toMatchObject({ reward: '0', missing: '', save_id: '0', oracle_config: 'conditioned-native' });
    expect(metadata.records[0].csv_row).toBe(2);
    expect(csv.split('\r\n')).toHaveLength(2);
    expect(csv).toContain('"conditioned-native"');
    expect(csv).not.toContain('retained page two unavailable');
    for (const scope of ['selected', 'all']) {
        await act(async () => tree.root.findByProps({ 'aria-label': 'Export scope' }).props.onChange({ target: { value: scope } }));
        const exported = JSON.parse(await download('Native JSON', `download-${scope}.json`));
        expect(exported.scope).toBe(scope); expect(exported.counts.exported).toBe(1);
        expect(exported.unresolved_selected_refs).toContainEqual(missing);
    }
});

it('exports a retained zero-yield failed receipt without inventing a candidate', async () => {
    const input = JSON.parse(readFileSync(process.env.BMS_RECEIVING_PAGE!.replace('partial.json', 'zero.json'), 'utf8'));
    await mount(<NativeBinderGenerationResults jobId="job" adapter={{ key: 'zero', title: 'Zero', label: () => '', fetchPage: async () => input.page }} />);
    const envelope = JSON.parse(await download('Native JSON', 'download-zero.json'));
    expect(envelope.counts).toEqual({ total_known: 0, loaded: 0, exported: 0 });
    expect(envelope.records).toEqual([]);
    expect(envelope.completeness).toMatchObject({ loaded_complete: true, execution_status: 'failed' });
    expect(envelope.receipt.metrics_enabled).toBe(false);
});

it('bridges MODEL-qualified saved refs through the existing selection owner into comparison sources, retaining off-view refs', async () => {
    const row = emitted().page.records[0], selected = { ...row.candidate_ref, model: 2 }, offView = { ...selected, job_id: 'child', artifact_id: 'off-view' };
    const changed = vi.fn();
    await mount(<NativeBinderGenerationResults jobId="job" onSelectedNativeSourcesChange={changed} adapter={{ key: 'models', title: 'Models', label: () => 'Exact', fetchPage: async () => ({ ...emitted().page, total: 1, records: [{ ...row, candidate_ref: selected }] }) }} />);
    await act(async () => tree.root.findByProps({ 'aria-label': 'Select Exact' }).props.onChange({ target: { checked: true } }));
    expect(changed.mock.calls.at(-1)![0][0].document.model).toBe(2);
    await act(async () => { window.dispatchEvent(new CustomEvent('bms:binder-round-selection', { detail: { jobId: 'job', refs: [selected, offView], replace: true } })); });
    expect(changed.mock.calls.at(-1)![0].map((source: any) => [source.job_id, source.artifact_id, source.document.model])).toEqual([['job', selected.artifact_id, 2], ['child', 'off-view', 2]]);
    expect(readRoundStorage('job', 'selection', [])).toEqual([selected]); // event consumes, durable set owner writes
});

it('isolates alternate document and MODEL cache/query/download identity; a model with no QA never borrows another vector', async () => {
    const calls: any[] = [];
    const profiles = JSON.parse(readFileSync(process.env.BMS_RECEIVING_PAGE!.replace('emitter-reader-partial.json', 'native-profiles.json'), 'utf8'));
    const [native, noQa] = profiles;
    const wire = native.metric, artifact = native.source.artifact_id;
    const source = (model: number, id = artifact) => ({ job_id: 'job', artifact_id: id, document: { artifact_id: id, model } });
    api.defaults.adapter = async config => {
        calls.push(config);
        const data = config.url?.endsWith('/document') ? native.metadata : wire;
        return { config, status: 200, statusText: 'OK', headers: {}, data };
    };
    const node = (sources: ReturnType<typeof source>[]) => <ThemeProvider><QueryClientProvider client={client}><DesignComparePane designs={[]} selectedNativeSources={sources} /></QueryClientProvider></ThemeProvider>;
    await mount(<DesignComparePane designs={[]} selectedNativeSources={[source(1)]} />);
    const exported = () => JSON.parse(decodeURIComponent(tree.root.findByProps({ download: 'native-confidence-comparison.json' }).props.href.split(',').slice(1).join(',')));
    const first = exported(); expect(first.profiles[0].metric.status).toBe('ok');
    expect(first.profiles[0].metric.values).toEqual([0]);
    expect(first.profiles[0].metric.document).toEqual(wire.document);
    expect(calls).toHaveLength(2); expect(calls.every(config => config.params.model_number === 1)).toBe(true);
    await act(async () => tree.update(node([source(2)]))); await flush();
    const second = exported();
    expect(second.selection.native_sources[0].document.model).toBe(2);
    expect(second.profiles[0].metric).toEqual({ status: 'unavailable', reason: 'Native confidence unavailable for MODEL 2' });
    expect(second.profiles[0].id).not.toBe(first.profiles[0].id);
    expect(calls).toHaveLength(4); expect(calls.slice(2).every(config => config.params.model_number === 2)).toBe(true);
    expect(tree.root.findAllByProps({ 'data-comparison-renderer': true })).toHaveLength(0);
    await act(async () => tree.update(node([source(1)]))); await flush();
    expect(calls).toHaveLength(4); expect(exported().profiles[0].metric.values).toEqual(first.profiles[0].metric.values);
    // Different document has no QA. Real parser receives the explicit unavailable shape.
    api.defaults.adapter = async config => { calls.push(config); return { config, status: 200, statusText: 'OK', headers: {}, data: config.url?.endsWith('/document') ? noQa.metadata : noQa.metric }; };
    await act(async () => tree.update(node([source(1, noQa.source.artifact_id)]))); await flush();
    expect(calls.at(-1).url).toContain(`/${noQa.source.artifact_id}/`);
    expect(exported().profiles[0].metric).toEqual({ status: 'unavailable', reason: noQa.metric.reason });
    api.defaults.adapter = async config => ({ config, status: 200, statusText: 'OK', headers: {}, data: config.url?.endsWith('/document') ? native.metadata : wire });
    await act(async () => tree.update(node([source(1), source(2)]))); await flush();
    expect(new Set(exported().profiles.map((profile: any) => profile.id)).size).toBe(2);
    expect(exported().profiles.map((profile: any) => profile.metric.status)).toEqual(['ok', 'unavailable']);
});
