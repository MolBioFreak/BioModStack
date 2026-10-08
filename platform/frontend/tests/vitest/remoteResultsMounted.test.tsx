import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { RemoteResultsPrompt } from '../../src/components/RemoteResultsPrompt';
import { api, fetchJobs, submitJob, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { MemoryRouter } from 'react-router-dom';
import { JobQueuePanel } from '../../src/components/JobQueuePanel';
import { JobQueueTable } from '../../src/components/dashboard/JobQueueTable';
import { readFileSync } from 'node:fs';
import type { RemoteResultsJob } from '../../src/components/remoteResultsState';

const ready: RemoteResultsJob = { id: 'job', status: 'awaiting_input', queue_status: 'completed', execution_target_id: 'vast:1', awaiting_input: true, awaiting_stage: 'remote_results', remote_state: 'results_available' };
const returning = { ...ready, status: 'running', queue_status: 'running', remote_state: 'returning' };
const clients: QueryClient[] = [];
const cleanups: (() => void)[] = [];
function mount(job: RemoteResultsJob, copies = 1) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    const container = document.createElement('div');
    document.body.append(container);
    const root = createRoot(container);
    const render = (value: RemoteResultsJob) => act(() => root.render(<QueryClientProvider client={client}>{Array.from({ length: copies }, (_, i) => <RemoteResultsPrompt key={i} job={value} />)}</QueryClientProvider>));
    render(job);
    const unmount = () => { act(() => root.unmount()); container.remove(); };
    cleanups.push(unmount);
    return { container, render, client };
}
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); };
afterEach(() => { cleanups.splice(0).forEach(fn => fn()); clients.splice(0).forEach(c => c.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY); window.history.replaceState({}, '', '/'); });

it('fresh mounts and refreshes use backend states without downloading; returning stays disabled on reload', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: returning });
    const view = mount(JSON.parse(JSON.stringify(ready)));
    expect(view.container.textContent).toContain('Pull results');
    view.render(JSON.parse(JSON.stringify(returning)));
    expect(view.container.querySelector('button')?.disabled).toBe(true);
    const reloaded = mount(JSON.parse(JSON.stringify(returning)));
    expect(reloaded.container.textContent).toContain('Pulling results');
    expect(reloaded.container.querySelector('button')?.disabled).toBe(true);
    await settle();
    expect(post).not.toHaveBeenCalled();
});

it('does not promise worker retention without provider verification, while preserving explicit pull and local import', () => {
    const post = vi.spyOn(api, 'post');
    const view = mount(ready);
    expect(view.container.textContent).toContain('Results reported ready on worker');
    expect(view.container.textContent).toContain('worker availability is checked when you pull');
    expect(view.container.textContent).not.toContain('results remain on worker');
    expect(view.container.querySelector('button')?.textContent).toBe('Pull results');
    view.render({ ...ready, remote_state: 'result_pull_failed' });
    expect(view.container.querySelector('[role="alert"]')?.textContent).toContain('Worker copy has not been re-verified');
    view.render({ ...ready, remote_state: 'result_pull_failed', provenance: { remote_execution_receipt: { received_manifest_sha256: 'digest', result_manifest_sha256: 'digest' } } });
    expect(view.container.textContent).toContain('Verified bytes are retained locally');
    expect(view.container.querySelector('button')?.textContent).toBe('Retry import');
    expect(post).not.toHaveBeenCalled();
});

