import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createHash, webcrypto } from 'node:crypto';
import { setTimeout as realTimeout } from 'node:timers';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BioXpSavedWorkflowRun } from '../../src/components/BioXpSavedWorkflowRun';
import { api } from '../../src/lib/api';
import { pendingWorkflowRunStorageKey, readPendingWorkflowRuns, retainPendingWorkflowRun } from '../../src/lib/bioxpSavedWorkflowRun';
import type { SavedWorkflowSnapshot } from '../../src/lib/bioxpWorkflowPlan';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let root: Root, host: HTMLDivElement, client: QueryClient;
let saved: SavedWorkflowSnapshot;
let generation: number;
let connected: boolean;
let timeout: boolean;
const nativeDocument = { protocol_id: 'fixture', version: 1, metadata: { original: true }, stages: [{ id: 's', actions: [{ kind: 'delay', params: { seconds: '01.20' } }] }] };
async function tick(ms = 20) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
async function render() {
    await act(async () => root.render(<QueryClientProvider client={client}><BioXpSavedWorkflowRun saved={saved} generation={generation} connected={connected} controlsEnabled /></QueryClientProvider>));
    await tick();
}
async function mount() { client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } }); root = createRoot(host); await render(); }
async function clickRun() {
    await act(async () => host.querySelector<HTMLInputElement>('input[type=checkbox]')!.click());
    await act(async () => host.querySelector<HTMLButtonElement>('button')!.click());
    for (let i = 0; i < 30 && !vi.mocked(api.post).mock.calls.some(([url]) => url === '/api/bioxp/protocols/submit'); i++) {
        await act(async () => { await new Promise(resolve => realTimeout(resolve, 5)); });
    }
    await tick();
}
const submits = () => vi.mocked(api.post).mock.calls.filter(([url]) => url === '/api/bioxp/protocols/submit');
beforeEach(() => {
    vi.useFakeTimers(); vi.stubGlobal('crypto', webcrypto); localStorage.clear();
    vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset(); generation = 9; connected = true; timeout = false;
    saved = { id: 'template-1', name: 'Original', draft: { schema: 'bms.bioxp-workflow-draft.v1', steps: [], editor_state: { raw: '01.20', omitted: null, flag: false } } };
    host = document.createElement('div'); document.body.append(host);
    vi.mocked(api.get).mockImplementation(async url => ({ data: { job_id: String(url).split('/').at(-1), status: 'queued' } }) as never);
    vi.mocked(api.post).mockImplementation(async (url, body: any) => {
        if (url === '/api/bioxp/workflows/preview') return { data: { document: nativeDocument, actions: [], issues: [] } } as never;
        if (url === '/api/bioxp/protocols/submit') {
            const stored = JSON.parse(localStorage.getItem(pendingWorkflowRunStorageKey) || '[]');
            if (stored.length) expect(stored.at(-1).document).toEqual(body.document);
            if (timeout) throw new Error('response timeout');
            return { data: { job_id: `protocol-live-${createHash('sha256').update(body.idempotency_key).digest('hex')}`, status: 'queued' } } as never;
        }
        throw new Error(`Unexpected mutation ${url}`);
    });
});
afterEach(async () => { await act(async () => root?.unmount()); client?.clear(); host.remove(); vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals(); });
describe('explicit saved snapshot run and original-job recovery', () => {
    it.each(['control', 'review'])('keeps saved Run serialized while a selected-away %s settles', async kind => {
        for (const key of ['one', 'two']) retainPendingWorkflowRun({ version: 1, key,
            jobId: `protocol-live-${createHash('sha256').update(key).digest('hex')}`, generation,
            saved, document: nativeDocument });
        vi.mocked(api.get).mockImplementation(async url => {
            const id = String(url).split('/').at(-1)!;
            return { data: { job_id: id, command: { command_id: id, ownership_generation: 42, terminal: false, status: 'dispatched' },
                operator: { pending_review: { stage_id: 's', action_id: 'a' } },
                execution: { dry_run: false, runtime_state: { workflow: { command_id: id, phase: kind === 'review' ? 'waiting' : 'executing', gate: kind === 'review' ? 'review' : null } } } } } as never;
        });
        await mount();
        const button = (name: string) => [...host.querySelectorAll('button')].find(node => node.textContent === name)!;
        await act(async () => host.querySelector<HTMLInputElement>('input[type=checkbox]')!.click());
        if (kind === 'review') await act(async () => {
            const input = [...host.querySelectorAll('label')].find(node => node.textContent?.startsWith('Reviewer'))!.querySelector('input')!;
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Alice');
            input.dispatchEvent(new Event('input', { bubbles: true }));
        });
        let finish!: (value: any) => void;
        vi.mocked(api.post).mockImplementation(() => new Promise(resolve => { finish = resolve; }) as never);
        await act(async () => {
            button(kind === 'review' ? 'Acknowledge protocol review' : 'Pause workflow').click();
            button('Run saved workflow').click();
        }); await tick();
        expect(api.post).toHaveBeenCalledTimes(1);
        await act(async () => {
            const select = host.querySelector('select')!; select.value = select.options[0].value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }); await tick();
        expect(button('Run saved workflow').disabled).toBe(true);
        expect(button('Request safe-state stop').disabled).toBe(true);
        expect(submits()).toHaveLength(0);
        await act(async () => finish({ data: { accepted: true, command_id: 'old-job' } })); await tick();
        expect(button('Run saved workflow').disabled).toBe(false);
        expect(button('Request safe-state stop').disabled).toBe(false);
    });

    it('does nothing on mount, submits exact saved snapshot once and isolates later template edits', async () => {
        await mount(); expect(api.post).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
        const original = JSON.parse(JSON.stringify(saved)); await clickRun();
        expect(submits()).toHaveLength(1);
        const body: any = submits()[0][1];
        expect(body).toMatchObject({ source_type: 'native', dry_run: false, live_execution_ack: true, expected_connection_generation: 9 });
        expect(body.document).toEqual({ ...nativeDocument, metadata: { original: true, bms_saved_workflow: original } });
        expect(vi.mocked(api.post).mock.calls[0]).toEqual(['/api/bioxp/workflows/preview', { draft: original.draft, protocol_id: expect.any(String) }]);
        saved.name = 'Edited'; saved.draft.editor_state.raw = '999'; await render();
        expect(body.document.metadata.bms_saved_workflow).toEqual(original);
        expect(readPendingWorkflowRuns().runs[0].saved).toEqual(original);
        expect(host.textContent).toContain('Submitted saved snapshot: Original');
    });
    it('retains canonical key/job before timeout and reload only GETs that job across generations', async () => {
        timeout = true; await mount(); await clickRun();
        const body: any = submits()[0][1];
        const id = `protocol-live-${createHash('sha256').update(body.idempotency_key).digest('hex')}`;
        expect(readPendingWorkflowRuns().runs[0]).toMatchObject({ jobId: id, key: body.idempotency_key, generation: 9 });
        expect(host.textContent).toContain('response timeout'); await tick(6100); expect(submits()).toHaveLength(1);
        await act(async () => root.unmount()); client.clear(); generation = 10; await mount(); await tick(4100);
        expect(submits()).toHaveLength(1);
        expect(vi.mocked(api.get).mock.calls.every(([url]) => url === `/api/bioxp/protocols/jobs/${id}`)).toBe(true);
        expect(api.get).toHaveBeenLastCalledWith(`/api/bioxp/protocols/jobs/${id}`, { params: { expected_connection_generation: 10 } });
        expect(host.textContent).toContain('Original connection generation: 9');
        connected = false; await render(); const count = vi.mocked(api.get).mock.calls.length; await tick(4100);
        expect(vi.mocked(api.get).mock.calls).toHaveLength(count); expect(submits()).toHaveLength(1);
    });
    it('captures before asynchronous composition and never follows a later selected template', async () => {
        let finish!: (value: unknown) => void;
        const transport = vi.mocked(api.post).getMockImplementation()!;
        vi.mocked(api.post).mockImplementation((url, body: any) => url === '/api/bioxp/workflows/preview'
            ? new Promise(resolve => { finish = resolve; }) as never : transport(url, body));
        await mount();
        const original = JSON.parse(JSON.stringify(saved));
        await act(async () => host.querySelector<HTMLInputElement>('input[type=checkbox]')!.click());
        await act(async () => { host.querySelector<HTMLButtonElement>('button')!.click(); host.querySelector<HTMLButtonElement>('button')!.click(); });
        for (let i = 0; i < 30 && !finish; i++) await act(async () => { await new Promise(resolve => realTimeout(resolve, 5)); });
        saved = { ...saved, id: 'other', name: 'Other', draft: { ...saved.draft, editor_state: { raw: 'different' } } }; await render();
        await act(async () => finish({ data: { document: nativeDocument, actions: [], issues: [] } })); await tick();
        expect(submits()).toHaveLength(1);
        expect((submits()[0][1] as any).document.metadata.bms_saved_workflow).toEqual(original);
    });
    it('fences a connection generation change during composition without posting or erasing earlier identity', async () => {
        await mount(); await clickRun(); const original = readPendingWorkflowRuns().runs[0];
        let finish!: (value: unknown) => void;
        vi.mocked(api.post).mockImplementation(() => new Promise(resolve => { finish = resolve; }) as never);
        await act(async () => host.querySelector<HTMLButtonElement>('button')!.click());
        for (let i = 0; i < 30 && !finish; i++) await act(async () => { await new Promise(resolve => realTimeout(resolve, 5)); });
        generation = 10; await render();
        await act(async () => finish({ data: { document: nativeDocument, actions: [], issues: [] } })); await tick();
        expect(submits()).toHaveLength(1); expect(readPendingWorkflowRuns().runs[0]).toEqual(original);
        expect(host.textContent).toContain('Connection changed during composition');
        expect(host.textContent).toContain(original.jobId);
    });
    it('reports storage failure but does not turn retention into a submission gate', async () => {
        vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
        await mount(); await clickRun(); expect(submits()).toHaveLength(1);
        expect(host.textContent).toContain('Browser run retention failed'); expect(host.textContent).toContain('Canonical job: protocol-live-');
    });
    it('bounds retention without discarding the latest association', async () => {
        await mount(); await clickRun(); const run = readPendingWorkflowRuns().runs[0];
        for (let n = 0; n < 12; n++) retainPendingWorkflowRun({ ...run, key: `k-${n}`, jobId: `protocol-live-${createHash('sha256').update(`k-${n}`).digest('hex')}` });
        const retained = readPendingWorkflowRuns(); expect(retained.runs).toHaveLength(8); expect(retained.runs.at(-1)?.key).toBe('k-11');
    });
    it('does not submit incomplete native preview or execute anything disconnected', async () => {
        connected = false; await mount(); expect(host.querySelector<HTMLButtonElement>('button')!.disabled).toBe(true);
        expect(api.post).not.toHaveBeenCalled(); connected = true; await render();
        vi.mocked(api.post).mockResolvedValue({ data: { document: null, actions: [], issues: [{ step_id: 's1', message: 'Explicit volume required' }] } } as never);
        await clickRun(); expect(submits()).toHaveLength(0); expect(host.textContent).toContain('s1: Explicit volume required');
    });
});
