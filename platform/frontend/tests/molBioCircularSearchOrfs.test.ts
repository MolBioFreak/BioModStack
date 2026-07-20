import assert from 'node:assert/strict';
import test from 'node:test';

import {
    findOpenReadingFrames,
    reverseComplementCodingSequence,
} from '../src/components/MolBioToolkit/utils/orfs.js';
import { findExactSequenceMatches } from '../src/components/MolBioToolkit/utils/search.js';

const forwardWrappingOrfSequence = `AAATAA${'C'.repeat(9)}ATG`;

test('circular ORF scan finds a forward start-to-stop path across the origin with bounded segments', () => {
    const circular = findOpenReadingFrames(forwardWrappingOrfSequence, 9, true);
    const wrapped = circular.find((orf) => (
        orf.strand === 1
        && orf.segments.length === 2
        && orf.segments[0].start === 15
    ));
    assert.ok(wrapped);
    assert.equal(wrapped.length, 9);
    assert.deepEqual(wrapped.segments, [
        { start: 15, end: 18 },
        { start: 0, end: 6 },
    ]);
    assert.equal(
        findOpenReadingFrames(forwardWrappingOrfSequence, 9, false).some((orf) => orf.strand === 1),
        false,
    );
});

test('circular ORF scan maps a reverse-strand origin path back to ordered bounded source segments', () => {
    const reverseTemplate = reverseComplementCodingSequence(forwardWrappingOrfSequence);
    const circular = findOpenReadingFrames(reverseTemplate, 9, true);
    const wrapped = circular.find((orf) => orf.strand === -1 && orf.segments.length === 2);
    assert.ok(wrapped);
    assert.equal(wrapped.length, 9);
    assert.deepEqual(wrapped.segments, [
        { start: 0, end: 3 },
        { start: 12, end: 18 },
    ]);
    assert.equal(
        findOpenReadingFrames(reverseTemplate, 9, false).some((orf) => orf.strand === -1),
        false,
    );
});

test('exact circular search returns bounded ordered segments and biological sequence across origin', () => {
    assert.deepEqual(findExactSequenceMatches('AAAACCCC', 'CCAAAA', {
        circular: true,
        bothStrands: false,
    }), [{
        start: 6,
        end: 4,
        strand: 1,
        sequence: 'CCAAAA',
        segments: [
            { start: 6, end: 8 },
            { start: 0, end: 4 },
        ],
    }]);
});

test('exact circular search finds reverse-only origin hits', () => {
    assert.deepEqual(findExactSequenceMatches('TTCCCCGC', 'AAGC', {
        circular: true,
        bothStrands: true,
    }), [{
        start: 6,
        end: 2,
        strand: -1,
        sequence: 'GCTT',
        segments: [
            { start: 6, end: 8 },
            { start: 0, end: 2 },
        ],
    }]);
});

test('palindromic query is not duplicated as a reverse-strand hit at the same position', () => {
    const matches = findExactSequenceMatches('ATATGGGG', 'ATAT', {
        circular: true,
        bothStrands: true,
    });
    assert.equal(matches.length, 1);
    assert.equal(matches[0].strand, 1);
});
