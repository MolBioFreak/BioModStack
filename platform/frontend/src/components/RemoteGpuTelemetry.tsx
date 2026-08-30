import { useQuery } from '@tanstack/react-query';

import { fetchActiveRemoteGpuTelemetry } from '../lib/api';

const asNumber = (value: unknown): number | null => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

export function RemoteGpuTelemetry() {
    const telemetryQuery = useQuery({
        queryKey: ['active-remote-gpu-telemetry'],
        queryFn: fetchActiveRemoteGpuTelemetry,
        refetchInterval: 5_000,
        retry: false,
    });

    if (telemetryQuery.isLoading) {
        return <div className="rounded-xl border border-slate-800 bg-slate-900 p-5 text-sm text-slate-400">Reading the active Vast worker…</div>;
    }
    if (telemetryQuery.isError || !telemetryQuery.data) {
        return (
            <div role="alert" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">
                No active ready Vast worker is available. Attach one from the Job Launcher.
            </div>
        );
    }

    const telemetry = telemetryQuery.data.data;
    if (!telemetry.target) {
        return (
            <div role="alert" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">
                No active ready Vast worker is available. Attach one from the Job Launcher.
            </div>
        );
    }
    if (!telemetry.available) {
        return (
            <div role="alert" className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-5 text-sm text-amber-100">
                Remote GPU telemetry is unavailable. {telemetry.error || 'The attached worker returned no readable GPU data.'}
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

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
                <div>
                    <div className="text-sm font-semibold text-emerald-100">Vast instance {telemetry.target.provider_instance_id}</div>
                    <div className="mt-1 text-xs text-emerald-200/80">Read-only remote telemetry · {new Date(telemetry.observed_at ?? Date.now()).toLocaleString()}</div>
                </div>
                <div className="text-right text-xs text-emerald-100">
                    <div>{hourly == null ? 'Rate unavailable' : `$${hourly.toFixed(3)}/hr`}</div>
                    {estimatedCost != null && <div className="mt-1">Estimated session: ${estimatedCost.toFixed(2)}</div>}
                </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {telemetry.gpus.map((gpu) => {
                    const memoryPercent = gpu.memory_total_mb > 0
                        ? Math.min(100, gpu.memory_used_mb / gpu.memory_total_mb * 100)
                        : 0;
                    return (
                        <article key={gpu.id} className="rounded-xl border border-slate-800 bg-slate-900 p-4">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <h3 className="text-sm font-semibold text-slate-100">Remote GPU {gpu.index}</h3>
                                    <p className="mt-1 text-xs text-slate-400">{gpu.name}</p>
                                </div>
                                <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-200">
                                    {gpu.utilization.toFixed(0)}%
                                </span>
                            </div>
                            <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-800">
                                <div className="h-full bg-emerald-400" style={{ width: `${memoryPercent}%` }} />
                            </div>
                            <div className="mt-2 flex justify-between text-xs text-slate-400">
                                <span>{gpu.memory_used_mb.toLocaleString()} MB used</span>
                                <span>{gpu.memory_total_mb.toLocaleString()} MB</span>
                            </div>
                            <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
                                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-300">
                                    Temperature<br /><span className="text-slate-100">{gpu.temperature == null ? 'N/A' : `${gpu.temperature.toFixed(0)}°C`}</span>
                                </div>
                                <div className="rounded-lg bg-slate-950 px-3 py-2 text-slate-300">
                                    Power<br /><span className="text-slate-100">{gpu.power_draw_w == null ? 'N/A' : `${gpu.power_draw_w.toFixed(1)} W`}</span>
                                </div>
                            </div>
                        </article>
                    );
                })}
            </div>
        </div>
    );
}
