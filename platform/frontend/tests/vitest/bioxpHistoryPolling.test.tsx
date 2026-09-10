import React, { act } from 'react';
import { BioXpHistoryReceiptCard, useBioXpHistoryPagination } from '../../src/components/BioXpHistoryReceiptCard';
import { historyItem } from '../fixtures/bioxpHistory';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { useInterruptBioXpOperatorActionV1, useBioXpOperatorActionHistory, useBioXpOperatorControlCatalogV2, useInvokeBioXpOperatorAction, useAssessBioXpOperatorAction, useBioXpOperatorMethodV1, useInvokeBioXpOperatorActionV2 } from '../../src/lib/bioxpClient';

import { BioXpQuickDashboard } from '../../src/components/BioXpQuickDashboard';

vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
let invoke: ReturnType<typeof useInvokeBioXpOperatorAction>;
let assess: ReturnType<typeof useAssessBioXpOperatorAction>;
let invokeV2: ReturnType<typeof useInvokeBioXpOperatorActionV2>;
const historyKey = (generation: number, limit: number) => ['bioxp', 'operator-controls', 'history', generation, limit, null];
const history = (status: string, count = 1) => ({ schema_version: 'bioxp.operator_action_history.v2', items: Array.from({ length: count }, (_, i) => ({ command_id: `old-${i}`, status, terminal: ['completed', 'failed', 'rejected', 'cleared', 'interrupted', 'ambiguous'].includes(status) })), next_cursor: null, limit: Math.max(1, count) });
function Harness({ enabled = true }: { enabled?: boolean }) {
    useBioXpOperatorActionHistory(7, enabled, 8);
    useBioXpOperatorMethodV1('xy-one', 7, enabled);
    invoke = useInvokeBioXpOperatorAction();
    assess = useAssessBioXpOperatorAction();
    invokeV2 = useInvokeBioXpOperatorActionV2();
    return null;
}
const render = async (enabled = true) => act(async () => {
    root.render(<QueryClientProvider client={client}><Harness enabled={enabled} /></QueryClientProvider>);
});
beforeEach(() => {
    vi.resetAllMocks();
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div');
    root = createRoot(container);
    vi.mocked(api.get).mockImplementation(async (url) => ({ data: String(url).includes('/methods/') ? { method_id: 'xy-one', status: 'active' } : history('completed') }) as never);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); });

it('retains independent normal and Stop hook receipts with real mutations and held HTTP responses', async () => {
    let normal!: ReturnType<typeof useInvokeBioXpOperatorAction>;
    let stop!: ReturnType<typeof useInvokeBioXpOperatorAction>;
    function Slots() {
        normal = useInvokeBioXpOperatorAction();
        stop = useInvokeBioXpOperatorAction('stop');
        return null;
    }
    await act(async () => root.render(<QueryClientProvider client={client}><Slots /></QueryClientProvider>));
    client.setQueryData(historyKey(7, 8), history('completed', 0));
    let finishNormal!: (value: unknown) => void;
    let finishStop!: (value: unknown) => void;
    vi.mocked(api.post).mockImplementationOnce(() => new Promise(resolve => { finishNormal = resolve; }) as never)
        .mockImplementationOnce(() => new Promise(resolve => { finishStop = resolve; }) as never);
    let normalPromise!: ReturnType<typeof normal.mutateAsync>;
    let stopPromise!: ReturnType<typeof stop.mutateAsync>;
    await act(async () => { normalPromise = normal.mutateAsync({ actionId: 'gripper-open', connectionGeneration: 7, ownershipGeneration: 2, inputs: {} }); });
    await act(async () => { stopPromise = stop.mutateAsync({ actionId: 'component-stop', connectionGeneration: 7, ownershipGeneration: 2, inputs: { axis: 'g' } }); });
    expect(api.post).toHaveBeenNthCalledWith(1, '/api/bioxp/operator-controls/actions/gripper-open', expect.objectContaining({ expected_connection_generation: 7 }));
    expect(api.post).toHaveBeenNthCalledWith(2, '/api/bioxp/operator-controls/actions/component-stop', expect.objectContaining({ inputs: { axis: 'g' } }));
    await act(async () => { finishStop({ data: { action_id: 'component-stop', command_id: 'stop-one', status: 'completed' } }); await stopPromise; });
    await act(async () => { await vi.waitFor(() => expect(stop.data?.command_id).toBe('stop-one')); });
    expect(normal.data).toBeUndefined();
    expect(normal.isPending).toBe(true);
    await act(async () => { finishNormal({ data: { action_id: 'gripper-open', command_id: 'normal-one', status: 'interrupted' } }); await normalPromise; });
    await act(async () => { await vi.waitFor(() => expect(normal.data?.command_id).toBe('normal-one')); });
    expect(stop.data?.command_id).toBe('stop-one');
    expect(client.getQueryData(historyKey(7, 8))).toEqual(history('completed', 0));
    expect(client.getQueryState(historyKey(7, 8))?.isInvalidated).toBe(true);
});

