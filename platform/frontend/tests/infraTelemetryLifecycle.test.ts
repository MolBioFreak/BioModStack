import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    loadPersistedTelemetryPreferences,
    mergeMinuteHistoryWithRawTail,
    parseTelemetryTimestampMs,
    persistTelemetryPreferences,
    resolveTelemetryGapBreakMs,
} from '../src/components/infraTelemetryHistory.js';

const PREFERENCES_STORAGE_KEY = 'bms_infra_live_telemetry_preferences_v1';

test('viewer reads bounded server-owned telemetry history without browser history writes', () => {
    const telemetrySource = readFileSync('src/components/InfraLiveTelemetry.tsx', 'utf8');
    const historySource = readFileSync('src/components/infraTelemetryHistory.ts', 'utf8');
    assert.doesNotMatch(telemetrySource, /react-plotly\.js|plotly\.js/);
    assert.match(telemetrySource, /function TimeSeriesPlot[\s\S]*?<svg/);
    assert.match(telemetrySource, /const resolution = windowMinutes >= 10 \? 'minute' : 'raw'/);
    assert.match(telemetrySource, /resolveTelemetryGapBreakMs\(resolution, pollIntervalMs\)/);
    assert.match(telemetrySource, /fetchTelemetryHistory\(startMs, endMs, 'minute', 4000\)/);
    assert.match(telemetrySource, /fetchTelemetryHistory\(rawTailStartMs, endMs, 'raw', 4000\)/);
    assert.match(telemetrySource, /mergeMinuteHistoryWithRawTail\(minuteHistory\.data\.points, rawTail\.data\.points\)/);
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
