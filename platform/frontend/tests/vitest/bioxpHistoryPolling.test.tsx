import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { useBioXpOperatorActionHistory, useInvokeBioXpOperatorAction, useAssessBioXpOperatorAction, useBioXpOperatorMethodV1, useInvokeBioXpOperatorActionV2 } from '../../src/lib/bioxpClient';

vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
let invoke: ReturnType<typeof useInvokeBioXpOperatorAction>;
let assess: ReturnType<typeof useAssessBioXpOperatorAction>;
let invokeV2: ReturnType<typeof useInvokeBioXpOperatorActionV2>;
const historyKey = (generation: number, limit: number) => ['bioxp', 'operator-controls', 'history', generation, limit];
const history = (status: string, count = 1) => ({ schema_version: 'bioxp.operator_action_history.v1', receipts: Array.from({ length: count }, (_, i) => ({ command_id: `old-${i}`, status })) });
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

it('fetches history at the requested depth and keeps enablement out of cache identity', async () => {
    await render();
    await vi.waitFor(() => expect(client.getQueryData(historyKey(7, 8))).toEqual(history('completed')));
    expect(api.get).toHaveBeenCalledWith('/api/bioxp/operator-controls/history?limit=8');
    await render(false);
    expect(client.getQueryData(historyKey(7, 8))).toEqual(history('completed'));
});
it.each(['invoke', 'assess'])('%s updates every matching generation/depth cache without polluting other generations', async (kind) => {
    await render(false);
    for (const generation of [7, 8]) for (const limit of [8, 25]) client.setQueryData(historyKey(generation, limit), history('completed', limit));
    vi.mocked(api.post).mockResolvedValue({ data: { command_id: 'new', status: 'queued' } });
    await act(async () => {
        if (kind === 'invoke') await invoke.mutateAsync({ actionId: 'oem.x.move_steps', connectionGeneration: 7, ownershipGeneration: 1, inputs: {} });
        else await assess.mutateAsync({ commandId: 'new', connectionGeneration: 7, ownershipGeneration: 1, verdict: 'pass', note: '' });
    });
    for (const limit of [8, 25]) {
        const data = client.getQueryData<ReturnType<typeof history>>(historyKey(7, limit))!;
        expect(data.receipts[0].command_id).toBe('new');
        expect(data.receipts).toHaveLength(limit);
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
