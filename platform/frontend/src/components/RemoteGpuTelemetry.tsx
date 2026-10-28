import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import type { RemoteGpuTelemetry as Telemetry, RemoteTelemetrySample } from '../lib/api';
import { TimeSeriesPlot } from './telemetryMetricPlot';
import { mergeRemoteTelemetry, remoteMetricSeries } from './remoteTelemetryHistory';
import { PanelFrame, PLOT_TICK, METRIC_COLORS, legendName, formatDuration } from './telemetryCards';
import { loadPersistedTelemetryPreferences, TELEMETRY_PREFERENCES_EVENT } from './infraTelemetryHistory';

import { fetchActiveRemoteGpuTelemetry } from '../lib/api';

const asNumber = (value: unknown): number | null => {
    if (value == null || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

// Same card heights as the local dashboard cards at each panel size.
const REMOTE_SIZING = {
    micro: { plotHeight: 96, gap: 'gap-1', spacing: 'space-y-1' },
    compact: { plotHeight: 132, gap: 'gap-1.5', spacing: 'space-y-1.5' },
    standard: { plotHeight: 196, gap: 'gap-2', spacing: 'space-y-2' },
    large: { plotHeight: 244, gap: 'gap-3', spacing: 'space-y-3' },
    xlarge: { plotHeight: 310, gap: 'gap-4', spacing: 'space-y-4' },
};
const REMOTE_SAMPLE_MS = 10_000;
const MIB = 1024 ** 2;
const GIB = 1024 ** 3;

function useSharedWindowMinutes(): number {
    const [minutes, setMinutes] = useState(() => loadPersistedTelemetryPreferences(1000, 3).windowMinutes);
    useEffect(() => {
        const update = () => setMinutes(loadPersistedTelemetryPreferences(1000, 3).windowMinutes);
        window.addEventListener(TELEMETRY_PREFERENCES_EVENT, update);
        window.addEventListener('storage', update);
        return () => {
            window.removeEventListener(TELEMETRY_PREFERENCES_EVENT, update);
            window.removeEventListener('storage', update);
        };
    }, []);
    return minutes;
}

const fixed = (value: number | null | undefined, digits: number, suffix: string) =>
    value == null || !Number.isFinite(value) ? 'n/a' : `${value.toFixed(digits)}${suffix}`;

export function RemoteGpuTelemetry({ dashboardSize = 'standard', executionTargetId }: { dashboardSize?: keyof typeof REMOTE_SIZING; executionTargetId?: string }) {
    const sizing = REMOTE_SIZING[dashboardSize];
    const windowMinutes = useSharedWindowMinutes();
    const client = useQueryClient();
    const queryKey = executionTargetId == null
        ? ['active-remote-gpu-telemetry']
        : ['active-remote-gpu-telemetry', executionTargetId];
    const telemetryQuery = useQuery({
        queryKey,
        placeholderData: undefined,
        queryFn: async () => {
            const previous = client.getQueryData<AxiosResponse<Telemetry>>(queryKey);
            const next = await fetchActiveRemoteGpuTelemetry(previous?.data.cursor, executionTargetId);
            return { ...next, data: mergeRemoteTelemetry(previous?.data, next.data) };
        },
        refetchInterval: REMOTE_SAMPLE_MS,
        refetchIntervalInBackground: false,
        retry: false,
    });

    if (telemetryQuery.isLoading) {
        return <div className="rounded-2xl border border-[var(--border-primary)] bg-[var(--surface-card,var(--bg-secondary))] p-5 text-sm text-[var(--text-secondary)]">Reading the active Vast worker…</div>;
    }
    if (telemetryQuery.isError || !telemetryQuery.data) {
        return (
            <div role="alert" className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">
                Remote telemetry could not be read.
            </div>
        );
    }

    const telemetry = telemetryQuery.data.data;
    if (!telemetry.target) {
        return (
            <div role="alert" className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">
                {telemetry.error || 'No active ready Vast worker is available. Attach one from the Dashboard.'}
            </div>
        );
    }
    const hourly = asNumber(telemetry.target.pricing.hourly_rate);
    const startedAt = typeof telemetry.target.pricing.provider_started_at === 'string'
        ? Date.parse(telemetry.target.pricing.provider_started_at)
        : Number.NaN;
    const uptimeMs = Number.isFinite(startedAt) ? Math.max(0, Date.now() - startedAt) : null;
    const estimatedCost = hourly != null && uptimeMs != null ? uptimeMs / 3_600_000 * hourly : null;

    const history = telemetry.history ?? [];
    const now = Date.now();
    const windowStart = now - windowMinutes * 60_000;
    const firstSample = history.map(s => Date.parse(s.observed_at ?? '')).find(Number.isFinite);
    // A young worker fills the chart with what it has instead of an empty hour.
    const collecting = firstSample != null && firstSample > windowStart;
    const xDomain: [number, number] = [collecting ? Math.min(firstSample!, now - REMOTE_SAMPLE_MS) : windowStart, now];
    const latest = history.at(-1);

    type Strip = { title: string; suffix: string; color: string; label: string; current: string; decimals?: number; value: (s: RemoteTelemetrySample) => number | null | undefined; range?: [number, number] };
    const card = (strips: Strip[]) => (
        <TimeSeriesPlot
            height={sizing.plotHeight}
            samples={[]}
            xDomain={xDomain}
            showXAxisLabels={false}
            yAxis={{ title: 'Metrics', color: PLOT_TICK }}
            series={strips.map(strip => ({
                ...remoteMetricSeries(history, strip.value),
                axis: { title: strip.title, color: PLOT_TICK, suffix: strip.suffix, decimals: strip.decimals ?? 1, ...(strip.range ? { range: strip.range } : {}) },
                name: legendName(strip.label, strip.current),
                line: { color: strip.color, width: 1.5 },
            }))}
        />
    );
    const interfaces = [...new Set(history.flatMap(s => (s.network ?? []).map(n => n.interface)))];
    const ramLimit = telemetry.ram?.limit_bytes ?? null;
    const diskTotal = telemetry.disk?.total_bytes ?? null;
    const latestNetwork = (name: string) => latest?.network?.find(n => n.interface === name);
    const compactCards = dashboardSize === 'micro' || dashboardSize === 'compact';

    return (
        <div className={sizing.spacing} data-bms-remote-telemetry="true" data-bms-telemetry-size={dashboardSize}>
            {!telemetry.available && <div role="alert" className="text-sm text-amber-200">Remote telemetry unavailable. {telemetry.error}</div>}
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-2xl border border-[var(--border-primary)] bg-[var(--surface-card,var(--bg-secondary))] px-4 py-2 text-xs text-[var(--text-secondary)]">
                <div>
                    <span className="text-sm font-semibold text-[var(--text-primary)]">Vast instance {telemetry.target.provider_instance_id}</span>
                    <span className="ml-2" title="Read-only. The worker reports every 10 seconds and keeps one hour of history.">
                        {telemetry.observed_at ? `updated ${new Date(telemetry.observed_at).toLocaleTimeString([], { hour12: false })}` : 'waiting for first sample'}
                        {collecting ? ` · ${formatDuration(now - firstSample!)} of history` : ''}
                    </span>
                </div>
                <div className="tabular-nums" title={estimatedCost != null ? `Estimated session cost $${estimatedCost.toFixed(2)}` : undefined}>
                    {hourly == null ? 'Rate unavailable' : `$${hourly.toFixed(3)}/hr`}
                    {uptimeMs != null && ` · up ${formatDuration(uptimeMs)}`}
                </div>
            </div>

            <div className={`grid lg:grid-cols-2 2xl:grid-cols-3 ${sizing.gap}`}>
                {telemetry.gpus.map((gpu) => {
                    const totalGib = gpu.memory_total_mb != null ? gpu.memory_total_mb / 1024 : null;
                    const usedGib = gpu.memory_used_mb != null ? gpu.memory_used_mb / 1024 : null;
                    const powerLimit = asNumber(gpu.power_limit_w);
                    const own = (s: RemoteTelemetrySample) => s.gpus.find(g => g.id === gpu.id);
                    return (
                        <article key={gpu.id} className="min-w-0">
                            <PanelFrame compact={compactCards} title={gpu.name} subtitle={`Remote GPU ${gpu.index}`}>
                                {card([
                                    { title: 'GPU utilization', suffix: ' %', range: [0, 100], color: METRIC_COLORS.utilization, label: 'Util', current: fixed(gpu.utilization, 0, '%'), value: s => own(s)?.utilization },
                                    { title: 'VRAM used', suffix: ' GiB', decimals: 0, ...(totalGib ? { range: [0, totalGib] as [number, number] } : {}), color: METRIC_COLORS.memory, label: 'VRAM',
                                        current: `${fixed(usedGib, 1, '')} / ${fixed(totalGib, 0, ' GiB')}`, value: s => { const v = own(s)?.memory_used_mb; return v == null ? null : v / 1024; } },
                                    { title: 'Power draw', suffix: ' W', decimals: 0, ...(powerLimit ? { range: [0, powerLimit] as [number, number] } : {}), color: METRIC_COLORS.power, label: 'Power',
                                        current: `${fixed(gpu.power_draw_w, 0, ' W')}${powerLimit ? ` / ${powerLimit.toFixed(0)} W` : ''}`, value: s => own(s)?.power_draw_w },
                                    { title: 'Temperature', suffix: ' °C', decimals: 0, range: [0, 100], color: METRIC_COLORS.temperature, label: 'Temp', current: fixed(gpu.temperature, 0, ' °C'), value: s => own(s)?.temperature },
                                ])}
                            </PanelFrame>
                        </article>
                    );
                })}
                <article className="min-w-0">
                    <PanelFrame compact={compactCards} title="CPU, memory and disk"
                        subtitle={`${telemetry.cpu?.allocated_cores != null ? `${Number(telemetry.cpu.allocated_cores.toFixed(1))} cores` : 'cores n/a'} · ${telemetry.cpu?.scope === 'cgroup' ? 'container limits' : 'host'}${telemetry.disk?.path ? ` · ${telemetry.disk.path}` : ''}`}>
                        {card([
                            { title: 'CPU utilization', suffix: ' %', range: [0, 100], color: METRIC_COLORS.utilization, label: 'Util', current: fixed(latest?.cpu?.utilization, 1, '%'), value: s => s.cpu?.utilization },
                            { title: 'RAM used', suffix: ' GiB', decimals: 0, ...(ramLimit ? { range: [0, ramLimit / GIB] as [number, number] } : {}), color: METRIC_COLORS.memory, label: 'RAM',
                                current: `${fixed(latest?.ram?.used_bytes != null ? latest.ram.used_bytes / GIB : null, 1, '')} / ${fixed(ramLimit != null ? ramLimit / GIB : null, 0, ' GiB')}`,
                                value: s => s.ram?.used_bytes == null ? null : s.ram.used_bytes / GIB },
                            { title: 'Disk free', suffix: ' GiB', decimals: 0, ...(diskTotal ? { range: [0, diskTotal / GIB] as [number, number] } : {}), color: METRIC_COLORS.secondary, label: 'Free',
                                current: `${fixed(latest?.disk?.free_bytes != null ? latest.disk.free_bytes / GIB : null, 0, '')} / ${fixed(diskTotal != null ? diskTotal / GIB : null, 0, ' GiB')}`,
                                value: s => s.disk?.free_bytes == null ? null : s.disk.free_bytes / GIB },
                        ])}
                    </PanelFrame>
                </article>
                {interfaces.length > 0 && (
                    <article className="min-w-0">
                        <PanelFrame compact={compactCards} title="Network" subtitle={interfaces.join(', ')}>
                            {card(interfaces.flatMap(name => [
                                { title: `${name} download`, suffix: ' MiB/s', color: METRIC_COLORS.memory, label: 'RX',
                                    current: fixed(latestNetwork(name)?.rx_bytes_per_second != null ? latestNetwork(name)!.rx_bytes_per_second! / MIB : null, 1, ' MiB/s'),
                                    value: (s: RemoteTelemetrySample) => { const n = s.network?.find(n => n.interface === name)?.rx_bytes_per_second; return n == null ? null : n / MIB; } },
                                { title: `${name} upload`, suffix: ' MiB/s', color: METRIC_COLORS.power, label: 'TX',
                                    current: fixed(latestNetwork(name)?.tx_bytes_per_second != null ? latestNetwork(name)!.tx_bytes_per_second! / MIB : null, 1, ' MiB/s'),
                                    value: (s: RemoteTelemetrySample) => { const n = s.network?.find(n => n.interface === name)?.tx_bytes_per_second; return n == null ? null : n / MIB; } },
                            ]))}
                        </PanelFrame>
                    </article>
                )}
            </div>
        </div>
    );
}
