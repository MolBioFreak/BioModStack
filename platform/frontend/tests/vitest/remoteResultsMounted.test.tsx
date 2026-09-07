import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { RemoteResultsPrompt } from '../../src/components/RemoteResultsPrompt';
import { api, submitJob, EXECUTION_TARGET_STORAGE_KEY } from '../../src/lib/api';
import { MemoryRouter } from 'react-router-dom';
import { JobQueuePanel } from '../../src/components/JobQueuePanel';
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
afterEach(() => { cleanups.splice(0).forEach(fn => fn()); clients.splice(0).forEach(c => c.clear()); vi.restoreAllMocks(); sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY); window.history.replaceState({}, '', '/'); });

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

it('ESMFold2 launcher copy distinguishes cached files from placement; submission keeps scientific settings', async () => {
    const source = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');
    expect(source).not.toContain('Local-only');
    expect(source).toContain('Worker-local model files');
    window.history.replaceState({}, '', '/submit');
    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, 'vast:1');
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} });
    const params = { model_variant: 'full', local_files_only: true };
    await submitJob({ name: 'ESM remote', model_id: 'esmfold2', mode: 'structure_prediction', params });
    expect(post).toHaveBeenCalledWith('/api/jobs', expect.objectContaining({ execution_target_id: 'vast:1', params }), undefined);
});

it('unrelated gates and terminal jobs never expose pull controls', () => {
    const post = vi.spyOn(api, 'post');
    for (const change of [{ execution_target_id: null }, { awaiting_stage: 'post_rfantibody' }, { status: 'cancelled' }, { status: 'failed' }, { status: 'completed' }]) {
        expect(mount({ ...ready, ...change }).container.querySelector('button')).toBeNull();
    }
    expect(post).not.toHaveBeenCalled();
});
