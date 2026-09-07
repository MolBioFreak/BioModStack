import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RemoteGpuTelemetry as Panel } from '../../src/components/RemoteGpuTelemetry';
import { describe, expect, it, vi } from 'vitest';
import { DashboardTelemetry } from '../../src/components/dashboard/DashboardTelemetry';

vi.mock('../../src/components/InfraLiveTelemetry', () => ({
    InfraLiveTelemetry: ({ dashboardSize }: { dashboardSize: string }) =>
        <div data-testid="local-telemetry" data-size={dashboardSize} />,
}));
import { mergeRemoteTelemetry, remoteMetricSeries } from '../../src/components/remoteTelemetryHistory';
import type { RemoteGpuTelemetry, RemoteTelemetrySample } from '../../src/lib/api';

const sample = (sequence: number, ago = 0): RemoteTelemetrySample => ({ sequence, observed_at: new Date(Date.now() - ago).toISOString(), available: true, gpus: [] });
const payload = (history: RemoteTelemetrySample[], reset = false) => ({ source: 'active_vast', available: true, target: { id: 'vast:8' }, gpus: [], history, reset }) as RemoteGpuTelemetry;

describe('central remote history', () => {
    it('resizes real remote charts and cards with the shared persisted size in every scope', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
        const value = payload([sample(1, 20_000), sample(2, 10_000), sample(3)]);
        value.target = { id: 'vast:8', provider_instance_id: '8', pricing: {}, active: true, state: 'ready' } as RemoteGpuTelemetry['target'];
        value.gpus = [{ id: 'gpu-0', index: 0, name: 'Fixture GPU', utilization: 45, memory_total_mb: 24000,
            memory_used_mb: 12000, temperature: null, power_draw_w: 100 }] as RemoteGpuTelemetry['gpus'];
        value.history![0].gpus = value.gpus;
        value.history![2].gpus = value.gpus;
        value.history![0].network = [{ interface: 'eth0', rx_bytes_per_second: 1048576, tx_bytes_per_second: null }];
        client.setQueryData(['execution-targets'], { data: [value.target] });
        client.setQueryData(['active-remote-gpu-telemetry'], { data: value });
        const cached = client.getQueryData(['active-remote-gpu-telemetry']);
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        const key = 'bms_dashboard_telemetry_compact_v1';
        window.localStorage.setItem(key, 'compact');
        try {
            await act(async () => root.render(<QueryClientProvider client={client}><DashboardTelemetry /></QueryClientProvider>));
            expect(container.querySelector('[data-testid="local-telemetry"]')?.getAttribute('data-size')).toBe('compact');
            const presets = [
                ['XS', 'micro', 72, 'p-2', 'space-y-1'],
                ['S', 'compact', 96, 'p-3', 'space-y-1.5'],
                ['M', 'standard', 120, 'p-4', 'space-y-4'],
                ['L', 'large', 156, 'p-5', 'space-y-5'],
                ['XL', 'xlarge', 196, 'p-6', 'space-y-6'],
            ] as const;
            for (const tab of container.querySelectorAll<HTMLButtonElement>('[role="tab"]')) {
                await act(async () => tab.click());
                expect(container.textContent).toContain('Panel Size');
                for (const [label, size, height, padding, spacing] of presets) {
                    const button = [...container.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent?.trim() === label);
                    expect(button).toBeTruthy();
                    await act(async () => button!.click());
                    expect(window.localStorage.getItem(key)).toBe(size);
                    const local = container.querySelector('[data-testid="local-telemetry"]');
                    if (!tab.textContent?.startsWith('Vast ·')) expect(local?.getAttribute('data-size')).toBe(size);
                    if (tab.textContent === 'Local') continue;
                    const remote = container.querySelector('[data-bms-remote-telemetry="true"]')!;
                    expect(remote.getAttribute('data-bms-telemetry-size')).toBe(size);
                    expect(remote.classList.contains(spacing)).toBe(true);
                    expect(remote.querySelector('article')?.classList.contains(padding)).toBe(true);
                    const plots = [...remote.querySelectorAll<HTMLElement>('[data-bms-telemetry-plot]')];
                    expect(plots).toHaveLength(9);
                    expect(plots.map(plot => plot.style.height)).toEqual(Array(9).fill(`${height}px`));
                    expect(plots.map(plot => plot.getAttribute('aria-label')).join(' ')).toContain('VRAM used (MiB)');
                    expect(plots[0].querySelector('path')?.getAttribute('d')?.match(/ M/g)).toHaveLength(2);
                    expect(remote.textContent).toContain('No samples');
                    expect(client.getQueryData(['active-remote-gpu-telemetry'])).toBe(cached);
                }
            }
        } finally {
            await act(async () => root.unmount());
            container.remove();
            client.clear();
            window.localStorage.clear();
        }
    });
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
