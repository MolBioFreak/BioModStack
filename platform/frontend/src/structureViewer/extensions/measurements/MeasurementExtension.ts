import { assessMeasurement, type ViewerMeasurement } from '../../contracts/measurements.js';
import { viewerOk, viewerUnsupported, type ViewerResult } from '../../contracts/viewerResults.js';

export class MeasurementExtension {
    private readonly measurements = new Map<string, ViewerMeasurement>();

    reconcile(next: readonly ViewerMeasurement[]): ViewerResult<readonly ViewerMeasurement[]> {
        const ids = new Set<string>();
        for (const measurement of next) {
            if (ids.has(measurement.measurementId)) return viewerUnsupported(`Duplicate measurement ${measurement.measurementId}`, 'measurements');
            ids.add(measurement.measurementId);
            const assessed = assessMeasurement(measurement);
            if (assessed.status !== 'ok') return assessed;
        }
        this.measurements.clear();
        for (const measurement of next) this.measurements.set(measurement.measurementId, measurement);
        return viewerOk([...this.measurements.values()]);
    }

    remove(measurementId: string): boolean { return this.measurements.delete(measurementId); }
    list(): readonly ViewerMeasurement[] { return [...this.measurements.values()]; }
}
