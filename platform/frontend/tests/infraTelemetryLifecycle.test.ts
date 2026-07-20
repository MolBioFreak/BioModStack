import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import * as telemetryHistory from '../src/components/infraTelemetryHistory.js';
import {
    loadPersistedTelemetryState,
    mergeTelemetrySample,
    parseTelemetryTimestampMs,
    persistTelemetryPreferences,
    reconcileTelemetryHistories,
    reconcileTelemetrySamples,
    trimRetainedSamples,
    type LiveSample,
} from '../src/components/infraTelemetryHistory.js';

const STORAGE_KEY = 'bms_infra_live_telemetry_v1';
const PREFERENCES_STORAGE_KEY = 'bms_infra_live_telemetry_preferences_v1';

function sample(timestampMs: number, cpuUtil = timestampMs): LiveSample {
    const timestamp = new Date(timestampMs).toISOString();
    return {
        timestamp,
        timestampMs,
        pollIntervalMs: 1000,
        clock: timestamp,
        cpuUtil,
        cpuFreqMhz: 1000,
        cpuPower: null,
        cpuTemp: null,
        ramUsed: 1,
        ramFree: 1,
        ramUtil: 50,
        ramSwap: 0,
        gpu: {},
    };
}

test('telemetry history collection is limited to routes that render telemetry', () => {
    const shouldCollectTelemetryHistory = (
        telemetryHistory as unknown as {
            shouldCollectTelemetryHistory?: (pathname: string) => boolean;
        }
    ).shouldCollectTelemetryHistory;

    assert.equal(typeof shouldCollectTelemetryHistory, 'function');
    assert.equal(shouldCollectTelemetryHistory?.('/'), true);
    assert.equal(shouldCollectTelemetryHistory?.('/infra'), true);
    assert.equal(shouldCollectTelemetryHistory?.('/infra/'), true);
    assert.equal(shouldCollectTelemetryHistory?.('/designer'), false);
    assert.equal(shouldCollectTelemetryHistory?.('/submit'), false);
    assert.equal(shouldCollectTelemetryHistory?.('/results'), false);
    assert.equal(shouldCollectTelemetryHistory?.('/ngs'), false);
});

test('live telemetry uses a bounded native SVG renderer instead of Plotly', () => {
    const telemetrySource = readFileSync('src/components/InfraLiveTelemetry.tsx', 'utf8');
    assert.doesNotMatch(telemetrySource, /react-plotly\.js|plotly\.js/);
    assert.match(telemetrySource, /function TimeSeriesPlot[\s\S]*?<svg/);
    assert.match(telemetrySource, /subscribeSharedTelemetryCollectorState/);
    assert.match(telemetrySource, /addEventListener\('storage', sharedTelemetryCollectorStorageHandler\)/);
    assert.doesNotMatch(telemetrySource, /if \(!payload\) return;[\s\S]{0,300}setSamples/);
});

test('layout wires the route policy into telemetry collector ownership', () => {
    const layoutSource = readFileSync('src/components/Layout.tsx', 'utf8');
    assert.match(
        layoutSource,
        /\{shouldCollectTelemetryHistory\(location\.pathname\) && <InfraTelemetryCollector \/>\}/,
    );
});

test('append-only telemetry updates avoid full-history sorting and persistence is throttled', () => {
    const appendRetainedTelemetrySample = (
        telemetryHistory as unknown as {
            appendRetainedTelemetrySample?: (
                samples: LiveSample[],
                sample: LiveSample,
                nowMs: number,
            ) => LiveSample[];
        }
    ).appendRetainedTelemetrySample;
    const shouldPersistTelemetryHistory = (
        telemetryHistory as unknown as {
            shouldPersistTelemetryHistory?: (lastPersistedAtMs: number, nowMs: number) => boolean;
        }
    ).shouldPersistTelemetryHistory;

    assert.equal(typeof appendRetainedTelemetrySample, 'function');
    assert.equal(typeof shouldPersistTelemetryHistory, 'function');
    if (!appendRetainedTelemetrySample || !shouldPersistTelemetryHistory) {
        throw new Error('telemetry append/persistence helpers are unavailable');
    }

    const first = sample(2_000_000_000_000, 10);
    const second = sample(2_000_000_001_000, 11);
    assert.deepEqual(
        appendRetainedTelemetrySample([first], second, second.timestampMs),
        [first, second],
    );
    assert.equal(shouldPersistTelemetryHistory(1_000, 15_999), false);
    assert.equal(shouldPersistTelemetryHistory(1_000, 16_000), true);
});

