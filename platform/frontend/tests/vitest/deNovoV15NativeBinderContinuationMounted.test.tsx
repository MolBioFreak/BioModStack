import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NativeBinderGenerationResults } from '../../src/components/NativeBinderGenerationResults';
import BinderSelectedControls from '../../src/components/BinderSelectedControls';
import { api } from '../../src/lib/api';
import { readBinderNativeSources } from '../../src/lib/binderContinuation';
vi.mock('../../src/components/CohortAnalytics', () => ({ CohortAnalytics: () => null }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => ({ StructureWorkbench: () => null }));
const doc = (artifact_id: string) => ({ artifact_id, target_state: artifact_id, logical_path: `${artifact_id}.cif`, download_url: `/exact/${artifact_id}.cif` });
const records = [
    { candidate_key: 'rejected', outcome: 'rejected', structures: [doc('reject')] },
    { candidate_key: 'missing', structures: [] },
    { candidate_key: 'states', structures: [doc('primary'), doc('alternate')] },
    ...Array.from({ length: 24 }, (_, i) => ({ candidate_key: `page-${i}`, structures: [doc(`p${i}`)] })),
];
let tree: ReactTestRenderer;
let client: QueryClient;
const original = api.defaults.adapter;
const posts: any[] = [];
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (name: string) => tree.root.findAllByType('button').find(node => text(node) === name)!;
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); }); };
async function mount(stage = 'draw') {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}><NativeBinderGenerationResults jobId="job" adapter={{ key: JSON.stringify(['bindcraft2', 'arm-a', stage]), title: stage, label: row => row.candidate_key!, fetchPage: async () => ({ records, total: records.length, offset: 0, limit: 100, receipt: {}, publication: {} }) }} /></QueryClientProvider>); });
    await flush();
}
async function close() { await act(async () => tree.unmount()); client.clear(); }
beforeEach(() => { sessionStorage.clear(); posts.length = 0; api.defaults.adapter = async config => {
    let data: any = [];
    if (config.url?.startsWith('/api/models/')) data = { params: [{ name: 'samples', label: 'Samples', type: 'integer', default: 1 }] };
    if (config.url?.startsWith('/api/designs/')) data = { job_id: 'accepted-owner' };
    if (config.url?.endsWith('/selection-context')) data = { candidate_documents: {} };
    if (config.method === 'post') { posts.push(JSON.parse(config.data)); data = { launched_jobs: [] }; }
    return { config, status: 200, statusText: 'OK', headers: {}, data };
}; });
afterEach(async () => { await close(); api.defaults.adapter = original; sessionStorage.clear(); vi.unstubAllGlobals(); });
it('retains checked metrics-only records and rejected saved molecules across pages, scopes and reopen', async () => {
    await mount();
    for (const key of ['rejected', 'missing']) await act(async () => tree.root.findByProps({ 'aria-label': `Select ${key}` }).props.onChange({ target: { checked: true } }));
    expect(text(tree.root)).toContain('No saved molecule');
    await act(async () => button('Next native records').props.onClick());
    await act(async () => tree.root.findByProps({ 'aria-label': 'Select page-23' }).props.onChange({ target: { checked: true } }));
    expect(readBinderNativeSources('job').map(s => s.artifact_id)).toEqual(['reject', 'p23']);
    await close(); await mount('trajectory');
    expect(tree.root.findByProps({ 'aria-label': 'Select missing' }).props.checked).toBe(false);
    await close(); await mount();
    expect(tree.root.findByProps({ 'aria-label': 'Select missing' }).props.checked).toBe(true);
    expect(tree.root.findByProps({ 'aria-label': 'Select rejected' }).props.checked).toBe(true);
    expect(readBinderNativeSources('other')).toEqual([]);
});
it('requires explicit multi-document selection and preserves exact receiving-editor return links', async () => {
    await mount();
    await act(async () => tree.root.findByProps({ 'aria-label': 'Select states' }).props.onChange({ target: { checked: true } }));
    expect(readBinderNativeSources('job')).toEqual([]);
    await act(async () => button('states').props.onClick());
    await act(async () => tree.root.findByProps({ 'aria-label': 'Published document' }).props.onChange({ target: { value: '1' } }));
    await act(async () => tree.root.findByProps({ 'aria-label': 'Use native document alternate' }).props.onChange({ target: { checked: true } }));
    expect(readBinderNativeSources('job').map(s => s.artifact_id)).toEqual(['alternate']);
    const link = tree.root.findAllByType('a').find(a => text(a) === 'Design sequence from exact structure')!;
    const url = new URL(link.props.href, 'http://localhost');
    expect(JSON.parse(url.searchParams.get('source_structure')!)).toEqual({ job_id: 'job', document: { artifact_id: 'alternate', target_state: 'alternate' } });
    const back = new URL(url.searchParams.get('return_to')!, 'http://localhost');
    expect(back.searchParams.get('native_candidate')).toBe('states');
    expect(back.searchParams.get('artifact_id')).toBe('alternate');
    expect(JSON.parse(back.searchParams.get('native_scope')!)).toEqual(['bindcraft2', 'arm-a', 'draw']);
});
it('submits restored mixed or native-only sources through the editable existing operation owner', async () => {
    await mount();
    await act(async () => tree.root.findByProps({ 'aria-label': 'Select rejected' }).props.onChange({ target: { checked: true } }));
    await close();
    for (const designIds of [['accepted-design'], []]) {
        client = new QueryClient();
        await act(async () => { tree = create(<QueryClientProvider client={client}><BinderSelectedControls sourceJobId="job" selectedDesignIds={designIds} selectedNativeSources={readBinderNativeSources('job')} onOpenJob={() => {}} onStartMD={() => {}} /></QueryClientProvider>); });
        await flush();
        await act(async () => tree.root.findAllByType('input').find(n => n.props.type === 'number')!.props.onChange({ target: { value: '7' } }));
        expect(button('Run selected operation').props.disabled).toBe(false);
        await act(async () => button('Run selected operation').props.onClick());
        expect(posts.at(-1)).toMatchObject({ design_ids: designIds, native_sources: [{ job_id: 'job', artifact_id: 'reject' }], params: { samples: 7 } });
        expect(posts.at(-1).native_sources[0]).not.toHaveProperty('document');
        if (designIds.length) await close();
    }
});
it('inspects only the chosen registered source and reports missing download without primary fallback or blocking submission', async () => {
    const fetcher = vi.fn(async () => ({ ok: false, status: 404 }));
    vi.stubGlobal('fetch', fetcher);
    for (const available of [true, false]) {
        client = new QueryClient();
        await act(async () => { tree = create(<QueryClientProvider client={client}><BinderSelectedControls sourceJobId="job" selectedDesignIds={[]} selectedNativeSources={[{ job_id: 'job', artifact_id: 'alternate', document: available ? doc('alternate') : undefined }]} onOpenJob={() => {}} onStartMD={() => {}} /></QueryClientProvider>); });
        await flush();
        await act(async () => button('Inspect exact native source alternate').props.onClick());
        await flush();
        expect(fetcher).toHaveBeenCalledTimes(1);
        expect(fetcher.mock.calls[0][0]).toBe('/exact/alternate.cif');
        expect(text(tree.root)).toContain(available ? 'Selected source inspection failed (404)' : 'No primary is substituted');
        expect(button('Run selected operation').props.disabled).toBe(false);
        if (available) await close();
    }
});
