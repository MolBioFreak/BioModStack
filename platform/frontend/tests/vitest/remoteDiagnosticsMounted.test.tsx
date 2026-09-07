import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { RemoteDiagnosticsPrompt } from '../../src/components/RemoteDiagnosticsPrompt';
import { api, type Job, type RemoteDiagnosticsRecord } from '../../src/lib/api';

const identity: RemoteDiagnosticsRecord['identity'] = { schema: 'bms.remote-result-pull.v1', attempt_id: 'attempt', execution_target_id: 'vast:1', source_revision: 'revision', source_tree: 'tree', execution_envelope_sha256: 'a'.repeat(64) };
const receipt = { ...identity, state: 'failed', exit_code: 1, result_manifest_sha256: 'd'.repeat(64) };
const failed: Job = { id: 'job/id', name: 'Failed job', model_id: 'protenix', mode: 'structure_prediction', params: {}, created_at: '2026-01-01', design_count: 0, output_dir: null, status: 'failed', remote_attempt_id: identity.attempt_id, execution_target_id: identity.execution_target_id, execution_source_revision: identity.source_revision, execution_source_tree: identity.source_tree, execution_bundle_sha256: identity.execution_envelope_sha256, provenance: { remote_execution_receipt: receipt } };
function recorded(state: RemoteDiagnosticsRecord['state'], changes = {}): Job {
    return { ...failed, provenance: { ...failed.provenance, remote_diagnostics: { state, identity, result_manifest_sha256: receipt.result_manifest_sha256, error: state === 'failed' ? 'Transfer interrupted' : null, output_dir: state === 'returned' ? '/controller/attempt/files' : null, ...changes } } };
}
const cleanups: (() => void)[] = [];
function mount(job: Job, copies = 1) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement('div'); document.body.append(container);
    const root = createRoot(container);
    const render = (job: Job) => act(() => root.render(<QueryClientProvider client={client}>{Array.from({ length: copies }, (_, i) => <RemoteDiagnosticsPrompt key={i} job={job} />)}</QueryClientProvider>));
    render(job);
    cleanups.push(() => { act(() => root.unmount()); container.remove(); client.clear(); });
    return { container, render, client };
}
const settle = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); }); };
afterEach(() => { cleanups.splice(0).forEach(fn => fn()); vi.restoreAllMocks(); vi.useRealTimers(); });

it('mount, refresh, duplicate mounts and persisted returning never POST automatically; explicit restart retry is available', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: recorded('returning') });
    const view = mount(failed, 2);
    expect(view.container.textContent).toContain('Retrieve failed-attempt files and logs; job remains failed');
    expect(view.container.textContent).toContain('including partial scientific files');
    view.render({ ...failed });
    const restarted = mount(recorded('returning'));
    expect(restarted.container.querySelector('button')?.textContent).toBe('Check/retry diagnostic pull');
    expect(restarted.container.querySelector('button')?.disabled).toBe(false);
    await settle(); expect(post).not.toHaveBeenCalled();
    act(() => restarted.container.querySelector('button')!.click());
    await settle(); expect(post).toHaveBeenCalledTimes(1);
});

it('same-tick clicks across duplicate surfaces issue one encoded POST; failures require explicit retry', async () => {
    const post = vi.spyOn(api, 'post').mockRejectedValue({ response: { data: { detail: 'Remote attempt controller is busy' } } });
    const view = mount(failed, 2);
    act(() => view.container.querySelectorAll('button').forEach(b => { b.click(); b.click(); }));
    await settle();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith('/api/jobs/job%2Fid/remote-diagnostics/pull');
    expect(view.container.textContent).toContain('Remote attempt controller is busy');
    await settle(); expect(post).toHaveBeenCalledTimes(1);
    post.mockResolvedValue({ data: recorded('returning') });
    act(() => view.container.querySelector('button')!.click());
    await settle(); expect(post).toHaveBeenCalledTimes(2);
    expect(view.container.textContent).toContain('Check/retry diagnostic pull');
});

it('polls only normal detail while returning and displays returned path as text without science success', async () => {
    vi.useFakeTimers();
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: recorded('returned') });
    const post = vi.spyOn(api, 'post');
    const view = mount(recorded('returning'));
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(get).toHaveBeenCalledWith('/api/jobs/job%2Fid');
    expect(view.container.textContent).toContain('Diagnostics returned to the controller');
    expect(view.container.textContent).toContain('job remains failed');
    expect(view.container.querySelector('code')?.textContent).toBe('/controller/attempt/files');
    expect(view.container.querySelector('a')).toBeNull();
    expect(view.container.querySelector('button')).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(get).toHaveBeenCalledTimes(1); expect(post).not.toHaveBeenCalled();
});

it('persisted failure survives remount and returned does not offer a transfer', () => {
    const post = vi.spyOn(api, 'post');
    for (let i = 0; i < 2; i++) {
        const view = mount(recorded('failed'));
        expect(view.container.querySelector('[role="alert"]')?.textContent).toBe('Transfer interrupted');
        expect(view.container.querySelector('button')?.textContent).toBe('Retry diagnostics');
    }
    expect(mount(recorded('returned')).container.querySelector('button')).toBeNull();
    expect(post).not.toHaveBeenCalled();
});

it('hides legacy failures without digest, nonterminal jobs and mismatched current receipt identity', () => {
    const post = vi.spyOn(api, 'post');
    const changes: Partial<Job>[] = [{ status: 'completed' }, { status: 'running' }, { execution_target_id: null }, { remote_attempt_id: 'new' }, { execution_source_revision: 'new' }, { execution_source_tree: 'new' }, { execution_bundle_sha256: 'b'.repeat(64) }, { provenance: null }, ...[undefined, 'bad', 'D'.repeat(64)].map(digest => ({ provenance: { remote_execution_receipt: { ...receipt, result_manifest_sha256: digest } } }))];
    for (const change of changes) expect(mount({ ...failed, ...change }).container.textContent).toBe('');
    expect(mount({ ...failed, status: 'cancelled' }).container.textContent).toContain('job remains cancelled');
    expect(post).not.toHaveBeenCalled();
});

it('does not reuse returned archive state from another attempt, source or digest', () => {
    for (const change of [{ identity: { ...identity, attempt_id: 'old' } }, { identity: { ...identity, source_tree: 'old' } }, { result_manifest_sha256: 'e'.repeat(64) }]) {
        const view = mount(recorded('returned', change));
        expect(view.container.querySelector('button')?.textContent).toBe('Pull diagnostics');
        expect(view.container.querySelector('code')).toBeNull();
    }
});

it('both job detail surfaces mount diagnostics separately next to the success result prompt', () => {
    for (const path of ['src/components/JobDetailPage.tsx', 'src/components/JobDetailsPanel.tsx']) {
        expect(readFileSync(path, 'utf8')).toMatch(/<RemoteResultsPrompt job=\{job\} \/>\s*<RemoteDiagnosticsPrompt job=\{job\} \/>/);
    }
});