test('telemetry retention rejects stale/future samples and caps the newest hour at 4,000 points', () => {
    const nowMs = 2_000_000_000_000;
    const inWindow = Array.from({ length: 4_005 }, (_, index) => sample(nowMs - (4_004 - index) * 100));
    const retained = trimRetainedSamples([
        sample(nowMs - 60 * 60 * 1000 - 1),
        ...inWindow,
        sample(nowMs + 60_001),
    ], nowMs);

    assert.equal(retained.length, 4_000);
    assert.equal(retained[0]?.timestampMs, inWindow[5]?.timestampMs);
    assert.equal(retained.at(-1)?.timestampMs, nowMs);
});

test('telemetry reconciliation merges concurrent histories, sorts, and keeps the newest duplicate', () => {
    const reconciled = reconcileTelemetrySamples(
        [sample(3_000, 30), sample(1_000, 10)],
        [sample(2_000, 20), sample(3_000, 31)],
        sample(4_000, 40),
    );

    assert.deepEqual(reconciled.map((entry) => entry.timestampMs), [1_000, 2_000, 3_000, 4_000]);
    assert.equal(reconciled[2]?.cpuUtil, 31);
    assert.deepEqual(mergeTelemetrySample(reconciled, sample(4_000, 999)), reconciled);

    const durableUnion = reconcileTelemetryHistories(
        [sample(1_000, 10), sample(3_000, 30)],
        [sample(2_000, 20), sample(3_000, 31)],
        4_000,
    );
    assert.deepEqual(durableUnion.map((entry) => entry.timestampMs), [1_000, 2_000, 3_000]);
    assert.equal(durableUnion[2]?.cpuUtil, 31);
});

test('timezone-naive API timestamps are normalized as UTC and repair persisted local timestampMs values', () => {
    const naiveUtc = '2026-07-18T02:53:46.744002';
    const expectedMs = Date.parse(`${naiveUtc}Z`);
    assert.equal(parseTelemetryTimestampMs(naiveUtc), expectedMs);
    assert.equal(parseTelemetryTimestampMs(`${naiveUtc}Z`), expectedMs);

    const values = new Map<string, string>();
    const previousWindow = globalThis.window;
    Object.defineProperty(globalThis, 'window', {
        configurable: true,
        value: {
            localStorage: {
                getItem: (key: string) => values.get(key) ?? null,
                setItem: (key: string, value: string) => values.set(key, value),
            },
        },
    });

    try {
        values.set(STORAGE_KEY, JSON.stringify({
            version: 3,
            pollIntervalMs: 1000,
            windowMinutes: 3,
            samples: [{ ...sample(expectedMs), timestamp: naiveUtc, timestampMs: expectedMs + (5 * 60 * 60 * 1000) }],
        }));
        const restored = loadPersistedTelemetryState(1000, 3, expectedMs + 1_000);
        assert.equal(restored.samples.length, 1);
        assert.equal(restored.samples[0]?.timestampMs, expectedMs);
        assert.equal(restored.samples[0]?.timestamp, new Date(expectedMs).toISOString());
    } finally {
        Object.defineProperty(globalThis, 'window', {
            configurable: true,
            value: previousWindow,
        });
    }
});

test('preference writes preserve samples already written by another mounted consumer', () => {
    const values = new Map<string, string>();
    const previousWindow = globalThis.window;
    Object.defineProperty(globalThis, 'window', {
        configurable: true,
        value: {
            localStorage: {
                getItem: (key: string) => values.get(key) ?? null,
                setItem: (key: string, value: string) => values.set(key, value),
            },
        },
    });

    try {
        const retainedSample = sample(Date.now());
        values.set(STORAGE_KEY, JSON.stringify({
            version: 3,
            pollIntervalMs: 1000,
            windowMinutes: 3,
            samples: [retainedSample],
        }));
        const historyBeforePreferenceWrite = values.get(STORAGE_KEY);

        persistTelemetryPreferences(5000, 60);
        assert.equal(values.get(STORAGE_KEY), historyBeforePreferenceWrite);
        assert.deepEqual(JSON.parse(values.get(PREFERENCES_STORAGE_KEY) ?? '{}'), {
            version: 1,
            pollIntervalMs: 5000,
            windowMinutes: 60,
        });
        const restored = loadPersistedTelemetryState(1000, 3);
        assert.equal(restored.pollIntervalMs, 5000);
        assert.equal(restored.windowMinutes, 60);
        assert.equal(restored.samples.length, 1);
        assert.equal(restored.samples[0]?.timestamp, retainedSample.timestamp);
        assert.equal(restored.samples[0]?.timestampMs, retainedSample.timestampMs);
        assert.equal(restored.samples[0]?.cpuUtil, retainedSample.cpuUtil);
        assert.match(restored.samples[0]?.clock ?? '', /\d{2}:\d{2}:\d{2}/);
    } finally {
        Object.defineProperty(globalThis, 'window', {
            configurable: true,
            value: previousWindow,
        });
    }
});
