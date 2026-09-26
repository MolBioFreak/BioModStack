import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CanceledError, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Dashboard } from '../../src/components/Dashboard';
import { QuickViewer } from '../../src/components/QuickViewer';
import { api, fetchModels, fetchModelById, submitJob } from '../../src/lib/api';

// Charts/worker provisioning are unrelated to these list interactions. The real
// Dashboard, filters, table, queue, Quick Viewer and Axios helpers stay mounted.
vi.mock('../../src/components/dashboard/DashboardTelemetry', () => ({ DashboardTelemetry: () => null }));
vi.mock('../../src/components/dashboard/SystemResources', () => ({ GpuSchedulerControls: () => null }));
const oldAdapter = api.defaults.adapter;
let root: Root; let host: HTMLDivElement; let client: QueryClient;
let requests: InternalAxiosRequestConfig[]; let failJobs: boolean; let failQueue: boolean; let hang: boolean;
const row = { id: 'old-job-id', name: 'Older retained job', model_id: 'fixture', mode: 'fixture', status: 'completed', created_at: '2026-01-01T12:00:00Z', design_count: 0 };
const flush = async (ms = 5) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const button = (text: string) => [...host.querySelectorAll('button')].find(node => node.textContent?.trim() === text)!;
async function mount(child: React.ReactNode) {
    await act(async () => root.render(<MemoryRouter><QueryClientProvider client={client}>{child}</QueryClientProvider></MemoryRouter>));
    await flush();
}
async function click(text: string) { await act(async () => button(text).click()); await flush(); }
async function input(node: HTMLInputElement, value: string) {
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value); node.dispatchEvent(new Event('input', { bubbles: true })); }); await flush();
}
beforeEach(() => {
    vi.useFakeTimers(); focusManager.setFocused(true);
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    requests = []; failJobs = false; failQueue = false; hang = false;
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    api.defaults.adapter = async config => {
        requests.push(config);
        if (hang && ['/api/jobs', '/api/queue'].includes(config.url!)) await new Promise((_resolve, reject) => config.signal!.addEventListener!('abort', () => reject(new CanceledError()), { once: true }));
        if ((config.url === '/api/jobs' && failJobs) || (config.url === '/api/queue' && failQueue)) throw new Error('fixture offline');
        let data: unknown;
        if (config.url === '/api/jobs') data = { jobs: [row], total: config.params?.q ? 1 : 201 };
        else if (config.url === '/api/queue') data = [{ ...row, name: 'Queued retained job', status: 'queued', queue_status: 'queued', priority: 0, paused: false, vram_estimate_mb: 0 }];
        else if (config.url === '/api/gpu/status') data = { gpus: [] };
        else if (config.url === '/api/gpu/gpus') data = { gpus: [] };
        else if (config.url === '/api/execution-targets') data = [];
        else if (config.url?.startsWith('/api/models')) data = [];
        else throw new Error(`Unexpected offline request ${config.method} ${config.url}`);
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = oldAdapter; focusManager.setFocused(undefined); vi.useRealTimers(); vi.unstubAllGlobals(); localStorage.clear(); });

it('Recent Jobs sends full-scope name/ID/status/NGS filters and explicit page offsets, resetting page on edits', async () => {
    await mount(<Dashboard />);
    const lastJobs = () => requests.filter(r => r.url === '/api/jobs').at(-1)!;
    expect(lastJobs().params).toMatchObject({ limit: 100, offset: 0, summary: true, exclude_ngs: false });
    await click('Next jobs'); expect(lastJobs().params.offset).toBe(100);
    await input(host.querySelector<HTMLInputElement>('input[placeholder="Search jobs by name or ID..."]')!, 'old-job-id');
    expect(lastJobs().params).toMatchObject({ q: 'old-job-id', offset: 0 });
    expect(host.textContent).toContain('Older retained job');
    expect(button('Next jobs').disabled).toBe(true);
    const status = [...host.querySelectorAll('select')].find(s => [...s.options].some(o => o.value === 'awaiting_input'))!;
    await act(async () => { status.value = 'awaiting_input'; status.dispatchEvent(new Event('change', { bubbles: true })); }); await flush();
    expect(lastJobs().params.status).toBe('awaiting_input');
    const ngs = [...host.querySelectorAll('label')].find(l => l.textContent?.includes('Show NGS Jobs'))!.querySelector('input')!;
    await act(async () => ngs.click()); await flush(); expect(lastJobs().params.exclude_ngs).toBe(true);
    expect(host.textContent).not.toContain('browse the full queue');
});
it('cold Dashboard and Queue failures are unavailable, not empty, and retry recovers', async () => {
    failJobs = true; failQueue = true; await mount(<Dashboard />);
    expect(host.textContent).toContain('Jobs unavailable'); expect(host.textContent).toContain('Queue unavailable');
    expect(host.textContent).not.toContain('No jobs found'); expect(host.textContent).not.toContain('No jobs in queue');
    expect(host.textContent).not.toContain('0 total jobs');
    failJobs = false; failQueue = false; await click('Retry jobs'); await click('Retry queue');
    expect(host.textContent).toContain('Older retained job'); expect(host.textContent).toContain('Queued retained job');
    expect(host.textContent).not.toContain('Jobs unavailable');
});
it('failed refreshes retain the real rows with last-success context', async () => {
    await mount(<Dashboard />); failJobs = true; failQueue = true;
    await act(async () => { await client.invalidateQueries({ queryKey: ['jobs'] }); await client.invalidateQueries({ queryKey: ['queue'] }); }); await flush();
    expect(host.textContent).toContain('Jobs refresh failed. Showing last successful read from');
    expect(host.textContent).toContain('Queue refresh failed. Showing last successful read from');
    expect(host.textContent).toContain('Older retained job'); expect(host.textContent).toContain('Queued retained job');
});
it('Quick Viewer is idle until chooser focus, does not poll, and refreshes after close/reopen or invalidation', async () => {
    await mount(<QuickViewer selectedJobId={null} />); await flush(30000);
    expect(requests.filter(r => r.url === '/api/jobs')).toHaveLength(0);
    const selector = host.querySelector<HTMLSelectElement>('[aria-label="Quick Viewer job"]')!;
    await act(async () => selector.focus()); await flush();
    expect(requests.filter(r => r.url === '/api/jobs')).toHaveLength(1);
    expect(requests.at(-1)!.params.status).toBe('completed'); expect(selector.textContent).toContain('Older retained job');
    await flush(30000); expect(requests.filter(r => r.url === '/api/jobs')).toHaveLength(1);
    await act(async () => selector.blur()); await flush();
    await act(async () => client.invalidateQueries({ queryKey: ['jobs'] })); await flush();
    expect(requests.filter(r => r.url === '/api/jobs')).toHaveLength(1);
    failJobs = true; await act(async () => selector.focus()); await flush();
    expect(host.textContent).toContain('Completed jobs refresh failed'); expect(selector.textContent).toContain('Older retained job');
    failJobs = false; await click('Retry completed jobs'); expect(host.textContent).not.toContain('Completed jobs refresh failed');
});
it('real Query unmount aborts jobs/queue and their read transports have finite timeouts', async () => {
    hang = true; await mount(<Dashboard />);
    const pending = requests.filter(r => ['/api/jobs', '/api/queue'].includes(r.url!));
    expect(pending).toHaveLength(2); expect(pending.every(r => r.timeout === 10000 && r.signal && !r.signal.aborted)).toBe(true);
    await mount(null); expect(pending.every(r => r.signal!.aborted)).toBe(true);
});
it('changing the real search aborts the replaced jobs read while keeping the queue request', async () => {
    hang = true; await mount(<Dashboard />);
    const first = requests.find(r => r.url === '/api/jobs')!;
    const queue = requests.find(r => r.url === '/api/queue')!;
    await input(host.querySelector<HTMLInputElement>('input[placeholder="Search jobs by name or ID..."]')!, 'next-search');
    expect(first.signal!.aborted).toBe(true); expect(queue.signal!.aborted).toBe(false);
    const current = requests.filter(r => r.url === '/api/jobs').at(-1)!;
    expect(current.params.q).toBe('next-search'); expect(current.signal!.aborted).toBe(false);
});
it('model reads carry signal/timeouts without changing mutation timeout defaults', async () => {
    const controller = new AbortController();
    await fetchModels(undefined, controller.signal, true); await fetchModelById('fixture', controller.signal);
    expect(requests.every(r => r.timeout === 10000 && r.signal === controller.signal)).toBe(true);
    expect(requests[0].params.compact).toBe(true);
    api.defaults.adapter = async config => { expect(config.timeout).toBe(0); expect(config.signal).toBeUndefined(); return { data: { id: 'inert' }, status: 200, statusText: 'OK', headers: {}, config }; };
    await submitJob({ name: 'inert transport assertion', model_id: 'fixture', mode: 'fixture', params: {} });
});
