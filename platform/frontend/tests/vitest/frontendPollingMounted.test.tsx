import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useSystemStatus } from '../../src/lib/useSystemStatus';
import QuickViewer from '../../src/components/QuickViewer';

const mocks = vi.hoisted(() => ({ system: vi.fn(), jobs: vi.fn(), fullJob: vi.fn(), loaded: vi.fn(), viewer: vi.fn() }));
vi.mock('../../src/lib/api', () => ({ fetchSystemStatus: mocks.system, fetchJobs: mocks.jobs, fetchFullJob: mocks.fullJob }));
vi.mock('../../src/structureViewer/StructureWorkbench', () => {
    mocks.loaded();
    return { StructureWorkbench: (props: unknown) => { mocks.viewer(props); return <div data-same-workbench />; } };
});
let root: ReactTestRenderer | undefined;
let client: QueryClient;
function Probe({ interval = 5000 }: { interval?: number }) {
    const query = useSystemStatus(interval);
    return <span>{query.isError ? 'error' : query.data?.data.timestamp ?? 'loading'}</span>;
}
async function render(children: React.ReactNode) {
    await act(async () => {
        const tree = <QueryClientProvider client={client}>{children}</QueryClientProvider>;
        if (root) root.update(tree); else root = create(tree);
    });
    await tick(1);
}
async function tick(ms: number) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
beforeEach(() => {
    vi.useFakeTimers();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    focusManager.setFocused(true);
    mocks.system.mockReset().mockResolvedValue({ data: { timestamp: 'first', gpus: [] } });
    mocks.jobs.mockResolvedValue({ data: { jobs: [], total: 0 } });
    mocks.fullJob.mockResolvedValue({ id: 'job-a', model_id: 'boltz2', mode: 'structure_prediction' });
});
afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    root = undefined;
    client.clear();
    focusManager.setFocused(undefined);
    vi.useRealTimers();
    vi.unstubAllGlobals();
});
it('one interval owner across simultaneous and staggered observers, cadence changes and remounts', async () => {
    await render(<><Probe key="chart" interval={1000}/><Probe key="gpu"/><Probe key="queue"/></>);
    expect(mocks.system).toHaveBeenCalledTimes(1);
    await tick(3100);
    expect(mocks.system).toHaveBeenCalledTimes(4);
    await render(<><Probe key="chart" interval={2000}/><Probe key="gpu"/><Probe key="queue"/><Probe key="late"/></>);
    expect(mocks.system).toHaveBeenCalledTimes(4);
    await tick(4100);
    expect(mocks.system).toHaveBeenCalledTimes(6);
    await render(<><Probe key="gpu"/><Probe key="queue"/></>);
    await tick(5100);
    expect(mocks.system).toHaveBeenCalledTimes(7);
    await render(null);
    await tick(10000);
    expect(mocks.system).toHaveBeenCalledTimes(7);
    await render(<Probe/>);
    expect(mocks.system).toHaveBeenCalledTimes(8);
});
it('hidden polling pauses, resumes, publishes change, preserves errors and recovers via invalidation', async () => {
    await render(<><Probe interval={1000}/><Probe/></>);
    focusManager.setFocused(false);
    await tick(5000);
    expect(mocks.system).toHaveBeenCalledTimes(1);
    mocks.system.mockResolvedValue({ data: { timestamp: 'changed', gpus: [] } });
    focusManager.setFocused(true);
    await tick(1100);
    expect(mocks.system).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(root!.toJSON())).toContain('changed');
    mocks.system.mockRejectedValue(new Error('offline'));
    await tick(1000);
    expect(JSON.stringify(root!.toJSON())).toContain('error');
    const count = mocks.system.mock.calls.length;
    await tick(1000);
    expect(mocks.system).toHaveBeenCalledTimes(count);
    mocks.system.mockResolvedValue({ data: { timestamp: 'recovered', gpus: [] } });
    await act(async () => { await client.invalidateQueries({ queryKey: ['system'] }); });
    await tick(1);
    expect(JSON.stringify(root!.toJSON())).toContain('recovered');
});
it('QuickViewer imports no workbench before structure choice, then mounts the same workbench with unchanged props', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ structures: [{ path: 'fixture.pdb', type: 'pdb', name: 'fixture', size_bytes: 20 }], count: 1 }) })));
    await render(<QuickViewer selectedJobId={null}/>);
    expect(mocks.loaded).not.toHaveBeenCalled();
    expect(mocks.viewer).not.toHaveBeenCalled();
    await render(<QuickViewer selectedJobId="job-a"/>);
    for (let i = 0; i < 10; i++) await tick(10);
    expect(mocks.loaded).toHaveBeenCalledTimes(1);
    expect(mocks.viewer).toHaveBeenCalled();
    expect(mocks.viewer.mock.lastCall![0]).toMatchObject({ mode: 'compact', structureUrl: '/api/files/pdb/fixture.pdb', format: 'pdb', jobId: 'job-a', alphafoldView: true });
    await render(<QuickViewer selectedJobId={null}/>);
    await render(<QuickViewer selectedJobId="job-a"/>);
    await tick(100);
    expect(mocks.loaded).toHaveBeenCalledTimes(1);
});
