import assert from 'node:assert/strict';
import test from 'node:test';

function response(
    points: Array<{ timestamp_ms: number; cpu_utilization: number }>,
    nextCursorMs: number,
    effectiveStartMs: number,
) {
    return {
        source: 'immutable_server_telemetry' as const,
        database: 'dedicated_telemetry_store' as const,
        resolution: 'server_bucketed_raw' as const,
        start_ms: 0,
        end_ms: 10_000,
        effective_start_ms: effectiveStartMs,
        bucket_ms: 2_000,
        generated_at_ms: 10_000,
        next_cursor_ms: nextCursorMs,
        points: points.map((point) => ({
            timestamp_ms: point.timestamp_ms,
            sample_count: 1,
            cpu_utilization: point.cpu_utilization,
            cpu_frequency_current_mhz: 1000,
            cpu_power_watts: 10,
            cpu_temperature: 40,
            ram_used_gb: 8,
            ram_available_gb: 24,
            ram_utilization: 25,
            ram_swap_percent: 0,
            gpus: [],
        })),
    };
}

test('incremental chart merge replaces overlap buckets and evicts expired points', async () => {
    const module = await import('../src/lib/telemetryChart.js').catch(() => null);
    assert.ok(module, 'the compact telemetry chart contract must exist');
    if (!module) return;

    const previous = response([
        { timestamp_ms: 0, cpu_utilization: 1 },
        { timestamp_ms: 2_000, cpu_utilization: 2 },
        { timestamp_ms: 4_000, cpu_utilization: 4 },
    ], 4_500, 0);
    const delta = response([
        { timestamp_ms: 4_000, cpu_utilization: 40 },
        { timestamp_ms: 6_000, cpu_utilization: 6 },
    ], 6_500, 4_000);

    const merged = module.mergeTelemetryChartHistory(previous, delta, 2_000, 8_000);
    assert.deepEqual(merged.points.map((point) => point.timestamp_ms), [2_000, 4_000, 6_000]);
    assert.deepEqual(merged.points.map((point) => point.cpu_utilization), [2, 40, 6]);
    assert.equal(merged.start_ms, 2_000);
    assert.equal(merged.end_ms, 8_000);
    assert.equal(merged.next_cursor_ms, 6_500);
});

test('initial merge retains the aligned leading bucket for domain clipping', async () => {
    const module = await import('../src/lib/telemetryChart.js').catch(() => null);
    assert.ok(module, 'the compact telemetry chart contract must exist');
    if (!module) return;

    const initial = response([
        { timestamp_ms: 0, cpu_utilization: 1 },
        { timestamp_ms: 2_000, cpu_utilization: 2 },
    ], 2_500, 0);
    const merged = module.mergeTelemetryChartHistory(undefined, initial, 1_500, 4_000);

    assert.deepEqual(merged.points.map((point) => point.timestamp_ms), [0, 2_000]);
    assert.equal(merged.start_ms, 1_500);
});

test('chart cursor is used only for the same bucket geometry and current window', async () => {
    const module = await import('../src/lib/telemetryChart.js').catch(() => null);
    assert.ok(module, 'the compact telemetry chart contract must exist');
    if (!module) return;

    const previous = response([{ timestamp_ms: 4_000, cpu_utilization: 4 }], 4_500, 0);
    assert.equal(module.resolveTelemetryChartCursor(previous, 2_000, 8_000, 2_000), 4_500);
    assert.equal(module.resolveTelemetryChartCursor(previous, 5_000, 8_000, 2_000), null);
    assert.equal(module.resolveTelemetryChartCursor(previous, 2_000, 8_000, 1_000), null);
});
