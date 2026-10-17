import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { expect, test, afterEach, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type Job } from '../../src/lib/api';
import { BinderResultFamily } from '../../src/components/BinderResultFamily';
import { familyRoot, familyReferences, fetchBinderResultFamily } from '../../src/lib/binderResultFamily';
const row = (id: string, values: Partial<Job> = {}): Job => ({ id, name: id, status: 'completed', model_id: 'bindcraft2',
    mode: 'default', params: {}, design_count: 0, output_dir: null, created_at: '2026-01-01', ...values });
const rows = [row('root'), row('accepted', { lineage_root_job_id: 'root', design_count: 1 }),
    row('rejected-zero', { lineage_root_job_id: 'root' }), row('native-zero', { lineage_root_job_id: 'root', model_id: 'ppiflow' }),
    row('round2', { lineage_root_job_id: 'root', selection_source_job_id: 'accepted', parent_job_id: 'scheduler', model_id: 'boltzgen' }),
    row('round3', { params: { iteration_source_root_job_id: 'root', iteration_source_job_id: 'round2' } })];
const original = api.defaults.adapter;
let renderer: ReactTestRenderer | undefined;
afterEach(async () => { api.defaults.adapter = original; if (renderer) await act(async () => renderer!.unmount()); renderer = undefined; });
const mount = async (jobId: string, open = vi.fn(), compare = vi.fn()) => {
    await act(async () => { renderer = create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BinderResultFamily jobId={jobId} launchContextId="destination" onOpenJob={open} onCompareJobs={compare} />
    </QueryClientProvider>); });
    await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); expect(JSON.stringify(renderer!.toJSON())).toContain('Current'); });
};

test('fresh reopen uses persisted family and keeps native and rejected zero-design jobs navigable', async () => {
    const requests: string[] = [];
    api.defaults.adapter = async config => {
        requests.push(config.params.scientific_family_job_id);
        expect(config.url).toBe('/api/jobs');
        expect(config.params.include_children).toBe(true);
        return { data: { jobs: rows, total: rows.length }, status: 200, statusText: 'OK', headers: {}, config };
    };
    const open = vi.fn(), compare = vi.fn();
    await mount('round3', open, compare);
    const content = JSON.stringify(renderer!.toJSON());
    expect(content).toContain('destination');
    expect(content).toContain('rejected-zero');
    expect(content).toContain('native-zero');
    await act(async () => renderer!.root.findAllByType('button').find(b => b.children.join('') === 'native-zero')!.props.onClick());
    expect(open).toHaveBeenCalledWith('native-zero');
    for (const name of ['accepted', 'rejected-zero']) await act(async () => renderer!.root.findByProps({ 'aria-label': `Compare ${name}` }).props.onChange({ target: { checked: true } }));
    await act(async () => renderer!.root.findAllByType('button').find(b => b.children.join('') === 'Compare selected jobs')!.props.onClick());
    expect(compare).toHaveBeenCalledWith(['accepted', 'rejected-zero']);
    await act(async () => renderer!.unmount()); renderer = undefined;
    await mount('round2');
    expect(requests).toEqual(['round3', 'round2']);
    expect(JSON.stringify(renderer!.toJSON())).toContain('Execution parent');
    expect(familyRoot(rows[4], rows)).toBe('root');
    expect(familyReferences(rows[4])).toEqual({ root: 'root', source: 'accepted', scheduler: 'scheduler' });
    expect(familyRoot(rows[5], rows)).toBe('root');
});

test('loads every server page and rejects truncated readback instead of claiming complete family', async () => {
    const offsets: number[] = [];
    api.defaults.adapter = async config => {
        offsets.push(config.params.offset);
        return { data: { jobs: rows.slice(config.params.offset, config.params.offset + 2), total: rows.length }, status: 200, statusText: 'OK', headers: {}, config };
    };
    expect(await fetchBinderResultFamily('root')).toEqual(rows);
    expect(offsets).toEqual([0, 2, 4]);
    api.defaults.adapter = async config => ({ data: { jobs: [], total: 1 }, status: 200, statusText: 'OK', headers: {}, config });
    await expect(fetchBinderResultFamily('root')).rejects.toThrow('Family listing changed');
});

test('the inspected BC2 candidate exposes every linked model and state with exact return navigation', async () => {
    const nativeId = 'design_l3_privatehash_candidate1';
    const direct = (id: string, copies: number) => row(id, { model_id: 'protenix', provenance: { binder_round_step: {
        source_ref: { job_id: 'root', native_record_id: nativeId },
        input_components: [{ role: 'binder', sequence: 'AAA' }, { role: 'target', sequence: 'TTT', count: copies }],
    } } });
    const linked = [rows[0], direct('mono', 1), direct('trimer', 3), row('sequence', { model_id: 'proteinmpnn', mode: 'sequence_design', selection_source_job_id: 'mono' }),
        row('failed', { status: 'failed', provenance: { binder_round_step: { retry_of_job_id: 'mono' } } }), rows[2]];
    api.defaults.adapter = async config => {
        expect(config.method).toBe('get'); expect(config.url).toBe('/api/jobs');
        return { data: { jobs: linked, total: linked.length }, status: 200, statusText: 'OK', headers: {}, config };
    };
    window.history.replaceState({}, '', '/designs/root?native_candidate=exact');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { renderer = create(<QueryClientProvider client={client}><BinderResultFamily jobId="root"
        candidate={{ native_record: { design: nativeId, sequence: 'AAA', trajectory_design: 'native-family' } }} candidateLabel="Design 61 · Variant 1" /></QueryClientProvider>); });
    await vi.waitFor(async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); expect(JSON.stringify(renderer!.toJSON())).toContain('Trimer target'); });
    const links = renderer!.root.findAllByType('a');
    expect(links.map(a => new URL(a.props.href, window.location.origin).pathname).sort()).toEqual(['/designs/mono', '/designs/sequence', '/designs/trimer', '/jobs/failed']);
    expect(links.map(a => a.children.join('')).join(' ')).toContain('Sequence design');
    const mono = new URL(links.find(a => a.props.href.startsWith('/designs/mono?'))!.props.href, window.location.origin);
    expect(mono.searchParams.get('include_children')).toBe('false');
    expect(mono.searchParams.get('view')).toBe('structure');
    expect(mono.searchParams.get('return_to')).toBe('/designs/root?native_candidate=exact');
    expect(mono.searchParams.get('return_label')).toBe('Design 61 · Variant 1');
    expect(renderer!.root.findByType('details').props.open).not.toBe(true);
    await act(async () => renderer!.unmount()); renderer = undefined; client.clear();
    window.history.replaceState({}, '', '/');
});
