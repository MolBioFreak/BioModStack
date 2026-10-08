import type { AtomRef } from './structureIdentity.js';
import { assessResidueRef } from './structureIdentity.js';
import {
    viewerAmbiguous,
    viewerOk,
    viewerUnsupported,
    type ViewerResult,
} from './viewerResults.js';

interface ViewerMeasurementBase {
    readonly measurementId: string;
    readonly label?: string;
    readonly provenanceRef: string;
}

export interface ViewerDistanceMeasurement extends ViewerMeasurementBase {
    readonly type: 'distance';
    readonly points: readonly [AtomRef, AtomRef];
}

export interface ViewerAngleMeasurement extends ViewerMeasurementBase {
    readonly type: 'angle';
    readonly points: readonly [AtomRef, AtomRef, AtomRef];
}

export interface ViewerDihedralMeasurement extends ViewerMeasurementBase {
    readonly type: 'dihedral';
    readonly points: readonly [AtomRef, AtomRef, AtomRef, AtomRef];
}

export type ViewerMeasurement =
    | ViewerDistanceMeasurement
    | ViewerAngleMeasurement
    | ViewerDihedralMeasurement;

const hasText = (value: string | undefined): boolean => Boolean(value?.trim());

export const assessMeasurement = (measurement: ViewerMeasurement): ViewerResult<ViewerMeasurement> => {
    if (!hasText(measurement.measurementId)) {
        return viewerUnsupported('Measurement identity requires measurementId', 'measurements');
    }
    if (!hasText(measurement.provenanceRef)) {
        return viewerUnsupported('Measurement geometry requires provenanceRef', 'measurement-provenance');
    }
    for (const point of measurement.points) {
        const residue = assessResidueRef(point);
        if (residue.status !== 'ok') return residue;
        const exactLabelAtom = hasText(point.labelAtomId)
            && hasText(point.labelAsymId)
            && Number.isInteger(point.labelSeqId);
        const exactAuthorAtom = hasText(point.authAtomId)
            && hasText(point.authAsymId)
            && Number.isInteger(point.authSeqId);
        if (!exactLabelAtom && !exactAuthorAtom) {
            return viewerAmbiguous('Measurement points require one complete label or author atom namespace');
        }
    }
    return viewerOk(measurement);
};
