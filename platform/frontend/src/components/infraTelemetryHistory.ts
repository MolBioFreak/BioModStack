export type PollPreset = 1000 | 2000 | 5000;
export type WindowPreset = 1 | 3 | 5 | 10 | 15 | 30 | 60;

const RAW_MIN_GAP_BREAK_MS = 12_000;
const TELEMETRY_COLLECTION_STALE_AFTER_MS = 15_000;

export function resolveTelemetryDisplayIntervalMs(
    windowMinutes: WindowPreset,
    pollIntervalMs: PollPreset,
): number {
    if (windowMinutes === 10) return 5_000;
    if (windowMinutes === 15) return 10_000;
    if (windowMinutes === 30) return 15_000;
    if (windowMinutes === 60) return 30_000;
    return pollIntervalMs;
}

export function resolveTelemetryStaleAfterMs(pollIntervalMs: PollPreset): number {
    return Math.min(
        TELEMETRY_COLLECTION_STALE_AFTER_MS,
        Math.max(10_000, pollIntervalMs * 5),
    );
}

export function resolveTelemetryFreshnessObservedAtMs(
    ...observedAtMs: number[]
): number {
    let latestObservedAtMs = Number.NaN;
    for (const observedAtMsValue of observedAtMs) {
        if (
            Number.isFinite(observedAtMsValue)
            && (!Number.isFinite(latestObservedAtMs) || observedAtMsValue > latestObservedAtMs)
        ) {
            latestObservedAtMs = observedAtMsValue;
        }
    }
    return latestObservedAtMs;
}

export function resolveTelemetryBucketIntervalMs(
    windowMinutes: WindowPreset,
    pollIntervalMs: PollPreset,
): number {
    if (windowMinutes <= 5) return pollIntervalMs;
    if (windowMinutes === 10) return 5_000;
    if (windowMinutes === 15) return 10_000;
    if (windowMinutes === 30) return 15_000;
    return 30_000;
}

export function resolveTelemetryWindowBounds(
    nowMs: number,
    windowMinutes: WindowPreset,
    displayIntervalMs: number,
): [number, number] {
    const endMs = Math.ceil(nowMs / displayIntervalMs) * displayIntervalMs;
    return [endMs - windowMinutes * 60_000, endMs];
}

export function isTelemetryHistoryFresh(
    latestTimestampMs: number | undefined,
    generatedAtMs: number,
    staleAfterMs: number,
    requestFailed: boolean,
): boolean {
    if (
        requestFailed
        || latestTimestampMs == null
        || !Number.isFinite(latestTimestampMs)
        || !Number.isFinite(generatedAtMs)
        || staleAfterMs < 0
    ) {
        return false;
    }
    const ageMs = generatedAtMs - latestTimestampMs;
    return ageMs >= 0 && ageMs <= staleAfterMs;
}

export function resolveTelemetryNominalDomain(
    storedStartMs: number | undefined,
    storedEndMs: number | undefined,
    wallClockMs: number,
    windowMinutes: WindowPreset,
    displayIntervalMs: number,
    requestFailed: boolean,
): [number, number] {
    if (
        !requestFailed
        && storedStartMs != null
        && storedEndMs != null
        && Number.isFinite(storedStartMs)
        && Number.isFinite(storedEndMs)
        && storedEndMs > storedStartMs
    ) {
        return [storedStartMs, storedEndMs];
    }
    return resolveTelemetryWindowBounds(wallClockMs, windowMinutes, displayIntervalMs);
}

export function resolveTelemetryPlotDomain(
    nominalDomain: readonly [number, number],
    latestPlottedTimestampMs: number | undefined,
    fresh: boolean,
): [number, number] {
    void latestPlottedTimestampMs;
    void fresh;
    return [nominalDomain[0], nominalDomain[1]];
}

export function resolveTelemetryPlotX(
    timestampMs: number,
    xMin: number,
    xMax: number,
): number | null {
    if (
        !Number.isFinite(timestampMs)
        || !Number.isFinite(xMin)
        || !Number.isFinite(xMax)
        || xMax <= xMin
        || timestampMs < xMin
        || timestampMs > xMax
    ) {
        return null;
    }
    return ((timestampMs - xMin) / (xMax - xMin)) * 1000;
}

