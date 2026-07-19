export type PollPreset = 1000 | 2000 | 5000;
export type WindowPreset = 1 | 3 | 5 | 10 | 15 | 30 | 60;

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

export interface PersistedInfraTelemetryState {
    version: 3;
    pollIntervalMs: PollPreset;
    windowMinutes: WindowPreset;
    samples: LiveSample[];
}

export interface RestoredInfraTelemetryState {
    pollIntervalMs: PollPreset;
    windowMinutes: WindowPreset;
    samples: LiveSample[];
}

interface PersistedInfraTelemetryPreferences {
    version: 1;
    pollIntervalMs: PollPreset;
    windowMinutes: WindowPreset;
}

export const INFRA_TELEMETRY_STORAGE_KEY = 'bms_infra_live_telemetry_v1';
export const INFRA_TELEMETRY_PREFERENCES_STORAGE_KEY = 'bms_infra_live_telemetry_preferences_v1';
const MAX_WINDOW_RETENTION_MS = 60 * 60 * 1000;
const MAX_RETAINED_SAMPLES = 4_000;
export const TELEMETRY_PERSIST_INTERVAL_MS = 15_000;

/**
 * Retained telemetry history is intentionally route-scoped. Current control
 * state has its own lightweight collector, but history collection performs
 * repeated structured sample allocation and must not run on unrelated tools.
 */
export function shouldCollectTelemetryHistory(pathname: string): boolean {
    const normalizedPathname = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
    return normalizedPathname === '/' || normalizedPathname === '/infra';
}

export function isValidPollPreset(value: unknown): value is PollPreset {
    return value === 1000 || value === 2000 || value === 5000;
}

function isValidWindowPreset(value: unknown): value is WindowPreset {
    return value === 1 || value === 3 || value === 5 || value === 10 || value === 15 || value === 30 || value === 60;
}

function loadPersistedTelemetryPreferences(
    defaultPollIntervalMs: PollPreset,
    defaultWindowMinutes: WindowPreset,
): Pick<RestoredInfraTelemetryState, 'pollIntervalMs' | 'windowMinutes'> {
    if (typeof window === 'undefined') {
        return { pollIntervalMs: defaultPollIntervalMs, windowMinutes: defaultWindowMinutes };
    }
    try {
        const raw = window.localStorage.getItem(INFRA_TELEMETRY_PREFERENCES_STORAGE_KEY);
        if (!raw) return { pollIntervalMs: defaultPollIntervalMs, windowMinutes: defaultWindowMinutes };
        const parsed = JSON.parse(raw) as Partial<PersistedInfraTelemetryPreferences>;
        if (parsed.version !== 1) {
            return { pollIntervalMs: defaultPollIntervalMs, windowMinutes: defaultWindowMinutes };
        }
        return {
            pollIntervalMs: isValidPollPreset(parsed.pollIntervalMs)
                ? parsed.pollIntervalMs
                : defaultPollIntervalMs,
            windowMinutes: isValidWindowPreset(parsed.windowMinutes)
                ? parsed.windowMinutes
                : defaultWindowMinutes,
        };
    } catch {
        return { pollIntervalMs: defaultPollIntervalMs, windowMinutes: defaultWindowMinutes };
    }
}

export function trimRetainedSamples(samples: LiveSample[], nowMs = Date.now()): LiveSample[] {
    const cutoffMs = nowMs - MAX_WINDOW_RETENTION_MS;
    const futureToleranceMs = 60_000;
    return samples
        .filter((sample) => (
            Number.isFinite(sample.timestampMs)
            && sample.timestampMs >= cutoffMs
            && sample.timestampMs <= nowMs + futureToleranceMs
        ))
        .slice(-MAX_RETAINED_SAMPLES);
}

function normalizeSamples(samples: LiveSample[]): LiveSample[] {
    const deduped = new Map<number, LiveSample>();
    for (const sample of samples) {
        if (!Number.isFinite(sample.timestampMs)) continue;
        deduped.set(sample.timestampMs, sample);
    }
    return Array.from(deduped.values()).sort((a, b) => a.timestampMs - b.timestampMs);
}