it('refreshes current-generation history after v2 submission and terminal method reconciliation', async () => {
    await render(false);
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    vi.mocked(api.post).mockResolvedValue({ data: { command_id: 'v2-one', terminal: false } });
    await act(async () => {
        await invokeV2.mutateAsync({ request: {
            expected_connection_generation: 7, schema_version: 'bioxp.operator_action_request.v2',
            action_id: 'oem.y.move_absolute', idempotency_key: 'test-v2', expected_ownership_generation: 1,
            expected_board_epoch_by_board: { '4': 2 }, inputs: { target_steps: 0 },
        } });
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['bioxp', 'operator-controls', 'history', 7] });
    invalidate.mockClear();
    vi.mocked(api.get).mockImplementation(async (url) => ({ data: String(url).includes('/methods/') ? { method_id: 'xy-one', status: 'completed' } : history('completed') }) as never);
    await render();
    await act(async () => { await vi.waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['bioxp', 'operator-controls', 'history', 7] })); });
});

it('bounds and cancels catalog reads without changing the authority freshness budget', async () => {
    function CatalogHarness() { useBioXpOperatorControlCatalogV2(7); return null; }
    vi.mocked(api.get).mockImplementation(() => new Promise(() => {}));
    await act(async () => root.render(<QueryClientProvider client={client}><CatalogHarness /></QueryClientProvider>));
    expect(api.get).toHaveBeenCalledWith('/api/bioxp/operator-controls/v2/catalog', {
        timeout: 12_000, signal: expect.any(AbortSignal),
    });
    const options = vi.mocked(api.get).mock.calls[0][1]!;
    const query = client.getQueryCache().getAll()[0];
    expect(query.options).toMatchObject({ staleTime: 15_000, refetchInterval: 10_000, retry: false, refetchIntervalInBackground: false });
    expect(options.signal?.aborted).toBe(false);
    await act(async () => root.render(null));
    expect(options.signal?.aborted).toBe(true);
});

it('shows a timed-out catalog read as an explicit error and recovers on a later poll', async () => {
    vi.useFakeTimers();
    let calls = 0;
    function CatalogHarness() {
        const query = useBioXpOperatorControlCatalogV2(7);
        return <><output>{query.status}</output><BioXpQuickDashboard connected data={undefined}
            isLoading={query.isLoading} error={query.error} motionControlsAvailable={undefined}
            unavailableReason={query.isSuccess ? 'Catalog recovered; telemetry not reported.' : undefined} /></>;
    }
    vi.mocked(api.get).mockImplementation((_url, options) => {
        calls++;
        if (calls > 1) return Promise.resolve({ data: { dashboard: { generated_at: Date.now() / 1000, telemetry: null } } }) as never;
        // Model the transport timeout rejection, not a never-settling GET.
        // Missing timeout leaves this pending and causes the regression to fail.
        return new Promise((_resolve, reject) => {
            if (options?.timeout) setTimeout(() => reject(Object.assign(new Error(`timeout of ${options.timeout}ms exceeded`), { code: 'ECONNABORTED' })), options.timeout);
        });
    });
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><CatalogHarness /></QueryClientProvider>));
        expect(container.textContent).toContain('Loading live state');
        await act(async () => { await vi.advanceTimersByTimeAsync(11_999); });
        expect(container.textContent).toContain('Loading live state');
        expect(api.get).toHaveBeenCalledTimes(1); // interval cannot duplicate an in-flight GET
        await act(async () => { await vi.advanceTimersByTimeAsync(1); });
        await act(async () => { await vi.advanceTimersByTimeAsync(1); }); // query observer notification
        expect(container.textContent).toContain('Dashboard unavailable: timeout of 12000ms exceeded');
        expect(container.textContent).not.toContain('Loading live state');
        expect(container.querySelector('output')?.textContent).toBe('error');
        await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
        await act(async () => { await vi.advanceTimersByTimeAsync(1); });
        expect(api.get).toHaveBeenCalledTimes(2);
        expect(container.querySelector('output')?.textContent).toBe('success');
        expect(container.textContent).toContain('Catalog recovered');
        expect(container.textContent).not.toContain('Dashboard unavailable');
        expect(api.post).not.toHaveBeenCalled(); // no STOP/abort/action as a side effect
    } finally {
        await act(async () => root.render(null));
        client.clear();
        vi.useRealTimers();
    }
});

