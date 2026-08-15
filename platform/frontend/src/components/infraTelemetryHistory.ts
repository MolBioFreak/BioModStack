export type PollPreset = 1000 | 2000 | 5000;
export type WindowPreset = 1 | 3 | 5 | 10 | 15 | 30 | 60;
export type TelemetryResolution = 'raw' | 'minute';

const RAW_MIN_GAP_BREAK_MS = 12_000;
const MINUTE_GAP_BREAK_MS = 90_000;

export function resolveTelemetryDisplayIntervalMs(
    windowMinutes: WindowPreset,
    pollIntervalMs: PollPreset,
): number {
    if (windowMinutes === 15) return 15_000;
    if (windowMinutes === 30) return 30_000;
    if (windowMinutes === 60) return 60_000;
    return pollIntervalMs;
}

export function resolveTelemetryWindowBounds(
    nowMs: number,
    windowMinutes: WindowPreset,
    displayIntervalMs: number,
): [number, number] {
    const endMs = Math.ceil(nowMs / displayIntervalMs) * displayIntervalMs;
    return [endMs - windowMinutes * 60_000, endMs];
}

export function downsampleTelemetryTail<T extends { timestamp_ms: number }>(
    points: readonly T[],
    displayIntervalMs: number,
): T[] {
    const sampled: T[] = [];
    let bucket = Number.NaN;
    let first: T | undefined;
    let latest: T | undefined;

    const flushBucket = () => {
        if (!first || !latest) return;
        sampled.push(first);
        if (latest.timestamp_ms !== first.timestamp_ms) sampled.push(latest);
    };

    for (const point of points) {
        const pointBucket = Math.floor(point.timestamp_ms / displayIntervalMs);
        if (pointBucket !== bucket) {
            flushBucket();
            bucket = pointBucket;
            first = point;
        }
        latest = point;
    }
    flushBucket();
    return sampled;
}

export function resolveTelemetryGapBreakMs(
    resolution: TelemetryResolution,
    pollIntervalMs: PollPreset,
): number {
    return resolution === 'minute'
        ? MINUTE_GAP_BREAK_MS
        : Math.max(RAW_MIN_GAP_BREAK_MS, pollIntervalMs * 3);
}

export function mergeMinuteHistoryWithRawTail<T extends { timestamp_ms: number }>(
    minutePoints: readonly T[],
    rawPoints: readonly T[],
): T[] {
    const latestMinuteTimestampMs = minutePoints.at(-1)?.timestamp_ms;
    if (latestMinuteTimestampMs == null) return [...rawPoints];
    const rawTailStartMs = latestMinuteTimestampMs + 60_000;
    return [
        ...minutePoints,
        ...rawPoints.filter((point) => point.timestamp_ms >= rawTailStartMs),
    ];
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
