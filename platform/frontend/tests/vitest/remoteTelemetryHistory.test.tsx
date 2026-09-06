import { describe, expect, it } from 'vitest';
import { mergeRemoteTelemetry, remoteMetricSeries } from '../../src/components/remoteTelemetryHistory';
import type { RemoteGpuTelemetry, RemoteTelemetrySample } from '../../src/lib/api';

const sample = (sequence: number, ago = 0): RemoteTelemetrySample => ({ sequence, observed_at: new Date(Date.now() - ago).toISOString(), available: true, gpus: [] });
const payload = (history: RemoteTelemetrySample[], reset = false) => ({ source: 'active_vast', available: true, target: { id: 'vast:8' }, gpus: [], history, reset }) as RemoteGpuTelemetry;

describe('central remote history', () => {
    it('merges deltas without duplicates and bounds one hour', () => {
        const merged = mergeRemoteTelemetry(payload([sample(1, 3_700_000), sample(2)]), payload([sample(2), sample(3)]));
        expect(merged.history?.map(s => s.sequence)).toEqual([2, 3]);
        expect(mergeRemoteTelemetry(merged, payload([sample(4)], true)).history?.map(s => s.sequence)).toEqual([4]);
        expect(mergeRemoteTelemetry(merged, { ...payload([]), target: null }).history).toEqual([]);
    });
    it('retains measured values and explicit missing data and breaks long gaps', () => {
        const history = [sample(1, 40_000), sample(2, 30_000), sample(3)];
        history[0].cpu = { utilization: 45 };
        history[2].cpu = { utilization: 65 };
        expect(remoteMetricSeries(history, s => s.cpu?.utilization).y).toEqual([45, null, null, 65]);
    });
});
