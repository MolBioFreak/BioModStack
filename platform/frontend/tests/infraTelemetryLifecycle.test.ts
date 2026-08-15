import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    downsampleTelemetryTail,
    loadPersistedTelemetryPreferences,
    mergeMinuteHistoryWithRawTail,
    parseTelemetryTimestampMs,
    persistTelemetryPreferences,
    resolveTelemetryDisplayIntervalMs,
    resolveTelemetryGapBreakMs,
    resolveTelemetryWindowBounds,
} from '../src/components/infraTelemetryHistory.js';

const PREFERENCES_STORAGE_KEY = 'bms_infra_live_telemetry_preferences_v1';

test('viewer reads bounded server-owned telemetry history without browser history writes', () => {
    const telemetrySource = readFileSync('src/components/InfraLiveTelemetry.tsx', 'utf8');
    const historySource = readFileSync('src/components/infraTelemetryHistory.ts', 'utf8');
    assert.doesNotMatch(telemetrySource, /react-plotly\.js|plotly\.js/);
    assert.match(telemetrySource, /function TimeSeriesPlot[\s\S]*?<svg/);
    assert.match(telemetrySource, /const resolution = windowMinutes >= 10 \? 'minute' : 'raw'/);
    assert.match(telemetrySource, /resolveTelemetryGapBreakMs\(resolution, pollIntervalMs\)/);
    assert.match(telemetrySource, /fetchTelemetryHistory\(startMs, requestEndMs, 'minute', 4000\)/);
    assert.match(telemetrySource, /fetchTelemetryHistory\(rawTailStartMs, requestEndMs, 'raw', 4000\)/);
    assert.match(telemetrySource, /downsampleTelemetryTail\(rawTail\.data\.points, displayIntervalMs\)/);
    assert.match(telemetrySource, /mergeMinuteHistoryWithRawTail\(minuteHistory\.data\.points, rawTailPoints\)/);
    assert.match(telemetrySource, /refetchInterval: displayIntervalMs/);
    assert.match(telemetrySource, /refetchOnWindowFocus: !usesRangeAwareDisplay/);
    assert.match(telemetrySource, /buildSample\(point\.payload, 1000, point\.timestamp_ms\)/);
    assert.match(telemetrySource, /Telemetry collection is stale/);
    assert.match(telemetrySource, /const xMin = xDomain\?\.\[0\]/);
    assert.match(telemetrySource, /const xMax = xDomain\?\.\[1\]/);
    assert.match(telemetrySource, /persistTelemetryPreferences/);
    assert.doesNotMatch(telemetrySource, /persistTelemetryState|appendRetainedTelemetrySample|subscribeSharedTelemetryCollectorState/);
    assert.doesNotMatch(historySource, /bms_infra_live_telemetry_v1|samples: LiveSample\[\]|persistTelemetryState/);
});

test('minute telemetry joins a raw live tail and preserves missing-bucket gaps', () => {
    const gapBreakMs = resolveTelemetryGapBreakMs('minute', 1000);
    assert.ok(gapBreakMs >= 60_000, 'adjacent minute buckets must remain connected');
    assert.ok(gapBreakMs < 120_000, 'a missing minute bucket must preserve a visible gap');

    const merged = mergeMinuteHistoryWithRawTail(
        [{ timestamp_ms: 0 }, { timestamp_ms: 60_000 }, { timestamp_ms: 120_000 }],
        [{ timestamp_ms: 120_000 }, { timestamp_ms: 179_000 }, { timestamp_ms: 180_000 }, { timestamp_ms: 181_000 }],
    );
    assert.deepEqual(
        merged.map((point) => point.timestamp_ms),
        [0, 60_000, 120_000, 180_000, 181_000],
        'raw samples must replace only the still-open minute tail',
    );
});

test('long telemetry windows use stable range-aware display cadence', () => {
    assert.equal(resolveTelemetryDisplayIntervalMs(1, 1000), 1000);
    assert.equal(resolveTelemetryDisplayIntervalMs(10, 5000), 5000);
    assert.equal(resolveTelemetryDisplayIntervalMs(15, 1000), 15_000);
    assert.equal(resolveTelemetryDisplayIntervalMs(30, 1000), 30_000);
    assert.equal(resolveTelemetryDisplayIntervalMs(60, 1000), 60_000);

    const first = resolveTelemetryWindowBounds(125_000, 60, 60_000);
    const sameBucket = resolveTelemetryWindowBounds(179_999, 60, 60_000);
    const nextBucket = resolveTelemetryWindowBounds(180_001, 60, 60_000);
    assert.deepEqual(first, sameBucket, 'the 1h domain must not translate between minute cadence boundaries');
    assert.equal(nextBucket[1] - first[1], 60_000, 'the 1h domain must advance by one complete cadence step');
    assert.equal(first[1] - first[0], 60 * 60_000, 'the selected window width must remain exact');
});

test('long telemetry windows keep a sparse fresh raw tail', () => {
    const rawTail = [1_000, 14_000, 15_000, 29_000, 30_000, 44_000, 59_000].map((timestamp_ms) => ({ timestamp_ms }));
    assert.deepEqual(
        downsampleTelemetryTail(rawTail, 30_000).map((point) => point.timestamp_ms),
        [29_000, 59_000],
        '30m display must keep only the newest raw sample in each 30s display bucket',
    );
    assert.deepEqual(
        downsampleTelemetryTail(rawTail, 60_000).map((point) => point.timestamp_ms),
        [59_000],
        '1h display must retain the newest live sample without rendering one-second tail noise',
    );
});

test('layout has no browser telemetry collector mount', () => {
    const layoutSource = readFileSync('src/components/Layout.tsx', 'utf8');
    assert.doesNotMatch(layoutSource, /InfraTelemetryCollector|shouldCollectTelemetryHistory/);
});

test('timezone-naive API timestamps are normalized as UTC', () => {
    const naiveUtc = '2026-07-18T02:53:46.744002';
    const expectedMs = Date.parse(`${naiveUtc}Z`);
    assert.equal(parseTelemetryTimestampMs(naiveUtc), expectedMs);
    assert.equal(parseTelemetryTimestampMs(`${naiveUtc}Z`), expectedMs);
});

test('preference storage persists only poll and window selections', () => {
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
        persistTelemetryPreferences(5000, 60);
        const restored = loadPersistedTelemetryPreferences(1000, 3);
        assert.equal(restored.pollIntervalMs, 5000);
        assert.equal(restored.windowMinutes, 60);
        assert.deepEqual(
            JSON.parse(values.get(PREFERENCES_STORAGE_KEY) ?? '{}'),
            { version: 1, pollIntervalMs: 5000, windowMinutes: 60 },
        );
        assert.equal(values.size, 1);
    } finally {
        if (previousWindow === undefined) {
            Reflect.deleteProperty(globalThis, 'window');
        } else {
            Object.defineProperty(globalThis, 'window', { configurable: true, value: previousWindow });
        }
    }
});
