import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import * as telemetryHistory from '../src/components/infraTelemetryHistory.js';
import {
    isTelemetryHistoryFresh,
    loadPersistedTelemetryPreferences,
    parseTelemetryTimestampMs,
    persistTelemetryPreferences,
    resampleTelemetrySamples,
    resolveTelemetryBucketIntervalMs,
    resolveTelemetryDisplayIntervalMs,
    resolveTelemetryGapBreakMs,
    resolveTelemetryNominalDomain,
    resolveTelemetryPlotDomain,
    resolveTelemetryWindowBounds,
} from '../src/components/infraTelemetryHistory.js';
import type { LiveSample } from '../src/components/infraTelemetryHistory.js';

const PREFERENCES_STORAGE_KEY = 'bms_infra_live_telemetry_preferences_v1';

test('viewer reads bounded server-owned telemetry history without browser history writes', () => {
    const telemetrySource = readFileSync('src/components/InfraLiveTelemetry.tsx', 'utf8');
    const historySource = readFileSync('src/components/infraTelemetryHistory.ts', 'utf8');
    assert.doesNotMatch(telemetrySource, /react-plotly\.js|plotly\.js/);
    assert.match(telemetrySource, /function TimeSeriesPlot[\s\S]*?<svg/);
    assert.match(telemetrySource, /fetchTelemetryHistory\(stableStartMs, requestEndMs, 'raw', 4000\)/);
    assert.match(telemetrySource, /resampleTelemetrySamples\(rawSamples, bucketIntervalMs\)/);
    assert.match(telemetrySource, /placeholderData: \(previousData\)/);
    assert.doesNotMatch(telemetrySource, /'minute'|mergeMinuteHistoryWithRawTail|downsampleTelemetryTail|MINUTE_LIVE_TAIL_MS/);
    assert.match(telemetrySource, /refetchInterval: displayIntervalMs/);
    assert.match(telemetrySource, /const liveStatusQuery = useQuery\(\{[\s\S]*?queryKey: INFRA_LIVE_SHARED_QUERY_KEY/);
    assert.match(telemetrySource, /queryFn: fetchSystemStatus,[\s\S]*?refetchInterval: pollIntervalMs/);
    assert.match(telemetrySource, /const payload = liveStatusQuery\.data\?\.data \?\? latestPoint\?\.payload/);
    assert.match(telemetrySource, /resolveTelemetryFreshnessObservedAtMs\([\s\S]*?liveStatusQuery\.data\?\.data\.timestamp/);
    assert.match(telemetrySource, /resolveTelemetryFreshnessObservedAtMs\([\s\S]*?historyQuery\.errorUpdatedAt[\s\S]*?liveStatusQuery\.errorUpdatedAt/);
    assert.match(telemetrySource, /historyQuery\.isError \|\| liveStatusQuery\.isError/);
    assert.match(telemetrySource, /resolveTelemetryNominalDomain\([\s\S]*?freshnessObservedAtMs,[\s\S]*?historyQuery\.isError \|\| historyIsStale/);
    assert.match(telemetrySource, /refetchOnWindowFocus: false/);
    assert.match(telemetrySource, /buildSample\(point\.payload, 1000, point\.timestamp_ms\)/);
    assert.match(telemetrySource, /Telemetry collection is stale/);
    assert.match(telemetrySource, /const xMin = xDomain\?\.\[0\]/);
    assert.match(telemetrySource, /const xMax = xDomain\?\.\[1\]/);
    assert.match(telemetrySource, /persistTelemetryPreferences/);
    assert.doesNotMatch(telemetrySource, /persistTelemetryState|appendRetainedTelemetrySample|subscribeSharedTelemetryCollectorState/);
    assert.doesNotMatch(historySource, /bms_infra_live_telemetry_v1|samples: LiveSample\[\]|persistTelemetryState/);
});

test('telemetry windows use stable range-aware display and bucket cadences', () => {
    assert.equal(resolveTelemetryDisplayIntervalMs(1, 1000), 1000);
    assert.equal(resolveTelemetryDisplayIntervalMs(10, 1000), 5_000);
    assert.equal(resolveTelemetryDisplayIntervalMs(15, 1000), 10_000);
    assert.equal(resolveTelemetryDisplayIntervalMs(30, 1000), 15_000);
    assert.equal(resolveTelemetryDisplayIntervalMs(60, 1000), 30_000);
    assert.deepEqual(
        ([1, 3, 5, 10, 15, 30, 60] as const).map(resolveTelemetryBucketIntervalMs),
        [1_000, 2_000, 3_000, 5_000, 10_000, 15_000, 30_000],
    );

    const first = resolveTelemetryWindowBounds(125_000, 60, 30_000);
    const sameBucket = resolveTelemetryWindowBounds(149_999, 60, 30_000);
    const nextBucket = resolveTelemetryWindowBounds(150_001, 60, 30_000);
    assert.deepEqual(first, sameBucket, 'the 1h domain must not translate between 30s cadence boundaries');
    assert.equal(nextBucket[1] - first[1], 30_000, 'the 1h domain must advance by one complete cadence step');
    assert.equal(first[1] - first[0], 60 * 60_000, 'the selected window width must remain exact');
});

test('fresh plotted telemetry reaches the right edge while stale telemetry preserves wall-clock lag', () => {
    const nominalDomain: [number, number] = [60_000, 120_000];
    const staleThresholdResolver = (
        telemetryHistory as typeof telemetryHistory & {
            resolveTelemetryStaleAfterMs?: (pollIntervalMs: number) => number;
        }
    ).resolveTelemetryStaleAfterMs;

    assert.equal(typeof staleThresholdResolver, 'function', 'telemetry freshness needs one bounded resolver');
    if (!staleThresholdResolver) return;

    assert.equal(staleThresholdResolver(1_000), 10_000);
    assert.equal(staleThresholdResolver(2_000), 10_000);
    assert.equal(staleThresholdResolver(5_000), 15_000);
    assert.equal(
        isTelemetryHistoryFresh(100_000, 120_000, staleThresholdResolver(5_000), false),
        false,
        'the slow poll preset must not classify API-stale telemetry as frontend-fresh',
    );

    assert.equal(isTelemetryHistoryFresh(119_000, 120_000, 10_000, false), true);
    assert.equal(isTelemetryHistoryFresh(119_000, 120_000, 10_000, true), false);
    assert.equal(isTelemetryHistoryFresh(109_999, 120_000, 10_000, false), false);
    assert.equal(isTelemetryHistoryFresh(undefined, 120_000, 10_000, false), false);

    assert.deepEqual(
        resolveTelemetryPlotDomain(nominalDomain, 119_000, true),
        [59_000, 119_000],
        'a fresh plotted bucket must own the right edge without changing the selected window width',
    );
    assert.deepEqual(
        resolveTelemetryPlotDomain(nominalDomain, 119_000, false),
        nominalDomain,
        'a stale series must retain its truthful wall-clock gap',
    );
    assert.deepEqual(
        resolveTelemetryPlotDomain(nominalDomain, 121_000, true),
        nominalDomain,
        'a future sample must not expand the wall-clock domain',
    );
});

test('live status ages telemetry freshness between slow history fetches', () => {
    const observedAtResolver = (
        telemetryHistory as typeof telemetryHistory & {
            resolveTelemetryFreshnessObservedAtMs?: (...observedAtMs: number[]) => number;
        }
    ).resolveTelemetryFreshnessObservedAtMs;
    const historyGeneratedAtMs = 120_000;
    const liveStatusObservedAtMs = 140_000;
    const latestSampleAtMs = 119_000;

    assert.equal(typeof observedAtResolver, 'function', 'freshness needs an advancing observation clock');
    if (!observedAtResolver) return;
    const observedAtMs = observedAtResolver(
        historyGeneratedAtMs,
        liveStatusObservedAtMs,
    );

    assert.equal(observedAtMs, liveStatusObservedAtMs);
    assert.equal(
        isTelemetryHistoryFresh(latestSampleAtMs, observedAtMs, 15_000, false),
        false,
        'a live-status poll must reveal staleness before a 30-second history refetch',
    );
    assert.equal(
        observedAtResolver(historyGeneratedAtMs, 110_000),
        historyGeneratedAtMs,
        'an older live-status response must not move the observation clock backward',
    );
    assert.equal(
        observedAtResolver(historyGeneratedAtMs, Number.NaN),
        historyGeneratedAtMs,
        'an invalid live-status timestamp must retain the valid history clock',
    );
    const simultaneousFailureAtMs = 170_000;
    const failureObservedAtMs = observedAtResolver(
        historyGeneratedAtMs,
        liveStatusObservedAtMs,
        simultaneousFailureAtMs,
    );
    assert.equal(
        failureObservedAtMs,
        simultaneousFailureAtMs,
        'request failure timestamps must keep the stale observation clock moving',
    );
    assert.deepEqual(
        resolveTelemetryNominalDomain(60_000, 120_000, failureObservedAtMs, 1, 10_000, true),
        [110_000, 170_000],
    );
});

test('failed history advances the nominal domain from the failed request time', () => {
    assert.deepEqual(
        resolveTelemetryNominalDomain(60_000, 120_000, 121_500, 1, 1_000, false),
        [60_000, 120_000],
        'a successful response must retain its server-owned bounds',
    );
    assert.deepEqual(
        resolveTelemetryNominalDomain(60_000, 120_000, 121_500, 1, 1_000, true),
        [62_000, 122_000],
        'a failed refetch must advance the wall-clock domain so stale lag remains visible',
    );
});

test('raw telemetry is averaged into aligned buckets with an in-place partial endpoint', () => {
    const sample = (timestampMs: number, value: number): LiveSample => ({
        timestamp: new Date(timestampMs).toISOString(),
        timestampMs,
        pollIntervalMs: 1000,
        clock: '',
        cpuUtil: value,
        cpuFreqMhz: value * 100,
        cpuPower: value,
        cpuTemp: value,
        ramUsed: value,
        ramFree: value,
        ramUtil: value,
        ramSwap: value,
        gpu: { 0: { util: value, vram: value, power: value, temp: value } },
    });
    const averaged = resampleTelemetrySamples(
        [sample(1_000, 2), sample(4_000, 4), sample(5_000, 10), sample(9_000, 14), sample(10_000, 20)],
        5_000,
    );
    assert.deepEqual(averaged.map((point) => point.timestampMs), [0, 5_000, 10_000]);
    assert.deepEqual(averaged.map((point) => point.cpuUtil), [3, 12, 20]);
    assert.deepEqual(averaged.map((point) => point.gpu[0]?.util), [3, 12, 20]);
    assert.equal(averaged.at(-1)?.timestampMs, 10_000, 'the partial endpoint must develop at its fixed bucket timestamp');
    assert.ok(resolveTelemetryGapBreakMs(30_000, 1000) >= 30_000, 'adjacent 1h buckets must connect');
    assert.ok(resolveTelemetryGapBreakMs(30_000, 1000) < 60_000, 'a missing 1h bucket must remain a visible gap');
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
