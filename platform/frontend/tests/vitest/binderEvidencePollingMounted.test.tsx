import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BinderPredictionEvidence } from '../../src/components/BinderPredictionEvidence';
import { api } from '../../src/lib/api';
vi.mock('react-plotly.js', () => ({ default: () => <div data-native-chart /> }));
const original = api.defaults.adapter;
let tree: ReactTestRenderer;
let client: QueryClient;
let calls: any[];
let fail = false;
let late = false;
const row = (id: string) => ({ source_design_id: id, candidate_key: id, sequences: late ? [{ job_id: 'late-designer', design_id: 'seq', name: 'late sequence', model_id: 'fampnn', status: 'completed', native_identity: { producer: 'exact' }, predictions: [{ job_id: 'late-prediction', design_id: null, name: null, model_id: 'protenix', status: 'queued', target_state: 'open', binder_chains: ['A'], target_chains: ['B'], design_url: null, pae_url: null, chain_metrics_url: null, analyses: [], ipsae: [] }] }] : [] });
const text = (node: any): string => typeof node === 'string' ? node : (node.children ?? []).map(text).join('');
const button = (name: string) => tree.root.findAllByType('button').find(n => text(n) === name)!;
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await vi.advanceTimersByTimeAsync(1); }); };
const poll = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); await flush(); };
const reply = (config: any, id?: string) => ({ config, status: 200, statusText: 'OK', headers: {}, data: { schema_version: 1, job_id: config.url.split('/')[4], offset: config.params.offset, limit: 100, total: 4000, records: id ? [row(id)] : Array.from({ length: 100 }, (_, i) => row(`source-${config.params.offset + i}`)) } });
async function mount(sourceDesignId?: string, jobId = 'root') {
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    await act(async () => { tree = create(<QueryClientProvider client={client}><BinderPredictionEvidence jobId={jobId} sourceDesignId={sourceDesignId} /></QueryClientProvider>); });
    await flush();
}
const evidenceCalls = () => calls.filter(c => c.url.endsWith('/binder-evidence'));
const cache = () => client.getQueryCache().getAll().filter(q => q.queryKey[0] === 'binder-evidence');
beforeEach(() => {
    vi.useFakeTimers(); calls = []; fail = false; late = false;
    api.defaults.adapter = async config => {
        calls.push(config);
        if (config.url?.endsWith('/round')) return { config, status: 200, statusText: 'OK', headers: {}, data: { job_id: 'root', state: 'completed', steps: {}, errors: {} } };
        if (fail) throw Error('offline');
        return reply(config, config.params.source_design_id);
    };
});
afterEach(async () => { await act(async () => tree?.unmount()); client?.clear(); api.defaults.adapter = original; vi.useRealTimers(); });
it('directly reads a distant source among thousands and discovers late children on a completed root', async () => {
    await mount('source-3999');
    expect(evidenceCalls().map(c => c.params)).toEqual([{ offset: 0, limit: 100, source_design_id: 'source-3999' }]);
    expect(text(tree.root)).toContain('Unmeasured');
    late = true; await poll();
    expect(evidenceCalls()).toHaveLength(2);
    expect(text(tree.root)).toContain('late sequence');
    await act(async () => tree.root.findByProps({ 'aria-label': 'Evidence sequence' }).props.onChange({ target: { value: JSON.stringify(['late-designer', 'seq']) } }));
    await flush();
    expect(text(tree.root)).toContain('late-prediction');
    await poll();
    expect(tree.root.findByProps({ 'aria-label': 'Evidence sequence' }).props.value).toBe(JSON.stringify(['late-designer', 'seq']));
    expect(cache()).toHaveLength(1);
});
it('replaces pages, clears inspection deliberately, polls only the current page and drops departed response caches', async () => {
    await mount();
    await act(async () => tree.root.findByProps({ 'aria-label': 'Evidence candidate' }).props.onChange({ target: { value: 'source-0' } }));
    expect(text(tree.root)).toContain('Source Design: source-0');
    for (const offset of [100, 200, 300]) {
        await act(async () => button('Next evidence candidates').props.onClick()); await flush();
        expect(tree.root.findByProps({ 'aria-label': 'Evidence candidate' }).props.value).toBe('');
        expect(tree.root.findAllByType('option')).toHaveLength(101);
        const before = evidenceCalls().length; await poll();
        expect(evidenceCalls().slice(before).map(c => c.params.offset)).toEqual([offset]);
        expect(cache()).toHaveLength(1);
    }
    await act(async () => button('Previous evidence candidates').props.onClick()); await flush();
    expect(evidenceCalls().at(-1).params.offset).toBe(200);
    expect(evidenceCalls().map(c => c.params.offset)).toEqual([0, 100, 100, 200, 200, 300, 300, 200]);
});
it('keeps truthful retained evidence after a failed refresh and recovers with Retry', async () => {
    await mount('source-3999'); fail = true; await poll();
    expect(text(tree.root)).toContain('Evidence readback unavailable');
    expect(text(tree.root)).toContain('Source Design: source-3999');
    const exported = tree.root.findAllByType('a').find(n => text(n) === 'Export candidate evidence JSON')!;
    expect(JSON.parse(decodeURIComponent(exported.props.href.split(',')[1]))).toEqual(row('source-3999'));
    fail = false; late = true;
    await act(async () => button('Retry evidence readback').props.onClick()); await flush();
    expect(text(tree.root)).toContain('late sequence');
    expect(text(tree.root)).not.toContain('Evidence readback unavailable');
});
it('rejects a mismatched direct response rather than showing another candidate', async () => {
    const adapter = api.defaults.adapter;
    api.defaults.adapter = async config => config.url?.endsWith('/binder-evidence') ? reply(config, 'foreign') : (adapter as any)(config);
    await mount('source-3999');
    expect(text(tree.root)).toContain('no other candidate has been substituted');
    expect(tree.root.findAllByType('a').filter(n => text(n) === 'Export candidate evidence JSON')).toHaveLength(0);
});
it.each(['root', 'other-root'])('aborts and ignores a pending old source response when selected source changes in %s', async nextJob => {
    let resolve!: (value: any) => void;
    let oldConfig: any;
    const adapter = api.defaults.adapter;
    api.defaults.adapter = async config => {
        if (config.params?.source_design_id === 'old') { calls.push(config); oldConfig = config; return await new Promise(r => { resolve = r; }); }
        return (adapter as any)(config);
    };
    await mount('old');
    await act(async () => tree.update(<QueryClientProvider client={client}><BinderPredictionEvidence jobId={nextJob} sourceDesignId="new" /></QueryClientProvider>)); await flush();
    expect(oldConfig.signal.aborted).toBe(true);
    await act(async () => resolve(reply(oldConfig, 'old'))); await flush();
    expect(text(tree.root)).toContain('Source Design: new');
    expect(text(tree.root)).not.toContain('Source Design: old');
    expect(evidenceCalls().map(c => [c.url, c.params.source_design_id])).toEqual([['/api/designs/by-job/root/binder-evidence', 'old'], [`/api/designs/by-job/${nextJob}/binder-evidence`, 'new']]);
    expect(cache()).toHaveLength(1);
});
