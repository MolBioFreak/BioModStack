import assert from 'node:assert/strict';
import test from 'node:test';

import {
    findOpenReadingFrames,
    reverseComplementCodingSequence,
} from '../src/components/MolBioToolkit/utils/orfs.js';
import { findExactSequenceMatches } from '../src/components/MolBioToolkit/utils/search.js';

const forwardWrappingOrfSequence = `AAATAA${'C'.repeat(9)}ATG`;

test('circular ORF scan finds origin-spanning starts on both strands with first-stop and one-revolution bounds', () => {
    // Independent literal source molecules: ATG is intact, split AT|G, or A|TG.
    // Each has ATG AAA TAA TAG in coding order: TAA, not the later TAG, ends the ORF.
    const fixtures = [
        { sequence: 'AAATAATAGCCCCCCATG', strand: 1, segments: [{ start: 15, end: 18 }, { start: 0, end: 6 }] },
        { sequence: 'GAAATAATAGCCCCCCAT', strand: 1, segments: [{ start: 16, end: 18 }, { start: 0, end: 7 }] },
        { sequence: 'TGAAATAATAGCCCCCCA', strand: 1, segments: [{ start: 17, end: 18 }, { start: 0, end: 8 }] },
        { sequence: 'CATGGGGGGCTATTATTT', strand: -1, segments: [{ start: 0, end: 3 }, { start: 12, end: 18 }] },
        { sequence: 'ATGGGGGGCTATTATTTC', strand: -1, segments: [{ start: 0, end: 2 }, { start: 11, end: 18 }] },
        { sequence: 'TGGGGGGCTATTATTTCA', strand: -1, segments: [{ start: 0, end: 1 }, { start: 10, end: 18 }] },
    ];
    const complement: Record<string, string> = { A: 'T', T: 'A', G: 'C', C: 'G' };
    const physicalReverse = (sequence: string) => [...sequence].reverse().map((base) => complement[base]).join('');
    for (const fixture of fixtures) {
        const circular = findOpenReadingFrames(fixture.sequence, 9, true).filter((orf) => orf.strand === fixture.strand);
        assert.equal(circular.length, 1, fixture.sequence);
        assert.equal(circular[0].length, 9);
        assert.deepEqual(circular[0].segments, fixture.segments);
        const bases = circular[0].segments.map(({ start, end }) => {
            const part = fixture.sequence.slice(start, end);
            return fixture.strand === 1 ? part : physicalReverse(part);
        }).join('');
        assert.equal(bases, 'ATGAAATAA');
        assert.deepEqual(findOpenReadingFrames(fixture.sequence, 9, false).filter((orf) => orf.strand === fixture.strand), []);
        assert.deepEqual(findOpenReadingFrames(fixture.sequence, undefined, true), []);
        // A too-short first stop cannot be skipped in favor of the later TAG.
        assert.deepEqual(findOpenReadingFrames(fixture.sequence, 12, true).filter((orf) => orf.strand === fixture.strand), []);
    }

    // Production default minLength=100: a 126-base ORF in a 216-base circle.
    const coding = `ATG${'AAA'.repeat(40)}TAA`;
    const circle = `${'C'.repeat(90)}${coding}`;
    for (const [rotation, workStart] of [[0, 90], [91, 215], [92, 214]]) {
        const forward = circle.slice(rotation) + circle.slice(0, rotation);
        for (const strand of [1, -1] as const) {
            const source = strand === 1 ? forward : physicalReverse(forward);
            const found = findOpenReadingFrames(source, undefined, true).filter((orf) => orf.strand === strand);
            assert.equal(found.length, 1, `default threshold rotation=${rotation} strand=${strand}`);
            assert.equal(found[0].length, 126);
            // Enumerate physical source indices in biological order, independently
            // of the scanner's doubled buffer and segment splitting helpers.
            const indices = Array.from({ length: 126 }, (_, offset) => {
                const index = (workStart + offset) % 216;
                return strand === 1 ? index : 215 - index;
            });
            const actualIndices = found[0].segments.flatMap(({ start, end }) => {
                assert.ok(start >= 0 && start < end && end <= 216);
                const span = Array.from({ length: end - start }, (_, offset) => start + offset);
                return strand === 1 ? span : span.reverse();
            });
            assert.deepEqual(actualIndices, indices);
            assert.equal(indices.map((index) => strand === 1 ? source[index] : complement[source[index]]).join(''), coding);
            assert.equal(findOpenReadingFrames(source, undefined, false).filter((orf) => orf.strand === strand).length, rotation === 0 ? 1 : 0);
        }
    }
    // ATGAAAAA has an in-frame TGA only after 12 bases, beyond its 8-base circle.
    // ATGCCCCCC has no stop at all; neither may produce an unterminated ORF.
    for (const sequence of ['ATGAAAAA', 'TTTTTCAT', 'ATGCCCCCC', 'GGGGGGCAT']) {
        assert.deepEqual(findOpenReadingFrames(sequence, 6, true), []);
    }
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
