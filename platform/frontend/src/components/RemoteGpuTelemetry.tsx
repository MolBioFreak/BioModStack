import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { AxiosResponse } from 'axios';
import type { RemoteGpuTelemetry as Telemetry, RemoteTelemetrySample } from '../lib/api';
import { TimeSeriesPlot } from './telemetryMetricPlot';
import { mergeRemoteTelemetry, remoteMetricSeries } from './remoteTelemetryHistory';

import { fetchActiveRemoteGpuTelemetry } from '../lib/api';

const asNumber = (value: unknown): number | null => {
    if (value == null || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

const REMOTE_SIZING = {
    micro: { plotHeight: 72, padding: 'p-2', gap: 'gap-1', spacing: 'space-y-1', margin: 'mt-1' },
    compact: { plotHeight: 96, padding: 'p-3', gap: 'gap-1.5', spacing: 'space-y-1.5', margin: 'mt-2' },
    standard: { plotHeight: 120, padding: 'p-4', gap: 'gap-3', spacing: 'space-y-4', margin: 'mt-4' },
    large: { plotHeight: 156, padding: 'p-5', gap: 'gap-4', spacing: 'space-y-5', margin: 'mt-5' },
    xlarge: { plotHeight: 196, padding: 'p-6', gap: 'gap-5', spacing: 'space-y-6', margin: 'mt-6' },
};

export function RemoteGpuTelemetry({ dashboardSize = 'standard' }: { dashboardSize?: keyof typeof REMOTE_SIZING }) {
    const sizing = REMOTE_SIZING[dashboardSize];
    const client = useQueryClient();
    const telemetryQuery = useQuery({
        queryKey: ['active-remote-gpu-telemetry'],
        queryFn: async () => {
            const previous = client.getQueryData<AxiosResponse<Telemetry>>(['active-remote-gpu-telemetry']);
            const next = await fetchActiveRemoteGpuTelemetry(previous?.data.cursor);
            return { ...next, data: mergeRemoteTelemetry(previous?.data, next.data) };
        },
        refetchInterval: 10_000,
        refetchIntervalInBackground: false,
        retry: false,
    });

    if (telemetryQuery.isLoading) {
        return <div className="rounded-xl border border-slate-800 bg-slate-900 p-5 text-sm text-slate-400">Reading the active Vast worker…</div>;
    }
    if (telemetryQuery.isError || !telemetryQuery.data) {
        return (
            <div role="alert" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">
                Remote telemetry could not be read.
            </div>
        );
    }

    const telemetry = telemetryQuery.data.data;
    if (!telemetry.target) {
        return (
            <div role="alert" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">
                No active ready Vast worker is available. Attach one from the Dashboard.
            </div>
        );
    }
    const hourly = asNumber(telemetry.target.pricing.hourly_rate);
    const startedAt = typeof telemetry.target.pricing.provider_started_at === 'string'
        ? Date.parse(telemetry.target.pricing.provider_started_at)
        : Number.NaN;
    const estimatedCost = hourly != null && Number.isFinite(startedAt)
        ? Math.max(0, Date.now() - startedAt) / 3_600_000 * hourly
        : null;

    const history = telemetry.history ?? [];
    const metric = (title: string, suffix: string, value: (s: RemoteTelemetrySample) => number | null | undefined, maximum?: number) => (
        <TimeSeriesPlot key={title} height={sizing.plotHeight} samples={[]} xDomain={[Date.now() - 3_600_000, Date.now()]}
            yAxis={{ title, suffix, color: '#22d3ee', ...(maximum == null ? {} : { range: [0, maximum] as [number, number] }) }}
            series={[{ name: title, line: { color: '#22d3ee' }, ...remoteMetricSeries(history, value) }]} />
    );
    const gib = (v: number | null | undefined) => v == null ? null : v / 1024 ** 3;
    return (
        <div className={sizing.spacing} data-bms-remote-telemetry="true" data-bms-telemetry-size={dashboardSize}>
            {!telemetry.available && <div role="alert" className="text-sm text-amber-200">Remote telemetry unavailable. {telemetry.error}</div>}
            <div className={`flex flex-wrap items-center justify-between rounded-xl border border-slate-700 bg-slate-900 ${sizing.padding} ${sizing.gap}`}>
                <div>
                    <div className="text-sm font-semibold text-emerald-100">Vast instance {telemetry.target.provider_instance_id}</div>
                    <div className="mt-1 text-xs text-emerald-200/80">Read-only remote telemetry · {telemetry.observed_at ? new Date(telemetry.observed_at).toLocaleString() : 'Waiting for sample'} · 10-second collection · 1-hour history</div>
                </div>
                <div className="text-right text-xs text-emerald-100">
                    <div>{hourly == null ? 'Rate unavailable' : `$${hourly.toFixed(3)}/hr`}</div>
                    {estimatedCost != null && <div className="mt-1">Estimated session: ${estimatedCost.toFixed(2)}</div>}
                </div>
            </div>

            <div className={`grid md:grid-cols-2 xl:grid-cols-3 ${sizing.gap}`}>
                {telemetry.gpus.map((gpu) => {
                    const memoryPercent = gpu.memory_total_mb != null && gpu.memory_used_mb != null && gpu.memory_total_mb > 0
                        ? Math.min(100, gpu.memory_used_mb / gpu.memory_total_mb * 100)
                        : 0;
                    return (
                        <article key={gpu.id} className={`rounded-xl border border-slate-800 bg-slate-900 ${sizing.padding}`}>
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <h3 className="text-sm font-semibold text-slate-100">Remote GPU {gpu.index}</h3>
                                    <p className="mt-1 text-xs text-slate-400">{gpu.name}</p>
                                </div>
                                <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-200">
                                    {gpu.utilization == null ? 'N/A' : `${gpu.utilization.toFixed(0)}%`}
                                </span>
                            </div>
                            <div className={`h-2 overflow-hidden rounded-full bg-slate-800 ${sizing.margin}`}>
                                <div className="h-full bg-emerald-400" style={{ width: `${memoryPercent}%` }} />
                            </div>
                            <div className="mt-2 flex justify-between text-xs text-slate-400">
                                <span>{gpu.memory_used_mb?.toLocaleString() ?? 'N/A'} MB used</span>
                                <span>{gpu.memory_total_mb?.toLocaleString() ?? 'N/A'} MB</span>
                            </div>
                            <div className={`grid grid-cols-2 text-xs ${sizing.margin} ${sizing.gap}`}>
                                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-300">
                                    Temperature<br /><span className="text-slate-100">{gpu.temperature == null ? 'N/A' : `${gpu.temperature.toFixed(0)}°C`}</span>
                                </div>
                                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-300">
                                    Power<br /><span className="text-slate-100">{gpu.power_draw_w == null ? 'N/A' : `${gpu.power_draw_w.toFixed(1)} W`}</span>
                                </div>
                            </div>
                            <div className={`${sizing.margin} ${sizing.spacing}`}>
                                {metric('GPU utilization', '%', s => s.gpus.find(g => g.id === gpu.id)?.utilization, 100)}
                                {metric('VRAM used', ' MiB', s => s.gpus.find(g => g.id === gpu.id)?.memory_used_mb, gpu.memory_total_mb ?? undefined)}
                                {metric('Temperature', '°C', s => s.gpus.find(g => g.id === gpu.id)?.temperature)}
                                {metric('Power', ' W', s => s.gpus.find(g => g.id === gpu.id)?.power_draw_w)}
                            </div>
                        </article>
                    );
                })}
            </div>
            <div className="text-xs text-slate-400">CPU allocation: {telemetry.cpu?.allocated_cores ?? 'N/A'} cores · CPU scope: {telemetry.cpu?.scope ?? 'N/A'} · RAM scope: {telemetry.ram?.scope ?? 'N/A'} · RAM limit: {gib(telemetry.ram?.limit_bytes)?.toFixed(2) ?? 'N/A'} GiB · Work filesystem: {telemetry.disk?.path ?? 'N/A'} · Total: {gib(telemetry.disk?.total_bytes)?.toFixed(2) ?? 'N/A'} GiB</div>
            <div className={`grid md:grid-cols-2 ${sizing.gap}`}>
                {metric('CPU utilization', '%', s => s.cpu?.utilization, 100)}
                {metric('RAM used', ' GiB', s => gib(s.ram?.used_bytes), gib(telemetry.ram?.limit_bytes) ?? undefined)}
                {metric('Work filesystem free', ' GiB', s => gib(s.disk?.free_bytes), gib(telemetry.disk?.total_bytes) ?? undefined)}
                {[...new Set(history.flatMap(s => (s.network ?? []).map(n => n.interface)))].map(name => <div key={name} className={sizing.spacing}>
                    {metric(`${name} RX throughput`, ' MiB/s', s => { const n = s.network?.find(n => n.interface === name)?.rx_bytes_per_second; return n == null ? null : n / 1024 ** 2; })}
                    {metric(`${name} TX throughput`, ' MiB/s', s => { const n = s.network?.find(n => n.interface === name)?.tx_bytes_per_second; return n == null ? null : n / 1024 ** 2; })}
                </div>)}
            </div>
        </div>
    );
}
