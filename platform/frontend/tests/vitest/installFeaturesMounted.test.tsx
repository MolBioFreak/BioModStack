import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DEFAULT_BMS_FEATURES, isBmsFeatureVisible, normalizeBmsFeatureState, normalizeBmsFeatures, resolveBmsFeatureQueryState, useBmsFeatureState, useResolvedBmsFeatures } from '../../src/runtime/installFeatures';

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
    return <>
        <nav>{isBmsFeatureVisible(state, 'bioxp', true, state.known) ? 'BioXP' : 'hidden'}</nav>
        <aside>{isBmsFeatureVisible(state, 'bioxp', false, state.known) ? 'BioXP' : 'hidden'}</aside>
    </>;
}
function Route() {
    const { features, resolved, known } = useResolvedBmsFeatures();
    return <main>{!resolved ? 'pending' : features.bioxp || !known ? 'BioXP route' : 'disabled'}</main>;
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
it.each(faults)('cold %s failure keeps the checked dev-tools navigation usable', async (_name, fault) => {
    fetchMock.mockImplementation(fault);
    await mount();
    expect(client.getQueryState(key)?.status).toBe('error');
    check('BioXP', 'BioXP route');
    expect(root!.root.findByType('aside').children).toEqual(['hidden']);
    fetchMock.mockResolvedValue(reply(enabled));
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await refresh();
    check('BioXP', 'BioXP route');
});
it('cold pending is not enabled and valid developer visibility changes are applied', async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    await mount();
    check('BioXP', 'pending');
    await client.cancelQueries({ queryKey: key });
    fetchMock.mockResolvedValue(reply({ ...enabled, dev_features: { bioxp: true } }));
    await refresh();
    check('BioXP', 'BioXP route');
    expect(root!.root.findByType('aside').children).toEqual(['hidden']);
});

it('defaults ngs_ont, stats, and protein_compute on and normalizes M2 module flags', () => {
    expect(DEFAULT_BMS_FEATURES).toEqual({
        bioxp: false,
        ngs_ont: true,
        stats: true,
        protein_compute: true,
    });
    const state = normalizeBmsFeatureState({
        features: { bioxp: true, ngs_ont: false, stats: false, protein_compute: true },
        configured_features: { bioxp: true, ngs_ont: true, stats: false, protein_compute: true },
        dev_features: { bioxp: true, ngs_ont: false, stats: false, protein_compute: false },
    });
    expect(state.features).toEqual({
        bioxp: true,
        ngs_ont: false,
        stats: false,
        protein_compute: true,
    });
    expect(state.configuredFeatures.ngs_ont).toBe(true);
    expect(state.devFeatures.bioxp).toBe(true);
    expect(state.devFeatures.ngs_ont).toBe(false);
    expect(resolveBmsFeatureQueryState(undefined, true).features).toEqual(DEFAULT_BMS_FEATURES);
    expect(normalizeBmsFeatures({ features: { bioxp: true } })).toEqual({
        bioxp: true,
        ngs_ont: true,
        stats: true,
        protein_compute: true,
    });
});
