import assert from 'node:assert/strict';
import test from 'node:test';

import {
    applyDeleteEdit,
    applyInsertEdit,
    applyTransformEdit,
} from '../src/components/MolBioToolkit/utils/sequenceEdits.js';
import type { SequenceEditData } from '../src/components/MolBioToolkit/utils/sequenceEdits.js';

function circularSequence(): SequenceEditData {
    return {
        name: 'circular-edit-fixture',
        sequence: 'AACCGGTTAA',
        circular: true,
        sequenceType: 'dna',
        features: [],
        primers: [{
            id: 'wrap-primer',
            name: 'wrap-primer',
            sequence: 'AAAACC',
            sequenceType: 'dna',
            start: 8,
            end: 2,
            strand: 1,
            sites: [
                { start: 8, end: 10, strand: 1 },
                { start: 0, end: 2, strand: 1 },
            ],
        }],
        translations: [],
        analysisTracks: [],
    };
}

test('insertion remaps every split primer site and preserves wrapped top-level placement', () => {
    const edited = applyInsertEdit(circularSequence(), 0, 'GG');
    assert.equal(edited.sequence, 'GGAACCGGTTAA');
    assert.deepEqual(edited.primers?.[0], {
        ...circularSequence().primers?.[0],
        start: 10,
        end: 4,
        sites: [
            { start: 10, end: 12, strand: 1 },
            { start: 2, end: 4, strand: 1 },
        ],
    });
});

test('deletion remaps every split primer site and recomputes wrapped top-level placement', () => {
    const edited = applyDeleteEdit(circularSequence(), 3, 5);
    assert.equal(edited.sequence, 'AACGTTAA');
    assert.deepEqual(edited.primers?.[0], {
        ...circularSequence().primers?.[0],
        start: 6,
        end: 2,
        sites: [
            { start: 6, end: 8, strand: 1 },
            { start: 0, end: 2, strand: 1 },
        ],
    });
});

test('deleting one split site drops it and derives placement from the retained site', () => {
    const edited = applyDeleteEdit(circularSequence(), 8, 10);
    assert.equal(edited.sequence, 'AACCGGTT');
    assert.deepEqual(edited.primers?.[0], {
        ...circularSequence().primers?.[0],
        start: 0,
        end: 2,
        sites: [{ start: 0, end: 2, strand: 1 }],
    });
});

test('selection transform remaps split primer sites and their strand together', () => {
    const sequenceData: SequenceEditData = {
        ...circularSequence(),
        circular: false,
        primers: [{
            id: 'compound-primer',
            name: 'compound-primer',
            sequence: 'AACCGG',
            sequenceType: 'dna',
            start: 2,
            end: 8,
            strand: 1,
            sites: [
                { start: 2, end: 4, strand: 1 },
                { start: 6, end: 8, strand: 1 },
            ],
        }],
    };
    const edited = applyTransformEdit(sequenceData, 2, 8, 'reverse');
    assert.deepEqual(edited.primers?.[0], {
        ...sequenceData.primers?.[0],
        sequence: 'TTGGCC',
        start: 6,
        end: 4,
        strand: -1,
        sites: [
            { start: 6, end: 8, strand: -1 },
            { start: 2, end: 4, strand: -1 },
        ],
    });
});
