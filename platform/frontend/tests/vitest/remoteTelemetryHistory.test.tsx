import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RemoteGpuTelemetry as Panel } from '../../src/components/RemoteGpuTelemetry';
import { describe, expect, it } from 'vitest';
import { mergeRemoteTelemetry, remoteMetricSeries } from '../../src/components/remoteTelemetryHistory';
import type { RemoteGpuTelemetry, RemoteTelemetrySample } from '../../src/lib/api';

const sample = (sequence: number, ago = 0): RemoteTelemetrySample => ({ sequence, observed_at: new Date(Date.now() - ago).toISOString(), available: true, gpus: [] });
const payload = (history: RemoteTelemetrySample[], reset = false) => ({ source: 'active_vast', available: true, target: { id: 'vast:8' }, gpus: [], history, reset }) as RemoteGpuTelemetry;

describe('central remote history', () => {
    it('mounts eight GPUs with measured-unit charts and honest missing readings', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
        const value = payload([sample(1), sample(2)]);
        value.target = { ...value.target, provider_instance_id: '8', pricing: {} } as RemoteGpuTelemetry['target'];
        value.gpus = Array.from({ length: 8 }, (_, index) => ({ id: `vast:8:gpu:${index}`, execution_target_id: 'vast:8', index,
            uuid: `GPU-${index}`, name: 'Fixture GPU', utilization: null, memory_used_mb: null, memory_total_mb: 24000,
            temperature: null, power_draw_w: null, controls: { fan: false, power: false } }));
        client.setQueryData(['active-remote-gpu-telemetry'], { data: value });
        const container = document.createElement('div');
        const root = createRoot(container);
        await act(async () => root.render(<QueryClientProvider client={client}><Panel /></QueryClientProvider>));
        expect(container.querySelectorAll('article')).toHaveLength(8);
        expect(container.textContent).toContain('N/A');
        expect(container.textContent).toContain('No samples');
        const labels = [...container.querySelectorAll('[data-bms-telemetry-plot]')].map(e => e.getAttribute('aria-label')).join(' ');
        expect(labels).toContain('VRAM used (MiB)');
        expect(labels).toContain('Power (W)');
        expect(labels).toContain('RAM used (GiB)');
        await act(async () => root.unmount());
        client.clear();
    });
    it('merges deltas without duplicates and bounds one hour', () => {
        const merged = mergeRemoteTelemetry(payload([sample(1, 3_700_000), sample(2)]), payload([sample(2), sample(3)]));
        expect(merged.history?.map(s => s.sequence)).toEqual([2, 3]);
        expect(mergeRemoteTelemetry(merged, payload([sample(4)], true)).history?.map(s => s.sequence)).toEqual([4]);
        expect(mergeRemoteTelemetry(merged, { ...payload([]), target: null }).history).toEqual([]);
    });
    it('retains measured values and explicit missing data and breaks long gaps', () => {
        const history = [sample(1, 40_000), sample(2, 30_000), sample(3)];
        history[0].cpu = { utilization: 45 };
        history[2].cpu = { utilization: 65 };
        expect(remoteMetricSeries(history, s => s.cpu?.utilization).y).toEqual([45, null, null, 65]);
    });
});
