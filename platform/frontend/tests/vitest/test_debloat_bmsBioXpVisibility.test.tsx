import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BioXpCockpit } from '../../src/components/BioXpCockpit';
import { api } from '../../src/lib/api';
import parkReceipt from '../fixtures/bioxp_park_completed_receipt.json';

// Actual cockpit, child components, query owners and Axios serialization. Only
// transport is inert. Bytes are serialized fixture body bytes, not live traffic.
const originalAdapter = api.defaults.adapter;
let host: HTMLDivElement, root: Root, client: QueryClient;
let requests: { url: string; method: string; bytes: number; detail?: boolean }[];
let cameraActive = false;
let recoveryVisible = false;
let rows: any[] = [];
let liveJob: any = null;
const recoveryReceipt = { ...parkReceipt, status: 'ambiguous', completion_class: 'recovery_required' };
const catalog = { actions: [], ownership_generation: 7, machine_serial: '206',
    dashboard: { pipettes: { channels: [] }, snapshot: { freshness: null } },
    canonical: { actions: [], ownership_generation: 7, dashboard: { generated_at: 1, active_commands: [], latest_receipts: [] } } };
async function advance(ms = 60_000) {
    for (let elapsed = 0; elapsed < ms; elapsed += 100) await act(async () => { await vi.advanceTimersByTimeAsync(100); });
}
async function click(selector: string) { await act(async () => (host.querySelector(selector) as HTMLElement).click()); await advance(100); }
function count(fragment: string) { return requests.filter(r => r.url.includes(fragment)).length; }
beforeEach(() => {
    vi.useFakeTimers(); cameraActive = false; recoveryVisible = false; rows = []; liveJob = null; requests = [];
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
    api.defaults.adapter = async config => {
        const url = config.url ?? '';
        let data: unknown = { dependency_blockers: [], operations: [], channels: [] };
        if (url === '/api/bioxp/status') data = { connection: { active: true, configured: true, generation: 1, reachable: true }, mutation_access: { enabled: true } };
        else if (url.includes('/operator-controls/catalog')) data = { ...catalog, canonical: { ...catalog.canonical, dashboard: { ...catalog.canonical.dashboard, latest_receipts: recoveryVisible ? [recoveryReceipt] : [] } } };
        else if (url.includes('/receipts/')) data = recoveryReceipt;
        else if (url.includes('/protocols/jobs/')) data = liveJob;
        else if (url.endsWith('/protocols/jobs')) data = { rows };
        else if (url.includes('/history')) data = { items: [], next_cursor: null };
        else if (url.endsWith('/camera/stream/state')) data = { active: cameraActive, connection_generation: 1, state: cameraActive ? 'live' : 'off', stream_id: 'other-client' };
        else if (url.endsWith('/camera/status')) data = { available: true, state: 'off', connection_generation: 1, frame_sequence: null, frame_age_seconds: null, freshness_budget_seconds: 5 };
        else if (url.includes('/user-templates')) data = [];
        requests.push({ url, method: config.method ?? '', detail: config.params?.detail, bytes: new TextEncoder().encode(JSON.stringify(data)).length });
        if (config.method !== 'get') throw new Error('No mutation authorized by this test');
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = originalAdapter; vi.useRealTimers(); });
async function mount() {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>));
    await advance(500);
}
it('deletes irrelevant observations for a full Build minute after visiting operational tabs, retaining mounted drafts', async () => {
    await mount();
    await click('#control-tab-pipettes');
    const manual = host.querySelector('#control-panel-pipettes input') as HTMLInputElement;
    await click('#control-tab-workflows');
    const editor = host.querySelector('[aria-label="Saved workflow"]');
    const name = host.querySelector('[aria-label="Workflow name"]') as HTMLInputElement;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Unsubmitted retained draft'); name.dispatchEvent(new Event('input', { bubbles: true })); });
    await click('#control-tab-robot');
    await click('#control-tab-workflows');
    expect(host.querySelector('[aria-label="Saved workflow"]')).toBe(editor);
    expect((host.querySelector('[aria-label="Workflow name"]') as HTMLInputElement).value).toBe('Unsubmitted retained draft');
    expect(host.querySelector('#control-panel-pipettes input')).toBe(manual);
    requests = []; await advance();
    const irrelevant = requests.filter(r => r.url !== '/api/bioxp/status');
    expect(irrelevant).toEqual([]);
    expect(irrelevant.reduce((n, r) => n + r.bytes, 0)).toBe(0);
    expect(requests.every(r => r.method === 'get')).toBe(true);
    console.info('Build 60s', JSON.stringify({ requests: requests.length, bytes: requests.reduce((n, r) => n + r.bytes, 0), irrelevantRequests: irrelevant.length, irrelevantBytes: 0 }));
});
it('disables all idle transport for a full hidden-document minute and resumes visible demand', async () => {
    await mount();
    await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
    await advance(100); requests = []; await advance();
    expect(requests).toEqual([]);
    await act(async () => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }); document.dispatchEvent(new Event('visibilitychange')); });
    await advance(500); expect(requests.length).toBeGreaterThan(0);
});
it('uses idle camera discovery rather than 2s status polling and discovers another visible client stream', async () => {
    await mount(); requests = []; await advance();
    expect(count('/camera/status')).toBe(0);
    expect(count('/camera/stream')).toBeGreaterThanOrEqual(3);
    expect(count('/camera/stream')).toBeLessThanOrEqual(4);
    cameraActive = true; await advance(18_000);
    expect(host.querySelector('img[alt="BioXP live camera"]')).not.toBeNull();
    expect(count('/camera/status')).toBeGreaterThan(0);
    const camera = [...host.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent === 'Camera')!;
    await act(async () => { camera.open = false; camera.dispatchEvent(new Event('toggle')); });
    await advance(100); requests = []; await advance();
    expect(count('/camera/')).toBe(0);
});
it('refreshes unresolved recovery while Robot controls is visible, with zero hidden receipt bytes for a full minute', async () => {
    recoveryVisible = true; await mount(); requests = []; await advance();
    expect(requests.filter(r => r.url.includes('/receipts/') && r.detail === true).length).toBeGreaterThan(20);
    expect(requests.filter(r => r.url.includes('/receipts/') && r.detail === false).length).toBeGreaterThan(20);
    await click('#control-tab-workflows'); requests = []; await advance();
    expect(requests.filter(r => r.url.includes('/receipts/'))).toEqual([]);
    await click('#control-tab-robot'); requests = []; await advance(3_000);
    expect(count('/receipts/')).toBeGreaterThan(0);
});
it('discovers cross-client jobs only with Runs visible and retains the original active outcome owner across Build navigation', async () => {
    await mount();
    const prepared = [...host.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent === 'Prepared workflows')!;
    await act(async () => { prepared.open = true; prepared.dispatchEvent(new Event('toggle')); });
    await advance(100);
    liveJob = { job_id: 'job-original', status: 'dispatched', command: { command_id: 'job-original', idempotency_key: 'original-key', ownership_generation: 7, state_version: 1, status: 'dispatched', terminal: false },
        execution: { dry_run: false, runtime_state: { workflow: { command_id: 'job-original', phase: 'executing', child_command_ids: [] } } } };
    rows = [liveJob]; await advance(11_000);
    expect(host.textContent).toContain('job-original');
    await click('#control-tab-workflows'); requests = []; await advance();
    expect(requests.filter(r => r.url.endsWith('/protocols/jobs'))).toEqual([]);
    expect(count('/protocols/jobs/job-original')).toBeGreaterThan(0);
    expect(requests.every(r => r.method === 'get')).toBe(true);
    liveJob = { ...liveJob, status: 'completed', command: { ...liveJob.command, terminal: true, status: 'completed' } };
    await advance(3_000); requests = []; await advance();
    expect(count('/protocols/jobs')).toBe(0);
    await click('#control-tab-robot');
    expect(host.textContent).toContain('job-original');
});
