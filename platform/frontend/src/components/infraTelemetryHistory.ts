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
