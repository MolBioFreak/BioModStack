import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider, focusManager } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Layout } from '../../src/components/Layout';
import { ThemeProvider } from '../../src/components/ThemeProvider';
import { usePowerControl, useFanControl, useSchedulerConfig } from '../../src/lib/useControlState';
import { api } from '../../src/lib/api';

const chartLoaded = vi.hoisted(() => vi.fn());
vi.mock('../../src/components/InfraLiveTelemetry', () => { chartLoaded(); return { InfraLiveTelemetry: () => null }; });
let root: Root; let host: HTMLDivElement; let client: QueryClient; let requests: string[];
const oldAdapter = api.defaults.adapter;
const flush = async (ms = 5) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
function TelemetryControls() { usePowerControl(); useFanControl(); useSchedulerConfig(); return <span>Mounted telemetry controls</span>; }
async function mount(route: string, child: React.ReactNode = null) {
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter key={route} initialEntries={[route]}><ThemeProvider><Layout>{child}</Layout></ThemeProvider></MemoryRouter></QueryClientProvider>)); await flush();
}
const reads = () => requests.filter(url => /power-control|fan-control|scheduler-config/.test(url));
async function togglePower() {
    const button = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('POWER LIMITS'))!;
    expect(button).toBeTruthy(); await act(async () => button.click()); await flush();
}
beforeEach(() => {
    vi.useFakeTimers(); focusManager.setFocused(true); requests = [];
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    Element.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({}) })));
    api.defaults.adapter = async config => {
        expect(config.method).toBe('get'); requests.push(config.url!);
        const data = config.url === '/api/gpu/power-control' ? { enabled: false, limits: {}, hardware_limits: {} }
            : config.url === '/api/gpu/fan-control' ? { gpus: {}, backend: 'fixture' }
            : config.url === '/api/gpu/status' ? { gpus: [] } : {};
        return { data, status: 200, statusText: 'OK', headers: {}, config };
    };
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); api.defaults.adapter = oldAdapter; vi.unstubAllGlobals(); focusManager.setFocused(undefined); vi.useRealTimers(); localStorage.clear(); });
it('persistent submit/NGS shell does not import charts or poll unused control state; real menu reads on open and stops on close', async () => {
    await mount('/submit'); await flush(30000); expect(reads()).toEqual([]); expect(chartLoaded).not.toHaveBeenCalled();
    await togglePower(); expect(reads()).toEqual(['/api/gpu/power-control', '/api/gpu/fan-control']);
    expect(host.textContent).toContain('GPU Hardware Controls');
    await flush(10000); expect(reads()).toHaveLength(4);
    await togglePower(); await flush(30000); expect(reads()).toHaveLength(4);
    await togglePower(); expect(reads()).toHaveLength(6);
    await mount('/ngs'); await flush(30000); expect(reads()).toHaveLength(6);
});
it('mounted telemetry and open menu share one polling owner per query and retain invalidation refresh', async () => {
    await mount('/submit', <TelemetryControls />); expect(reads()).toHaveLength(3);
    await togglePower(); const afterOpen = reads().length;
    await flush(10000); expect(reads()).toHaveLength(afterOpen + 3);
    await act(async () => { await client.invalidateQueries({ queryKey: ['powerControl'] }); }); await flush();
    expect(reads()).toHaveLength(afterOpen + 4);
    await togglePower(); await flush(10000); expect(reads()).toHaveLength(afterOpen + 7);
    await mount('/ngs'); const closed = reads().length; await flush(30000); expect(reads()).toHaveLength(closed);
});
