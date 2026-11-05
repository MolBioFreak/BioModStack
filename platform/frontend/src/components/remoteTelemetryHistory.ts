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

// A line breaks only after more than two missed reports, not on ordinary jitter
// (real period is the reporting interval plus collection time and tick wait).
export const DEFAULT_REMOTE_SAMPLE_SECONDS = 10;
export const remoteGapMs = (intervalSeconds?: number | null) =>
    2.5 * 1000 * (intervalSeconds != null && intervalSeconds > 0 ? intervalSeconds : DEFAULT_REMOTE_SAMPLE_SECONDS);

// Header tooltip text: the cadence the payload reports, falling back to the default.
export const remoteCadenceSeconds = (intervalSeconds?: number | null) =>
    intervalSeconds != null && intervalSeconds > 0 ? intervalSeconds : DEFAULT_REMOTE_SAMPLE_SECONDS;

export function remoteMetricSeries(history: RemoteTelemetrySample[], value: (sample: RemoteTelemetrySample) => number | null | undefined, intervalSeconds?: number | null) {
    const gap = remoteGapMs(intervalSeconds);
    const x: number[] = [], y: Array<number | null> = [];
    let previous: number | undefined;
    for (const sample of history) {
        const time = Date.parse(sample.observed_at ?? '');
        if (!Number.isFinite(time)) continue;
        if (previous != null && time - previous > gap) { x.push(time - 1); y.push(null); }
        x.push(time);
        const v = value(sample);
        y.push(v != null && Number.isFinite(v) ? v : null);
        previous = time;
    }
    return { x, y };
}
