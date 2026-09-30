import React, { act } from 'react';
import { readFileSync, writeFileSync } from 'node:fs';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import metadata from '../fixtures/bioxp_xy_bms_metadata.json';
import { BioXpCockpit } from '../../src/components/BioXpCockpit';
vi.mock('../../src/lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock('../../src/components/BioXpCameraPanel', () => ({ BioXpCameraPanel: () => null }));
it('mounted cockpit and opened Advanced share the exact catalog including draft target and embedded dashboards', async () => {
    vi.useFakeTimers();
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const catalog = process.env.BMS_CATALOG_PRODUCER
        ? JSON.parse(readFileSync(process.env.BMS_CATALOG_PRODUCER, 'utf8')).bms
        : { schema_version: 'bioxp.operator_control_catalog.v1', actions: [], ownership_generation: 1,
            dashboard: metadata.catalog.dashboard.telemetry, canonical: structuredClone(metadata.catalog), source_authority_verified: true };
    // Freshness clock is a mounted cadence fixture; producer fields are otherwise unchanged.
    catalog.canonical.dashboard.generated_at = Date.now() / 1000;
    vi.mocked(api.get).mockImplementation(async url => {
        if (url === '/api/bioxp/status') return { data: { connection: { active: true, configured: true, generation: 7, reachable: true } } };
        if (url === '/api/bioxp/operator-controls/catalog') return { data: catalog };
        if (url.startsWith('/api/bioxp/operator-controls/history?')) return { data: { items: [], next_cursor: null } };
        throw new Error(`Unexpected GET ${url}`);
    });
    const tick = async (ms = 10) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    try {
        await act(async () => root.render(<QueryClientProvider client={client}><BioXpCockpit /></QueryClientProvider>)); await tick();
        const count = () => vi.mocked(api.get).mock.calls.filter(([url]) => url.endsWith('/catalog')).length;
        const disclosure = [...host.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent?.toLowerCase().includes('advanced'))!;
        expect(disclosure).toBeDefined();
        await act(async () => { disclosure.open = true; disclosure.dispatchEvent(new Event('toggle')); }); await tick();
        const started = count(); await tick(10_000);
        expect(count() - started).toBe(2); // one five-second catalog cadence, not one per surface
        for (const [url, options] of vi.mocked(api.get).mock.calls) {
            expect(url).not.toMatch(/operator-controls\/(v2\/catalog|dashboard)$/);
            if (url.endsWith('/catalog')) expect(options?.params).toEqual({ z_target_steps: 65000 });
        }
        expect(host.textContent).toContain('Individual Controls');
        if (process.env.BMS_CONSUMER_FINISH_METRICS) writeFileSync(process.env.BMS_CONSUMER_FINISH_METRICS + '.catalog.json', JSON.stringify({
            scope: 'mounted fixture response UTF-8 JSON bytes, no compression; not robot traffic or latency',
            windowMs: 10000, catalogRequests: count() - started,
            bytesPerResponse: new TextEncoder().encode(JSON.stringify(catalog)).length,
            surfaces: ['cockpit', 'Advanced'],
        }, null, 2));
    } finally { await act(async () => root.unmount()); client.clear(); host.remove(); vi.useRealTimers(); }
});
