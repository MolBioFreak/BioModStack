import assert from 'node:assert/strict';
import test from 'node:test';

import { assessMeasurement, type ViewerMeasurement } from '../src/structureViewer/contracts/measurements.js';

const atom = (atomName: string) => ({
    documentId: 'doc-1',
    labelAsymId: 'A',
    labelSeqId: 1,
    labelAtomId: atomName,
});

test('distance, angle, and dihedral measurements require exact canonical atoms', () => {
    const measurements: ViewerMeasurement[] = [
        { measurementId: 'd', type: 'distance', points: [atom('N'), atom('CA')], provenanceRef: 'user:v1' },
        { measurementId: 'a', type: 'angle', points: [atom('N'), atom('CA'), atom('C')], provenanceRef: 'user:v1' },
        { measurementId: 't', type: 'dihedral', points: [atom('N'), atom('CA'), atom('C'), atom('O')], provenanceRef: 'user:v1' },
    ];
    for (const measurement of measurements) assert.equal(assessMeasurement(measurement).status, 'ok');
});

test('residue-wide and provenance-free geometry fails closed', () => {
    const missingAtom = assessMeasurement({
        measurementId: 'bad',
        type: 'distance',
        points: [atom('N'), { documentId: 'doc-1', labelAsymId: 'A', labelSeqId: 2 }],
        provenanceRef: 'user:v1',
    });
    assert.equal(missingAtom.status, 'ambiguous');

    const missingProvenance = assessMeasurement({
        measurementId: 'bad-2',
        type: 'distance',
        points: [atom('N'), atom('CA')],
        provenanceRef: '',
    });
    assert.equal(missingProvenance.status, 'unsupported');
});
