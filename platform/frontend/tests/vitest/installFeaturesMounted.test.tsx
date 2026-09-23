import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { isBmsFeatureVisible, useBmsFeatureState, useResolvedBmsFeatures } from '../../src/runtime/installFeatures';

const key = ['bms-install-features'];
const enabled = { features: { bioxp: true }, dev_features: { bioxp: false } };
const reply = (payload: unknown) => ({ ok: true, json: async () => payload });
const faults = [
    ['network', () => Promise.reject(new TypeError('offline'))],
    ['HTTP', async () => ({ ok: false, status: 503 })],
    ['JSON', async () => ({ ok: true, json: async () => { throw new SyntaxError('invalid JSON'); } })],
    ['missing configuration', async () => reply({})],
    ['invalid flag', async () => reply({ features: { bioxp: 'unknown' } })],
    ['invalid developer configuration', async () => reply({ ...enabled, dev_features: null })],
] as const;
let root: ReactTestRenderer | undefined;
let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;
function Navigation() {
    const state = useBmsFeatureState();
    return <nav>{isBmsFeatureVisible(state, 'bioxp', false) ? 'BioXP' : 'hidden'}</nav>;
}
function Route() {
    const { features, resolved } = useResolvedBmsFeatures();
    return <main>{!resolved ? 'pending' : features.bioxp ? 'BioXP route' : 'disabled'}</main>;
}
async function tick() { await act(async () => { await vi.advanceTimersByTimeAsync(10); }); }
async function mount() {
    await act(async () => { root = create(<QueryClientProvider client={client}><Navigation/><Route/></QueryClientProvider>); });
    await tick();
}
async function refresh() {
    await act(async () => { await client.invalidateQueries({ queryKey: key }); });
    await tick();
}
function check(nav: string, route: string) {
    expect(root!.root.findByType('nav').children).toEqual([nav]);
    expect(root!.root.findByType('main').children).toEqual([route]);
}
beforeEach(() => {
    vi.useFakeTimers();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    fetchMock = vi.fn(async () => reply(enabled));
    vi.stubGlobal('fetch', fetchMock);
});
afterEach(async () => {
    if (root) await act(async () => root!.unmount());
    root = undefined;
    client.clear();
    vi.unstubAllGlobals();
    vi.useRealTimers();
});
it.each(faults)('retains both mounted consumers through repeated %s failures, remount, disable and recovery', async (_name, fault) => {
    await mount();
    check('BioXP', 'BioXP route');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const validated = client.getQueryData(key);
    fetchMock.mockImplementation(fault);
    for (let i = 0; i < 2; i++) {
        await refresh();
        expect(client.getQueryState(key)?.status).toBe('error');
        expect(client.getQueryData(key)).toBe(validated);
        check('BioXP', 'BioXP route');
    }
    await act(async () => root!.unmount());
    await mount();
    check('BioXP', 'BioXP route');
    fetchMock.mockResolvedValue(reply({ features: { bioxp: false }, dev_features: { bioxp: true } }));
    await refresh();
    check('hidden', 'disabled');
    fetchMock.mockImplementation(fault);
    await refresh();
    check('hidden', 'disabled');
    fetchMock.mockResolvedValue(reply(enabled));
    await refresh();
    check('BioXP', 'BioXP route');
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/system/features')).toBe(true);
});
it.each(faults)('cold %s failure remains disabled until a validated success', async (_name, fault) => {
    fetchMock.mockImplementation(fault);
    await mount();
    expect(client.getQueryState(key)?.status).toBe('error');
    check('hidden', 'disabled');
    fetchMock.mockResolvedValue(reply(enabled));
    // The shell stays mounted through API restarts; no manual refresh is available.
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100); });
    check('BioXP', 'BioXP route');
});
it('a transient outage retains the last validated BioXP flag during automatic refresh', async () => {
    await mount();
    check('BioXP', 'BioXP route');
    fetchMock.mockRejectedValue(new TypeError('offline'));
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100); });
    check('BioXP', 'BioXP route');
    fetchMock.mockResolvedValue(reply({ features: { bioxp: false }, dev_features: { bioxp: true } }));
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100); });
    check('hidden', 'disabled');
});
it('cold pending is not enabled and valid developer visibility changes are applied', async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    await mount();
    check('hidden', 'pending');
    await client.cancelQueries({ queryKey: key });
    fetchMock.mockResolvedValue(reply({ ...enabled, dev_features: { bioxp: true } }));
    await refresh();
    check('hidden', 'BioXP route');
});
