import assert from 'node:assert/strict';
import test from 'node:test';
import * as history from '../src/components/infraTelemetryHistory.js';

test('chart countdown owns the actual refresh timer and cleanup prevents a late refresh', (t) => {
    const schedule = (history as unknown as { scheduleTelemetryRefresh?: (
        interval: number, refresh: () => void, countdown: (seconds: number) => void,
    ) => () => void }).scheduleTelemetryRefresh;
    assert.equal(typeof schedule, 'function');
    if (!schedule) return;
    t.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 100_000 });
    let refreshes = 0;
    const seconds: number[] = [];
    const stop = schedule(5_000, () => refreshes++, (value) => seconds.push(value));
    assert.equal(seconds.at(-1), 5);
    t.mock.timers.tick(1_000);
    assert.equal(seconds.at(-1), 4);
    assert.equal(refreshes, 0);
    t.mock.timers.tick(4_000);
    assert.equal(seconds.at(-1), 0);
    assert.equal(refreshes, 1);
    t.mock.timers.tick(30_000);
    assert.equal(refreshes, 1, 'completion must explicitly schedule the next refresh, not a free-running interval');
    stop();
    const cancel = schedule(30_000, () => refreshes++, () => {});
    t.mock.timers.tick(5_000);
    cancel();
    t.mock.timers.tick(30_000);
    assert.equal(refreshes, 1, 'unmount, paused tab and range changes cancel their timer');
});

test('all windows trim cadence padding but preserve missing history and stale edges', () => {
    for (const windowMinutes of [1, 3, 5, 10, 15, 30, 60] as const) {
        const bucket = history.resolveTelemetryBucketIntervalMs(windowMinutes, 1000);
        const nominal = history.resolveTelemetryWindowBounds(4_000_001, windowMinutes, bucket);
        const first = nominal[0] + bucket;
        const last = nominal[1] - bucket;
        const domain = history.resolveTelemetryPlotDomain(nominal, last, true, first, bucket);
        assert.deepEqual(domain, [first, last]);
        assert.equal(history.resolveTelemetryPlotX(first, ...domain), 0);
        assert.equal(history.resolveTelemetryPlotX(last, ...domain), 1000);
        assert.deepEqual(history.resolveTelemetryPlotDomain(nominal, last, false, first, bucket), nominal);
        assert.deepEqual(history.resolveTelemetryPlotDomain(nominal, nominal[1] - 3 * bucket, true, nominal[0] + 3 * bucket, bucket), nominal);
        assert.deepEqual(history.resolveTelemetryPlotDomain(nominal, last, true, last, bucket), nominal);
        assert.deepEqual(history.resolveTelemetryPlotDomain(nominal, undefined, true, undefined, bucket), nominal);
        assert.deepEqual(history.resolveTelemetryPlotDomain(nominal, NaN, true, first, bucket), nominal);
    }
});