it('fetches history at the requested depth and keeps enablement out of cache identity', async () => {
    await render();
    await vi.waitFor(() => expect(client.getQueryData(historyKey(7, 8))).toEqual(history('completed')));
    expect(api.get).toHaveBeenCalledWith('/api/bioxp/operator-controls/history?limit=8', { signal: expect.any(AbortSignal), params: undefined });
    await render(false);
    expect(client.getQueryData(historyKey(7, 8))).toEqual(history('completed'));
});
it.each(['invoke', 'assess'])('%s invalidates matching pages without synthesizing history rows or polluting other generations', async (kind) => {
    await render(false);
    for (const generation of [7, 8]) for (const limit of [8, 25]) client.setQueryData(historyKey(generation, limit), history('completed', limit));
    vi.mocked(api.post).mockResolvedValue({ data: { command_id: 'new', status: 'queued' } });
    await act(async () => {
        if (kind === 'invoke') await invoke.mutateAsync({ actionId: 'oem.x.move_steps', connectionGeneration: 7, ownershipGeneration: 1, inputs: {} });
        else await assess.mutateAsync({ commandId: 'new', connectionGeneration: 7, ownershipGeneration: 1, verdict: 'pass', note: '' });
    });
    for (const limit of [8, 25]) {
        const data = client.getQueryData<ReturnType<typeof history>>(historyKey(7, limit))!;
        expect(data).toEqual(history('completed', limit));
        expect(client.getQueryState(historyKey(7, limit))?.isInvalidated).toBe(true);
        expect(client.getQueryState(historyKey(8, limit))?.isInvalidated).toBe(false);
        expect(client.getQueryData(historyKey(8, limit))).toEqual(history('completed', limit));
    }
});
it('polls only nonterminal history and keeps method reconciliation polling after an initial read failure', async () => {
    await render(false);
    const query = client.getQueryCache().find({ queryKey: historyKey(7, 8), exact: true });
    expect(query).toBeDefined();
    const interval = query!.options.refetchInterval as (query: unknown) => number | false;
    expect(typeof interval).toBe('function');
    for (const status of ['queued', 'active', 'dispatched']) expect(interval({ state: { data: history(status) } })).toBe(1000);
    for (const status of ['completed', 'failed', 'rejected', 'cleared']) expect(interval({ state: { data: history(status) } })).toBe(false);
    expect(interval({ state: {} })).toBe(false);
    const method = client.getQueryCache().find({ queryKey: ['bioxp', 'operator-controls', 'v2', 'method', 'xy-one', 7] })!;
    const methodInterval = method.options.refetchInterval as (query: unknown) => number | false;
    expect(methodInterval({ state: {} })).toBe(500);
    expect(methodInterval({ state: { data: { status: 'active' } } })).toBe(500);
    expect(methodInterval({ state: { data: { status: 'failed' } } })).toBe(false);
});