function average(values: readonly number[]): number {
    return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function averageNullable(values: readonly (number | null)[]): number | null {
    const available = values.filter((value): value is number => value != null);
    return available.length > 0 ? average(available) : null;
}

export function resampleTelemetrySamples(samples: readonly LiveSample[], bucketIntervalMs: number): LiveSample[] {
    const buckets = new Map<number, LiveSample[]>();
    for (const sample of samples) {
        const bucketStartMs = Math.floor(sample.timestampMs / bucketIntervalMs) * bucketIntervalMs;
        const bucket = buckets.get(bucketStartMs);
        if (bucket) bucket.push(sample);
        else buckets.set(bucketStartMs, [sample]);
    }

    return [...buckets.entries()].map(([bucketStartMs, bucket]) => {
        const gpuIndices = new Set(bucket.flatMap((sample) => Object.keys(sample.gpu).map(Number)));
        const gpu: LiveSample['gpu'] = {};
        for (const index of gpuIndices) {
            const readings = bucket.flatMap((sample) => sample.gpu[index] ? [sample.gpu[index]] : []);
            gpu[index] = {
                util: average(readings.map((reading) => reading.util)),
                vram: average(readings.map((reading) => reading.vram)),
                power: average(readings.map((reading) => reading.power)),
                temp: average(readings.map((reading) => reading.temp)),
            };
        }
        const timestamp = new Date(bucketStartMs).toISOString();
        return {
            timestamp,
            timestampMs: bucketStartMs,
            pollIntervalMs: bucket.at(-1)?.pollIntervalMs ?? 1000,
            clock: timestamp.slice(11, 19),
            cpuUtil: average(bucket.map((sample) => sample.cpuUtil)),
            cpuFreqMhz: average(bucket.map((sample) => sample.cpuFreqMhz)),
            cpuPower: averageNullable(bucket.map((sample) => sample.cpuPower)),
            cpuTemp: averageNullable(bucket.map((sample) => sample.cpuTemp)),
            ramUsed: average(bucket.map((sample) => sample.ramUsed)),
            ramFree: average(bucket.map((sample) => sample.ramFree)),
            ramUtil: average(bucket.map((sample) => sample.ramUtil)),
            ramSwap: average(bucket.map((sample) => sample.ramSwap)),
            gpu,
        };
    });
}

export function resolveTelemetryGapBreakMs(bucketIntervalMs: number, pollIntervalMs: PollPreset): number {
    return Math.max(RAW_MIN_GAP_BREAK_MS, bucketIntervalMs * 1.5, pollIntervalMs * 3);
}

export interface LiveSample {
    timestamp: string;
    timestampMs: number;
    pollIntervalMs: PollPreset;
    clock: string;
    cpuUtil: number;
    cpuFreqMhz: number;
    cpuPower: number | null;
    cpuTemp: number | null;
    ramUsed: number;
    ramFree: number;
    ramUtil: number;
    ramSwap: number;
    gpu: Record<number, { util: number; vram: number; power: number; temp: number }>;
}

interface PersistedInfraTelemetryPreferences {
    version: 1;
    pollIntervalMs: PollPreset;
    windowMinutes: WindowPreset;
}

export const INFRA_TELEMETRY_PREFERENCES_STORAGE_KEY = 'bms_infra_live_telemetry_preferences_v1';

export function isValidPollPreset(value: unknown): value is PollPreset {
    return value === 1000 || value === 2000 || value === 5000;
}

function isValidWindowPreset(value: unknown): value is WindowPreset {
    return value === 1 || value === 3 || value === 5 || value === 10 || value === 15 || value === 30 || value === 60;
}

export function loadPersistedTelemetryPreferences(
    defaultPollIntervalMs: PollPreset,
    defaultWindowMinutes: WindowPreset,
): PersistedInfraTelemetryPreferences {
    const defaults: PersistedInfraTelemetryPreferences = {
        version: 1,
        pollIntervalMs: defaultPollIntervalMs,
        windowMinutes: defaultWindowMinutes,
    };
    if (typeof window === 'undefined') return defaults;

    try {
        const raw = window.localStorage.getItem(INFRA_TELEMETRY_PREFERENCES_STORAGE_KEY);
        if (!raw) return defaults;
        const parsed = JSON.parse(raw) as Partial<PersistedInfraTelemetryPreferences>;
        if (parsed.version !== 1) return defaults;
        return {
            version: 1,
            pollIntervalMs: isValidPollPreset(parsed.pollIntervalMs)
                ? parsed.pollIntervalMs
                : defaultPollIntervalMs,
            windowMinutes: isValidWindowPreset(parsed.windowMinutes)
                ? parsed.windowMinutes
                : defaultWindowMinutes,
        };
    } catch {
        return defaults;
    }
}

export function persistTelemetryPreferences(pollIntervalMs: PollPreset, windowMinutes: WindowPreset): void {
    if (typeof window === 'undefined') return;
    try {
        const preferences: PersistedInfraTelemetryPreferences = {
            version: 1,
            pollIntervalMs,
            windowMinutes,
        };
        window.localStorage.setItem(
            INFRA_TELEMETRY_PREFERENCES_STORAGE_KEY,
            JSON.stringify(preferences),
        );
    } catch {
        // Preferences are optional. Storage failures must not stop telemetry rendering.
    }
}

export function parseTelemetryTimestampMs(timestamp: string): number {
    const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(timestamp) ? timestamp : `${timestamp}Z`;
    return Date.parse(normalized);
}
