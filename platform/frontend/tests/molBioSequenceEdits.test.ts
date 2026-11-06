import assert from 'node:assert/strict';
import test from 'node:test';

import {
    applyDeleteEdit, applyInsertEdit, applyReplaceEdit, applyTransformEdit,
    type SequenceEditData,
} from '../src/components/MolBioToolkit/utils/sequenceEdits.js';
import { getPrimerRenderableSites, normalizeStoredPrimerPlacement } from '../src/components/MolBioToolkit/utils/selectionActions.js';
import { buildPrimersTsv } from '../src/components/MolBioToolkit/utils/exportData.js';

function circularSequence(): SequenceEditData {
    return {
        name: 'circular-edit-fixture', sequence: 'AACCGGTTAA', circular: true,
        sequenceType: 'dna', features: [], primers: [{
            id: 'wrap-primer', name: 'wrap-primer', sequence: 'AAAA', sequenceType: 'dna',
            start: 8, end: 2, strand: 1,
            sites: [{ start: 8, end: 10, strand: 1 }, { start: 0, end: 2, strand: 1 }],
        }], translations: [], analysisTracks: [],
    };
}
function linearSequence(): SequenceEditData {
    return {
        sequence: 'TTTTACGTTGCAAGTCCATGGTACGGGG', circular: false,
        sequenceType: 'dna', features: [], primers: [{
            id: 'p', name: 'ordered primer', sequence: 'ACGTTGCAAGTCCATGGTAC',
            start: 4, end: 24, strand: 1, tm: 62.03,
        }],
    };
}
function assertUnplaced(data: SequenceEditData, oligo: string) {
    const primer = data.primers![0];
    assert.equal(primer.sequence, oligo, 'an edit must not silently change an ordered oligo');
    assert.equal(primer.binding_status, 'needs_review');
    assert.ok(primer.binding_warning);
    assert.equal(primer.start, 0);
    assert.equal(primer.end, 0);
    assert.deepEqual(primer.sites, []);
    assert.equal(primer.tm, undefined);
    assert.deepEqual(getPrimerRenderableSites(primer, data.sequence.length, Boolean(data.circular)), []);
    return primer;
}

test('insertion through circular origin invalidates the spanning footprint', () => {
    const edited = applyInsertEdit(circularSequence(), 0, 'GG');
    assert.equal(edited.sequence, 'GGAACCGGTTAA');
    assertUnplaced(edited, 'AAAA');
    assertUnplaced(applyInsertEdit(circularSequence(), 10, 'GG'), 'AAAA');
});

test('edits outside a circular binding preserve its actual ordered source intervals', () => {
    const edited = applyDeleteEdit(circularSequence(), 3, 5);
    assert.equal(edited.sequence, 'AACGTTAA');
    assert.deepEqual(edited.primers![0], {
        ...circularSequence().primers![0], start: 6, end: 2,
        sites: [{ start: 6, end: 8, strand: 1 }, { start: 0, end: 2, strand: 1 }],
    });
});

test('deleting any part or all of a footprint keeps the oligo as unplaced', () => {
    assertUnplaced(applyDeleteEdit(circularSequence(), 8, 10), 'AAAA');
    assertUnplaced(applyDeleteEdit(linearSequence(), 4, 24), 'ACGTTGCAAGTCCATGGTAC');
});

test('insertions and 3-prime substitutions invalidate binding and stale annealing Tm', () => {
    const source = linearSequence();
    assertUnplaced(applyInsertEdit(source, 23, 'GGGG'), source.primers![0].sequence);
    const replaced = applyReplaceEdit(source, 23, 24, 'T');
    const primer = assertUnplaced(replaced, source.primers![0].sequence);
    assert.equal(replaced.sequence, 'TTTTACGTTGCAAGTCCATGGTATGGGG');
    const stored = normalizeStoredPrimerPlacement(primer as unknown as Record<string, unknown>, replaced.sequence.length);
    assert.equal(stored.binding_status, 'needs_review');
    assert.deepEqual(stored.sites, []);
    const rows = buildPrimersTsv(replaced.primers!, 'dna', replaced.sequence.length).split('\n').map(row => row.split('\t'));
    assert.equal(rows[1][1], source.primers![0].sequence);
    assert.deepEqual(rows[1].slice(3,6), ['', '', '']);
    assert.equal(rows[1][7], '');
    assert.equal(rows[1][9], 'needs review');
});

test('outside edits shift legal bindings while identical replacements preserve them', () => {
    const source = linearSequence();
    const shifted = applyInsertEdit(source, 0, 'GG');
    assert.equal(shifted.primers![0].start, 6);
    assert.equal(shifted.primers![0].end, 26);
    assert.equal(shifted.primers![0].binding_status, undefined);
    assert.equal(shifted.sequence.slice(6,26), shifted.primers![0].sequence);
    assert.equal(applyReplaceEdit(source, 23, 24, 'C'), source);
});

test('reverse-complement round trip keeps a circular primer and canonical source order', () => {
    const source = circularSequence();
    const edited = applyTransformEdit(source, 0, 10, 'reverse_complement');
    assert.equal(edited.sequence, 'TTAACCGGTT');
    assert.deepEqual(edited.primers![0], {
        ...source.primers![0], strand: -1,
        sites: [{ start: 8, end: 10, strand: -1 }, { start: 0, end: 2, strand: -1 }],
    });
    const restored = applyTransformEdit(edited, 0, 10, 'reverse_complement');
    assert.equal(restored.sequence, source.sequence);
    assert.deepEqual(restored.primers, source.primers);
});

test('partial reverse complement and single-strand transforms never rewrite the ordered primer', () => {
    const source = linearSequence();
    for (const operation of ['reverse', 'complement'] as const) {
        assertUnplaced(applyTransformEdit(source, 0, source.sequence.length, operation), source.primers![0].sequence);
    }
    assertUnplaced(applyTransformEdit(source, 20, 27, 'reverse_complement'), source.primers![0].sequence);
});

test('physical digest fragments reject unsupported sequence edits', () => {
    const source = { ...linearSequence(), digestFragment: { fragment_id: 'physical' } };
    assert.throws(() => applyInsertEdit(source, 5, 'A'), /physical strand ends/);
    assert.throws(() => applyTransformEdit(source, 0, source.sequence.length, 'reverse_complement'), /physical strand ends/);
});