it('mounted software Abort issues exactly one HTTP cancellation without addressed fanout and resets late receipts', async () => {
    let abort!: ReturnType<typeof useInterruptBioXpOperatorActionV1>;
    function Slot() { abort = useInterruptBioXpOperatorActionV1(); void abort.isPending; void abort.data; return null; }
    await act(async () => root.render(<QueryClientProvider client={client}><Slot /></QueryClientProvider>));
    let finish!: (value: unknown) => void;
    vi.mocked(api.post).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }) as never);
    const request = { expected_connection_generation: 7, schema_version: 'bioxp.operator_interrupt_request.v1' as const, idempotency_key: 'software-abort-one', reason: 'Cancel software waiters only; motors may continue', observed_ownership_generation: 2, observed_board_epoch_by_board: {} };
    let pending!: ReturnType<typeof abort.mutateAsync>;
    await act(async () => { pending = abort.mutateAsync({ actionId: 'oem.abort_all', request }); });
    await act(async () => { await vi.waitFor(() => expect(abort.isPending).toBe(true)); });
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/api/bioxp/operator-controls/v2/interrupts/oem.abort_all', request);
    // The cockpit resets independent slots on disconnect/generation change.
    await act(async () => abort.reset());
    await act(async () => { finish({ data: { action_id: 'oem.abort_all', command_id: 'old-software-abort', terminal: true, status: 'completed' } }); await pending; });
    expect(abort.data).toBeUndefined();
    expect(api.post).toHaveBeenCalledTimes(1);
});

it('fetches full retained evidence only on expansion and hides it on disconnect', async () => {
    const row = historyItem({ command_id: 'retained-proof', action_id: 'oem.z.manual_home', status: 'failed', source: 'legacy_operator_plane' });
    const source = { command_id: row.command_id, terminal_evidence: { retained_original: 'native-proof-value' } };
    api.get.mockResolvedValue({ data: { ...row, source_receipt: source } });
    const render = (connected: boolean) => root.render(<QueryClientProvider client={client}><BioXpHistoryReceiptCard receipt={row} generation={7} connected={connected} /></QueryClientProvider>);
    await act(async () => { render(true); });
    expect(api.get).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain('native-proof-value');
    await act(async () => {
        const disclosure = container.querySelector('details')!;
        disclosure.open = true;
        disclosure.dispatchEvent(new Event('toggle'));
    });
    await flush();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0]).toEqual(['/api/bioxp/operator-controls/v2/receipts/retained-proof', { params: { detail: true } }]);
    expect(container.textContent).toContain('native-proof-value');
    await act(async () => { render(false); });
    expect(container.textContent).not.toContain('native-proof-value');
    expect(api.post).not.toHaveBeenCalled();
});

it('owns history cursors by connection and page size, using the single route for every page', async () => {
    let pagination!: ReturnType<typeof useBioXpHistoryPagination>;
    function Paged({ generation, limit }: { generation: number; limit: number }) {
        pagination = useBioXpHistoryPagination(generation, limit);
        useBioXpOperatorActionHistory(generation, true, limit, pagination.cursor);
        return null;
    }
    api.get.mockResolvedValue({ data: history('completed', 8) });
    const render = (generation: number, limit: number) => root.render(<QueryClientProvider client={client}><Paged generation={generation} limit={limit} /></QueryClientProvider>);
    await act(async () => { render(7, 8); });
    await flush();
    await act(async () => { pagination.older('older-one'); });
    await flush();
    expect(pagination.cursor).toBe('older-one');
    expect(api.get.mock.calls.at(-1)).toEqual(['/api/bioxp/operator-controls/history?limit=8', { signal: expect.any(AbortSignal), params: { cursor: 'older-one' } }]);
    await act(async () => { render(7, 25); });
    await flush();
    expect(pagination.cursor).toBeNull();
    await act(async () => { render(7, 8); });
    expect(pagination.cursor).toBeNull();
    await act(async () => { pagination.older('older-two'); });
    await act(async () => { render(8, 8); });
    await flush();
    expect(pagination.cursor).toBeNull();
    expect(pagination.hasNewer).toBe(false);
    expect(api.get.mock.calls.every(([url]) => !String(url).includes('/v2/history'))).toBe(true);
});