it('duplicate surfaces send one explicit POST; retry is explicit and persisted failure survives reload', async () => {
    const post = vi.spyOn(api, 'post').mockRejectedValue(new Error('Connection lost'));
    const view = mount(ready, 2);
    act(() => view.container.querySelectorAll('button').forEach(button => button.click()));
    await settle();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith('/api/jobs/job/remote-results/pull');
    expect(view.container.textContent).toContain('Retry pull');
    const failed = { ...ready, remote_state: 'result_pull_failed', error_message: 'Result pull interrupted; choose Retry pull' };
    view.render(failed);
    const reloaded = mount(failed);
    await settle();
    expect(post).toHaveBeenCalledTimes(1);
    expect(reloaded.container.querySelector('[role="alert"]')?.textContent).toContain('interrupted');
    post.mockResolvedValue({ data: returning });
    act(() => reloaded.container.querySelector('button')!.click());
    await settle();
    expect(post).toHaveBeenCalledTimes(2);
    reloaded.render(returning);
    expect(reloaded.container.querySelector('button')?.disabled).toBe(true);
    const invalidate = vi.spyOn(reloaded.client, 'invalidateQueries');
    reloaded.render({ ...ready, status: 'completed', awaiting_input: false, awaiting_stage: null, remote_state: 'completed' });
    await settle();
    expect(reloaded.container.textContent).toBe('');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['structure-files', 'job'] });
    expect(post).toHaveBeenCalledTimes(2);
});

