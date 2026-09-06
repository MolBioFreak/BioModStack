import type { RemoteGpuTelemetry, RemoteTelemetrySample } from '../lib/api';

export function mergeRemoteTelemetry(previous: RemoteGpuTelemetry | undefined, next: RemoteGpuTelemetry): RemoteGpuTelemetry {
    const old = !next.reset && previous?.target?.id === next.target?.id ? previous?.history ?? [] : [];
    const cutoff = Date.now() - 3_600_000;
    const samples = new Map<number | string, RemoteTelemetrySample>();
    for (const sample of [...old, ...(next.history ?? [])]) {
        if (sample.observed_at && Date.parse(sample.observed_at) >= cutoff) {
            samples.set(sample.sequence ?? sample.observed_at, sample);
        }
    }
    return { ...next, history: next.target ? [...samples.values()].slice(-361) : [] };
}

export function remoteMetricSeries(history: RemoteTelemetrySample[], value: (sample: RemoteTelemetrySample) => number | null | undefined) {
    const x: number[] = [], y: Array<number | null> = [];
    let previous: number | undefined;
    for (const sample of history) {
        const time = Date.parse(sample.observed_at ?? '');
        if (!Number.isFinite(time)) continue;
        if (previous != null && time - previous > 20_000) { x.push(time - 1); y.push(null); }
        x.push(time);
        const v = value(sample);
        y.push(v != null && Number.isFinite(v) ? v : null);
        previous = time;
    }
    return { x, y };
}
