import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import zTargetProducer from '../fixtures/bioxp_z_target_producer.json';
import { useBioXpOperatorControlCatalog } from '../../src/lib/bioxpClient';

import { catalogWireFixture } from '../fixtures/bioxpCatalogWire';
const fixtureRead = vi.hoisted(() => vi.fn());
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(async (url, options) => {
    if (options?.params?.view === 'metadata') return { data: catalogWireFixture(catalog(false), 'metadata') };
    const response = await fixtureRead(url, options);
    return { ...response, data: catalogWireFixture(response.data, options?.params?.view) };
}), post: vi.fn() } }));
let client: QueryClient;
let root: Root;
let container: HTMLDivElement;
// These are the availability fields from the real stale/fresh GET transition.
// No motion is simulated or submitted by this query test.
const catalog = (stale: boolean) => ({ actions: [
    { action_id: 'gripper-home', enabled: !stale, disabled_reason: stale ? 'cached_projection_stale' : null },
    { action_id: 'gripper-open', enabled: !stale, disabled_reason: stale ? 'cached_projection_stale' : null },
    { action_id: 'door-home', enabled: !stale, disabled_reason: stale ? 'cached_projection_stale' : null },
    { action_id: 'door-open', enabled: false, disabled_reason: 'DOOR axis is not homed.' },
] });
function Controls({ enabled = true, target, generation = 7 }: { enabled?: boolean; target?: number; generation?: number }) {
    const query = useBioXpOperatorControlCatalog(generation, enabled, null, target);
    return <><output>{query.status}</output><span data-testid="preview">{query.data?.dashboard?.z_axis?.provider?.target_preview?.effective_position_steps}</span>{query.data?.actions.map(action =>
        <button key={action.action_id} disabled={!enabled || query.isError || !action.enabled}>{action.action_id}</button>,
    )}</>;
}
const render = async (enabled = true, copies = 1) => act(async () => {
    root.render(<QueryClientProvider client={client}>{Array.from({ length: copies }, (_, i) => <Controls key={i} enabled={enabled} />)}</QueryClientProvider>);
});
const advance = async (ms: number) => {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
};
const button = (label: string) => [...container.querySelectorAll('button')].find(b => b.textContent === label)!;
beforeEach(() => {
    vi.useFakeTimers(); fixtureRead.mockReset(); fixtureRead.mockClear(); vi.mocked(api.post).mockClear();
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    container = document.createElement('div'); root = createRoot(container);
});
afterEach(async () => {
    await act(async () => root.unmount()); client.clear(); vi.useRealTimers();
});
it('recovers cached gripper and door Home availability without overriding an unhomed-door restriction', async () => {
    fixtureRead.mockResolvedValueOnce({ data: catalog(true) }).mockResolvedValue({ data: catalog(false) });
    await render(); await advance(1);
    expect(button('gripper-open').disabled).toBe(true);
    expect(button('door-home').disabled).toBe(true);
    await advance(1000);
    expect(button('gripper-home').disabled).toBe(false);
    expect(button('gripper-open').disabled).toBe(false);
    expect(button('door-home').disabled).toBe(false);
    expect(button('door-open').disabled).toBe(true);
    expect(fixtureRead).toHaveBeenCalledTimes(2);
    await advance(5000);
    expect(fixtureRead).toHaveBeenCalledTimes(3);
    expect(api.post).not.toHaveBeenCalled();
});
it('recovers from a transient catalog error through a later read, not a command retry', async () => {
    fixtureRead.mockRejectedValueOnce(new Error('temporary read failure')).mockResolvedValue({ data: catalog(false) });
    await render(); await advance(1);
    expect(container.querySelector('output')?.textContent).toBe('error');
    await advance(5000);
    expect(button('gripper-home').disabled).toBe(false);
    expect(button('door-open').disabled).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
});
it('bounds a held read and never starts duplicate reads while it is pending', async () => {
    fixtureRead.mockImplementation((_url, options) => new Promise((_resolve, reject) => {
        if (options?.timeout) setTimeout(() => reject(new Error('read timed out')), options.timeout);
    }));
    await render(); await advance(10000);
    expect(fixtureRead).toHaveBeenCalledTimes(1);
    expect(fixtureRead).toHaveBeenCalledWith('/api/bioxp/operator-controls/catalog', { signal: expect.any(AbortSignal), timeout: 12000, params: { view: 'assessment', assessment_base: '', canonical_assessment_base: '' } });
    await advance(2000);
    expect(container.querySelector('output')?.textContent).toBe('error');
    expect(api.post).not.toHaveBeenCalled();
});
it('stops polling on disconnect and shares one read between mounted consumers', async () => {
    fixtureRead.mockResolvedValue({ data: catalog(false) });
    await render(true, 2); await advance(1);
    expect(fixtureRead).toHaveBeenCalledTimes(1);
    await advance(5000);
    expect(fixtureRead).toHaveBeenCalledTimes(2);
    await render(false, 2);
    await advance(20000);
    expect(fixtureRead).toHaveBeenCalledTimes(2);
    expect(api.post).not.toHaveBeenCalled();
});

it('sends exact draft queries and preserves manual availability while a new preview is pending', async () => {
    const fixture = (minimum: number, requested: number) => ({ ...catalog(false), dashboard: { z_axis: {
        provider: zTargetProducer.find(row => row.minimum === minimum && row.requested === requested)!.provider,
    } } });
    const mount = async (target: number, generation = 7) => act(async () => root.render(
        <QueryClientProvider client={client}><Controls target={target} generation={generation} /></QueryClientProvider>,
    ));
    fixtureRead.mockResolvedValue({ data: fixture(500, 0) });
    await mount(0); await advance(1);
    expect(fixtureRead).toHaveBeenLastCalledWith('/api/bioxp/operator-controls/catalog', expect.objectContaining({ params: expect.objectContaining({ view: 'assessment', z_target_steps: 0 }) }));
    expect(container.querySelector('[data-testid="preview"]')?.textContent).toBe('500');
    fixtureRead.mockResolvedValue({ data: fixture(65000, 0) });
    for (let poll = 0; poll < 3; poll++) {
        await advance(5000);
        expect(container.querySelector('[data-testid="preview"]')?.textContent).toBe('65000');
    }
    let resolve: ((value: unknown) => void) | undefined;
    fixtureRead.mockImplementation(() => new Promise(done => { resolve = done; }));
    await mount(90000); await advance(1);
    expect(fixtureRead).toHaveBeenLastCalledWith('/api/bioxp/operator-controls/catalog', expect.objectContaining({ params: expect.objectContaining({ view: 'assessment', z_target_steps: 90000 }) }));
    expect(button('gripper-home').disabled).toBe(false); // draft changes are not admission gates
    await act(async () => { resolve?.({ data: fixture(65000, 90000) }); });
    await advance(1);
    expect(container.querySelector('[data-testid="preview"]')?.textContent).toBe('90000');
    await mount(0, 8); await advance(1);
    expect(container.querySelector('[data-testid="preview"]')?.textContent).toBe('');
    expect(api.post).not.toHaveBeenCalled();
});