it('queue renders completed queue-state waits and running transfers through refresh without auto pull', async () => {
    const post = vi.spyOn(api, 'post');
    let rows = [{ ...ready, name: 'Remote prediction', model_id: 'protenix', mode: 'structure_prediction', paused: false }];
    vi.spyOn(api, 'get').mockImplementation(async (url) => ({ data: url === '/api/queue' ? rows : {} }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    const container = document.createElement('div');
    document.body.append(container);
    const root = createRoot(container);
    cleanups.push(() => { act(() => root.unmount()); container.remove(); });
    act(() => root.render(<QueryClientProvider client={client}><MemoryRouter><JobQueuePanel /></MemoryRouter></QueryClientProvider>));
    await settle();
    expect(container.textContent).toContain('Pull results');
    expect(container.querySelectorAll('[aria-label="Remote job phases"]')).toHaveLength(1);
    expect(container.querySelector('[aria-label="Remote job phases"] h4')?.textContent).toBe('Remote jobs');
    act(() => client.setQueryData(['queue'], { data: [{ ...returning, name: 'Remote prediction', model_id: 'protenix', mode: 'structure_prediction', paused: false }] }));
    await settle();
    expect(container.querySelector('[data-remote-results-job] button')?.getAttribute('disabled')).not.toBeNull();
    expect(container.textContent).toContain('Pulling results');
    rows = [{ ...returning, name: 'Remote prediction', model_id: 'protenix', mode: 'structure_prediction', paused: false }];
    act(() => root.render(null));
    act(() => root.render(<QueryClientProvider client={client}><MemoryRouter><JobQueuePanel /></MemoryRouter></QueryClientProvider>));
    await settle();
    expect(container.textContent).toContain('Pulling results');
    expect(container.querySelector('[data-remote-results-job] button')?.getAttribute('disabled')).not.toBeNull();
    rows = [];
    act(() => client.setQueryData(['queue'], { data: rows }));
    await settle();
    expect(container.querySelector('[data-remote-results-job]')).toBeNull();
    expect(post).not.toHaveBeenCalled();
});

it.each([
    { received: true, status: 'awaiting_input', remote_state: 'result_pull_failed', label: 'Retry import' },
    { received: false, status: 'awaiting_input', remote_state: 'result_pull_failed', label: 'Retry pull' },
    { received: true, status: 'running', remote_state: 'returning', label: 'Importing received results…' },
    { received: false, status: 'running', remote_state: 'returning', label: 'Pulling results…' },
])('Recent Jobs summary and GPU queue agree: $label', async ({ received, status, remote_state, label }) => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    const post = vi.spyOn(api, 'post');
    const summary = { ...ready, status, remote_state, name: 'DRT4 Fold-CP', model_id: 'foldcp', mode: 'structure_prediction',
        created_at: '2026-01-01T00:00:00Z', design_count: 0, remote_results_received: received,
        error_message: remote_state === 'result_pull_failed' ? 'Result pull failed: workflow completed but result ingestion produced no designs' : null };
    // The bounded list intentionally has no provenance. Queue/detail still carry it.
    const { remote_results_received: _projection, ...fields } = summary;
    const queued = { ...fields, paused: false, provenance: { remote_execution_receipt: {
        result_manifest_sha256: 'current', received_manifest_sha256: received ? 'current' : 'previous',
    } } };
    const get = vi.spyOn(api, 'get').mockImplementation(async (url) => ({ data: url === '/api/queue' ? [queued] : url === '/api/jobs' ? { jobs: [summary], total: 1 } : {} }));
    const response = await fetchJobs({ summary: true });
    expect(response.data.jobs[0].provenance).toBeNull();
    expect(response.data.jobs[0].remote_results_received).toBe(received);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    const container = document.createElement('div'); document.body.append(container);
    const root = createRoot(container);
    cleanups.push(() => { act(() => root.unmount()); container.remove(); });
    const noop = () => {};
    act(() => root.render(<QueryClientProvider client={client}><MemoryRouter>
        <section data-surface="queue"><JobQueuePanel /></section>
        <section data-surface="recent"><JobQueueTable jobs={response.data.jobs} loading={false}
            onCancel={noop} onResubmit={noop} onResume={noop} onViewLogs={noop} onViewQuick={noop} quickViewJobId={null} /></section>
    </MemoryRouter></QueryClientProvider>));
    await settle();
    const prompts = ['queue', 'recent'].map(surface => container.querySelector(`[data-surface="${surface}"] [data-remote-results-job="job"]`)!);
    for (const prompt of prompts) {
        expect(prompt).not.toBeNull();
        expect(prompt.querySelector('button')?.textContent).toBe(label);
        expect(prompt.querySelector('button')?.disabled).toBe(status === 'running');
        expect(prompt.textContent).toContain(received ? 'Results received; native import pending' : 'Results reported ready on worker');
    }
    expect(prompts[0].textContent).toBe(prompts[1].textContent);
    expect(post).not.toHaveBeenCalled();
    // Existing queue telemetry is retained; no per-job detail read is added.
    expect(get.mock.calls.map(([url]) => url)).toEqual(['/api/jobs', '/api/queue', '/api/gpu/status']);
});

it.each([false, true])('terminal import failure stays out of queue; Recent Jobs explicitly retries once (mobile=$0)', async (mobile) => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: mobile, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    const failure = { ...ready, status: 'failed', queue_status: 'failed', awaiting_input: false, awaiting_stage: null,
        remote_state: 'result_import_failed', remote_results_received: true, paused: false,
        name: 'DRT4 Fold-CP', model_id: 'foldcp', mode: 'structure_prediction',
        created_at: '2026-01-01T00:00:00Z', design_count: 0,
        error_message: 'Workflow completed but result ingestion produced no designs' };
    const post = vi.spyOn(api, 'post').mockRejectedValue(new Error('Import still produced no designs'));
    // The persisted terminal disposition is excluded by the existing queue API.
    const get = vi.spyOn(api, 'get').mockImplementation(async (url) => ({ data: url === '/api/queue' ? []
        : url === '/api/jobs' ? { jobs: [failure], total: 1 } : {} }));
    const response = await fetchJobs({ summary: true });
    expect(response.data.jobs[0].provenance).toBeNull();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    const container = document.createElement('div'); document.body.append(container);
    const root = createRoot(container);
    cleanups.push(() => { act(() => root.unmount()); container.remove(); });
    const onResume = vi.fn(); const onResubmit = vi.fn(); const noop = () => {};
    const render = () => act(() => root.render(<QueryClientProvider client={client}><MemoryRouter>
        <section data-surface="queue"><JobQueuePanel /></section>
        <section data-surface="recent"><JobQueueTable jobs={response.data.jobs} loading={false}
            onCancel={noop} onResubmit={onResubmit} onResume={onResume} onViewLogs={noop} onViewQuick={noop} quickViewJobId={null} /></section>
    </MemoryRouter></QueryClientProvider>));
    render(); await settle(); render(); await settle();
    const queue = container.querySelector('[data-surface="queue"]')!;
    const recent = container.querySelector('[data-surface="recent"]')!;
    expect(queue.querySelector('[data-remote-results-job]')).toBeNull();
    expect(queue.textContent).not.toContain('DRT4 Fold-CP');
    expect(queue.querySelector('[aria-label="Remote job phases"]')).toBeNull();
    expect(recent.textContent).toContain('Result import failed');
    expect(recent.textContent).not.toContain('native import pending');
    expect(recent.textContent).toContain('without rerunning science or contacting the worker');
    expect(recent.querySelector('[role="alert"]')?.textContent).toContain('no designs');
    const retry = recent.querySelector<HTMLButtonElement>('[data-remote-results-job] button')!;
    expect(retry.textContent).toBe('Retry import'); expect(retry.disabled).toBe(false);
    expect(post).not.toHaveBeenCalled();
    expect(get.mock.calls.map(([url]) => url)).toEqual(['/api/jobs', '/api/queue', '/api/gpu/status']);
    act(() => { retry.click(); retry.click(); });
    await settle(); await settle();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith('/api/jobs/job/remote-results/pull');
    expect(onResume).not.toHaveBeenCalled(); expect(onResubmit).not.toHaveBeenCalled();
    expect(recent.querySelector('[role="alert"]')?.textContent).toBe('Import still produced no designs');
    expect(recent.querySelector('[data-remote-results-job] button')?.textContent).toBe('Retry import');
    expect(queue.querySelector('[data-remote-results-job]')).toBeNull();
    expect(get.mock.calls.map(([url]) => url)).toEqual(['/api/jobs', '/api/queue', '/api/gpu/status', '/api/queue']);
});

