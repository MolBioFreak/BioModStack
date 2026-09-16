import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { getBioXpCameraStatus, useBioXpCameraStatus } from '../../src/lib/bioxpClient';
import { BioXpCameraPanel } from '../../src/components/BioXpCameraPanel';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

it('actual camera fetch preserves upstream identity and age, adds browser monotonic elapsed once despite backward wallclock', async () => {
    vi.useFakeTimers();
    const data = { state: 'live', available: true, frame_sequence: 7, frame_age_seconds: 3, provider_generation: 9, freshness_budget_seconds: 30 };
    let finish!: (value: unknown) => void;
    vi.mocked(api.get).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }) as never);
    const pending = getBioXpCameraStatus(41);
    await vi.advanceTimersByTimeAsync(32000);
    vi.setSystemTime(Date.now() - 600000);
    finish({ data });
    const result = await pending;
    expect(result).toMatchObject({ ...data, requestConnectionGeneration: 41, requestElapsedMs: 32000 });
    expect(api.get).toHaveBeenCalledWith('/api/bioxp/camera/status', { params: { expected_generation: 41 } });
    expect(data.frame_age_seconds).toBe(3);
});

it('actual camera hook and panel reject delayed advancing and repeated frames and fence old connection responses', async () => {
    vi.useFakeTimers();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const container = document.createElement('div');
    const root = createRoot(container);
    let query!: ReturnType<typeof useBioXpCameraStatus>;
    function Mounted({ generation }: { generation: number }) {
        query = useBioXpCameraStatus(generation);
        void query.data; // subscribe this harness to data changes as the panel does
        return <BioXpCameraPanel connected connectionGeneration={generation} mutationEnabled={false} />;
    }
    const pending: Array<(value: unknown) => void> = [];
    vi.mocked(api.get).mockImplementation((url) => String(url).endsWith('/camera/status')
        ? new Promise(resolve => pending.push(resolve)) as never
        : Promise.resolve({ data: { active: false, state: 'off', connection_generation: 1 } }) as never);
    const render = (generation: number) => act(async () => { root.render(<QueryClientProvider client={client}><Mounted generation={generation} /></QueryClientProvider>); });
    const resolve = async (sequence: number, delay: number) => {
        await act(async () => {
            await vi.advanceTimersByTimeAsync(delay);
            vi.setSystemTime(Date.now() - 60000);
            pending.shift()!({ data: { state: 'live', available: true, frame_sequence: sequence, frame_age_seconds: 1, provider_generation: 3, freshness_budget_seconds: 30 } });
            await vi.advanceTimersByTimeAsync(10);
        });
        await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    };
    try {
        await render(1);
        await resolve(1, 0);
        expect(container.textContent).toContain('Ready');
        await act(async () => { void query.refetch(); });
        await resolve(2, 31000);
        expect(container.textContent).toContain('Stale');
        expect(query.data).toMatchObject({ frame_sequence: 2, frame_age_seconds: 1, requestElapsedMs: 31000, requestConnectionGeneration: 1 });
        await act(async () => { void query.refetch(); });
        await resolve(2, 0); // shorter transit must not renew the same stale frame
        expect(container.textContent).toContain('Stale');
        await act(async () => { void query.refetch(); });
        await resolve(2, 31000);
        expect(container.textContent).toContain('Stale');
        await act(async () => { void query.refetch(); });
        await render(2);
        await resolve(3, 0); // old generation result must not be adopted
        expect(query.data).toBeUndefined();
        expect(container.textContent).not.toContain('Ready');
        await resolve(4, 0);
        expect(query.data?.requestConnectionGeneration).toBe(2);
    } finally { await act(async () => root.unmount()); client.clear(); }
});