function formatClock(timestampMs: number): string {
    const date = new Date(timestampMs);
    return date.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

export function parseTelemetryTimestampMs(timestamp: string): number {
    const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(timestamp) ? timestamp : `${timestamp}Z`;
    return Date.parse(normalized);
}

function parseStoredSample(value: unknown): LiveSample | null {
    if (!value || typeof value !== 'object') return null;
    const sample = value as Partial<LiveSample>;
    if (typeof sample.timestamp !== 'string') return null;
    const timestampMs = parseTelemetryTimestampMs(sample.timestamp);
    if (!Number.isFinite(timestampMs)) return null;
    if (
        !isValidPollPreset(sample.pollIntervalMs)
        || typeof sample.cpuUtil !== 'number'
        || typeof sample.cpuFreqMhz !== 'number'
        || typeof sample.ramUsed !== 'number'
        || typeof sample.ramFree !== 'number'
        || typeof sample.ramUtil !== 'number'
        || typeof sample.ramSwap !== 'number'
    ) {
        return null;
    }
    if (!sample.gpu || typeof sample.gpu !== 'object') return null;

    return {
        timestamp: new Date(timestampMs).toISOString(),
        timestampMs,
        pollIntervalMs: sample.pollIntervalMs,
        clock: formatClock(timestampMs),
        cpuUtil: sample.cpuUtil,
        cpuFreqMhz: sample.cpuFreqMhz,
        cpuPower: typeof sample.cpuPower === 'number' ? sample.cpuPower : null,
        cpuTemp: typeof sample.cpuTemp === 'number' ? sample.cpuTemp : null,
        ramUsed: sample.ramUsed,
        ramFree: sample.ramFree,
        ramUtil: sample.ramUtil,
        ramSwap: sample.ramSwap,
        gpu: sample.gpu as LiveSample['gpu'],
    };
}

export function loadPersistedTelemetryState(
    defaultPollIntervalMs: PollPreset,
    defaultWindowMinutes: WindowPreset,
    nowMs: number = Date.now(),
): RestoredInfraTelemetryState {
    if (typeof window === 'undefined') {
        return {
            pollIntervalMs: defaultPollIntervalMs,
            windowMinutes: defaultWindowMinutes,
            samples: [],
        };
    }

    try {
        const raw = window.localStorage.getItem(INFRA_TELEMETRY_STORAGE_KEY);
        if (!raw) {
            return {
                ...loadPersistedTelemetryPreferences(defaultPollIntervalMs, defaultWindowMinutes),
                samples: [],
            };
        }

        const parsed = JSON.parse(raw) as Partial<PersistedInfraTelemetryState>;
        if (parsed.version !== 3) {
            return {
                ...loadPersistedTelemetryPreferences(defaultPollIntervalMs, defaultWindowMinutes),
                samples: [],
            };
        }
        const legacyPollIntervalMs = isValidPollPreset(parsed.pollIntervalMs)
            ? parsed.pollIntervalMs
            : defaultPollIntervalMs;
        const legacyWindowMinutes = isValidWindowPreset(parsed.windowMinutes)
            ? parsed.windowMinutes
            : defaultWindowMinutes;
        const { pollIntervalMs, windowMinutes } = loadPersistedTelemetryPreferences(
            legacyPollIntervalMs,
            legacyWindowMinutes,
        );
        const samples = Array.isArray(parsed.samples)
            ? trimRetainedSamples(
                normalizeSamples(parsed.samples.map(parseStoredSample).filter(Boolean) as LiveSample[]),
                nowMs,
            )
            : [];

        return { pollIntervalMs, windowMinutes, samples };
    } catch {
        return {
            ...loadPersistedTelemetryPreferences(defaultPollIntervalMs, defaultWindowMinutes),
            samples: [],
        };
    }
}

export function persistTelemetryState(state: PersistedInfraTelemetryState): void {
    if (typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(INFRA_TELEMETRY_STORAGE_KEY, JSON.stringify(state));
    } catch {
        // Ignore storage quota / availability failures and keep live telemetry flowing.
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
        // Preferences are opportunistic; storage failures must not stop telemetry.
    }
}

export function shouldPersistTelemetryHistory(lastPersistedAtMs: number, nowMs: number): boolean {
    return nowMs - lastPersistedAtMs >= TELEMETRY_PERSIST_INTERVAL_MS;
}

/**
 * Fast path for the normal collector case: monotonically increasing samples.
 * This avoids rebuilding a Map and sorting the full retained hour every second.
 * Out-of-order values still fall back to the defensive reconciliation path.
 */
export function appendRetainedTelemetrySample(
    previousSamples: LiveSample[],
    nextSample: LiveSample,
    nowMs = Date.now(),
): LiveSample[] {
    const previousLast = previousSamples.at(-1);
    if (previousLast && nextSample.timestampMs <= previousLast.timestampMs) {
        return reconcileTelemetrySamples(previousSamples, [], nextSample);
    }

    const cutoffMs = nowMs - MAX_WINDOW_RETENTION_MS;
    let firstRetainedIndex = 0;
    while (
        firstRetainedIndex < previousSamples.length
        && previousSamples[firstRetainedIndex]!.timestampMs < cutoffMs
    ) {
        firstRetainedIndex += 1;
    }

    const retained = firstRetainedIndex === 0
        ? previousSamples
        : previousSamples.slice(firstRetainedIndex);
    const next = [...retained, nextSample];
    return next.length > MAX_RETAINED_SAMPLES
        ? next.slice(next.length - MAX_RETAINED_SAMPLES)
        : next;
}

export function mergeTelemetrySample(previousSamples: LiveSample[], nextSample: LiveSample): LiveSample[] {
    const normalizedPrevious = normalizeSamples(previousSamples);
    if (normalizedPrevious.length > 0 && normalizedPrevious[normalizedPrevious.length - 1]?.timestamp === nextSample.timestamp) {
        return normalizedPrevious;
    }
    return trimRetainedSamples(normalizeSamples([...normalizedPrevious, nextSample]), nextSample.timestampMs);
}

export function reconcileTelemetryHistories(
    previousSamples: LiveSample[],
    persistedSamples: LiveSample[],
    nowMs = Date.now(),
): LiveSample[] {
    return trimRetainedSamples(
        normalizeSamples([...previousSamples, ...persistedSamples]),
        nowMs,
    );
}

export function reconcileTelemetrySamples(
    previousSamples: LiveSample[],
    persistedSamples: LiveSample[],
    nextSample: LiveSample,
): LiveSample[] {
    return reconcileTelemetryHistories(
        [...previousSamples, nextSample],
        persistedSamples,
        nextSample.timestampMs,
    );
}