it('ESMFold2 launcher copy distinguishes cached files from placement; submission keeps scientific settings', async () => {
    const source = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');
    expect(source).not.toContain('Local-only');
    expect(source).toContain('Worker-local model files');
    window.history.replaceState({}, '', '/submit');
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'vast:1');
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
    const params = { model_variant: 'full', local_files_only: true };
    await submitJob({ name: 'ESM remote', model_id: 'esmfold2', mode: 'structure_prediction', params, execution_plan_approval: 'a'.repeat(64) });
    expect(post).toHaveBeenCalledWith('/api/jobs', expect.objectContaining({ execution_target_id: 'vast:1', params }), undefined);
});

it('received terminal failure reopens from a full receipt without automatic retry', async () => {
    const post = vi.spyOn(api, 'post');
    const failed = { ...ready, status: 'failed', queue_status: 'failed', awaiting_input: false, awaiting_stage: null,
        remote_state: 'result_import_failed', provenance: { remote_execution_receipt: {
            received_manifest_sha256: 'current', result_manifest_sha256: 'current',
        } } };
    const view = mount(JSON.parse(JSON.stringify(failed)));
    await settle();
    expect(view.container.textContent).toContain('Result import failed');
    expect(view.container.querySelector('button')?.textContent).toBe('Retry import');
    for (const change of [{ status: 'cancelled' }, { remote_state: 'failed' }, { remote_state: 'result_pull_failed' },
        { remote_results_received: false }, { provenance: null }]) {
        view.render({ ...failed, ...change });
        expect(view.container.querySelector('button')).toBeNull();
    }
    expect(post).not.toHaveBeenCalled();
});

it('unrelated gates and terminal jobs never expose pull controls', () => {
    const post = vi.spyOn(api, 'post');
    for (const change of [{ execution_target_id: null }, { awaiting_stage: 'post_rfantibody' }, { status: 'cancelled' }, { status: 'failed' }, { status: 'completed' }]) {
        expect(mount({ ...ready, ...change }).container.querySelector('button')).toBeNull();
    }
    expect(post).not.toHaveBeenCalled();
});
