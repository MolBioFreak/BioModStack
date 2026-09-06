import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { TimeSeriesPlot, buildTelemetrySvgPath } from '../src/components/telemetryMetricPlot';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { InfraLiveTelemetry } from '../src/components/InfraLiveTelemetry';

Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const axis = { title: 'Package power', color: '#eab308', suffix: ' W', range: [0, 200] as [number, number] };
const line = { name: 'Package power', axis, x: [1000, 2000, 3000, 4000], y: [150, null, 175, 200], line: { color: '#eab308' } };

test('path preserves raw watts, all vertices and null gaps without extrapolation', () => {
    assert.equal(buildTelemetrySvgPath(line, 1000, 4000, 0, 200), ' M 0.00 25.00 M 666.67 12.50 L 1000.00 0.00');
});

test('mixed metrics mount separate named actual-unit axes and exact-value hover', async () => {
    let root: ReactTestRenderer;
    await act(async () => { root = create(<TimeSeriesPlot height={118} samples={[]} yAxis={axis} series={[line, { ...line, name: 'Frequency', axis: { ...axis, title: 'Frequency', suffix: ' GHz', range: [0, 5] }, y: [3.25, null, 4.1, 4.5] }]} xDomain={[1000, 4000]} />); });
    try {
        const plots = root!.root.findAllByProps({ 'data-bms-telemetry-plot': 'true' });
        assert.equal(plots.length, 2);
        assert.equal(plots[0].props['aria-label'], 'Package power (W) telemetry history');
        const axes = root!.root.findAllByProps({ 'data-bms-telemetry-axis': 'true' });
        assert.match(JSON.stringify(axes[0].children.map(c => typeof c === 'string' ? c : c.children)), /200 W/);
        const hover = root!.root.findAllByProps({ 'data-bms-telemetry-inspector': 'true' })[0];
        await act(async () => hover.props.onKeyDown({ key: 'ArrowRight', preventDefault() {} }));
        assert.match(JSON.stringify(root!.toJSON()), /150 W/);
        assert.match(JSON.stringify(root!.toJSON()), /GHz/);
    } finally { await act(async () => root!.unmount()); }
});


test('mounted CPU, RAM and GPU panels pass raw units and preserve absent GPU readings', async () => {
    // Offline unit fixture, not a preview or claimed device measurement.
    const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false } } });
    const gpu = { index: 0, name: 'Test GPU', utilization: 20, memory_total_mb: 24576, memory_used_mb: 4096,
        reserved_memory_mb: 1024, power_draw_w: 325, power_limit_w: 300, max_power_watts: 350,
        min_power_watts: 100, temperature: 72, processes: [] };
    client.setQueryData(['infra-live-shared'], { data: {
        cpu: { name: 'Test CPU', utilization: 12, frequency_current_mhz: 3250, power_watts: 150, temperature: 55 },
        ram: { total_gb: 32, used_gb: 12, available_gb: 20, utilization: 37.5, swap_percent: 2 },
        gpus: [gpu], timestamp: new Date(4000).toISOString(),
    } });
    const point = { timestamp_ms: 1000, sample_count: 1, cpu_utilization: 12,
        cpu_frequency_current_mhz: 3250, cpu_power_watts: 150, cpu_temperature: 55,
        ram_used_gb: 12, ram_available_gb: 20, ram_utilization: 37.5, ram_swap_percent: 2,
        gpus: [{ index: 0, utilization: 20, vram_gb: 5, power_draw_w: 325, temperature: 72 }] };
    client.setQueryData(['compact-telemetry-chart-history', 1, 1000, 1000], {
        start_ms: 0, end_ms: 4000, generated_at_ms: 4000, next_cursor_ms: 3000, bucket_ms: 1000,
        points: [point, { ...point, timestamp_ms: 2000, gpus: [] }, { ...point, timestamp_ms: 3000 }],
    });
    let root: ReactTestRenderer;
    await act(async () => { root = create(<QueryClientProvider client={client}><InfraLiveTelemetry defaultWindowMinutes={1} /></QueryClientProvider>); });
    try {
        const plots = root!.root.findAllByType(TimeSeriesPlot);
        const lines = plots.flatMap(p => p.props.series);
        const byTitle = Object.fromEntries(lines.map(line => [line.axis.title, line]));
        assert.deepEqual(byTitle['Frequency'].y, [3.25, 3.25, 3.25]);
        assert.deepEqual(byTitle['Package power'].y, [150, 150, 150]);
        assert.deepEqual(byTitle['Used memory'].y, [12, 12, 12]);
        assert.deepEqual(byTitle['Available memory'].y, [20, 20, 20]);
        assert.deepEqual(byTitle['VRAM used + reserved'].y, [5, null, 5]);
        assert.deepEqual(byTitle['Power draw'].y, [325, null, 325], 'draw above the current cap is not clipped');
        assert.equal(root!.root.findAllByProps({ 'data-bms-telemetry-plot': 'true' }).length, 12);
        assert.deepEqual(lines.map(line => line.axis.suffix.trim()), ['%', 'GHz', 'W', '°C', 'GB', 'GB', '%', '%', '%', 'GB', 'W', '°C']);
    } finally { await act(async () => root!.unmount()); client.clear(); }
});
