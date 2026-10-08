import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import { clearFeatureAnnotations } from '../src/components/MolBioToolkit/utils/annotations.js';
import {
    assertAnnotationTopology,
    resolveAnnotationAlignmentPolicy,
    resolveAnnotationSequenceAlignment,
    transformFeatureForAlignment,
} from '../src/components/MolBioToolkit/utils/annotationTransfer.js';

test('clear annotations removes features only and preserves primers and sequence', () => {
    const original = {
        sequence: 'AACCGGTT',
        circular: true,
        features: [{ id: 'f1' }, { id: 'f2' }],
        primers: [{ id: 'p1' }],
    };

    const cleared = clearFeatureAnnotations(original);
    assert.deepEqual(cleared.features, []);
    assert.equal(cleared.sequence, original.sequence);
    assert.equal(cleared.primers, original.primers);
    assert.notEqual(cleared, original);
});

test('annotation alignment accepts exact identity and a unique circular origin rotation', () => {
    assert.deepEqual(
        resolveAnnotationSequenceAlignment('AAAACCCCGGGG', 'AAAACCCCGGGG', true),
        { length: 12, mode: 'exact', reverseComplement: false, rotation: 0 },
    );
    assert.deepEqual(
        resolveAnnotationSequenceAlignment('AAAACCCCGGGG', 'CCCCGGGGAAAA', true),
        { length: 12, mode: 'rotated', reverseComplement: false, rotation: 4 },
    );
});

test('rotated annotation transfer splits an origin-spanning feature deterministically', () => {
    const alignment = resolveAnnotationSequenceAlignment('AAAACCCCGGGG', 'CCCCGGGGAAAA', true);
    const transformed = transformFeatureForAlignment({
        id: 'feature',
        name: 'shifted',
        type: 'misc_feature',
        start: 2,
        end: 6,
        strand: 1 as const,
        segments: [{ start: 2, end: 6 }],
    }, alignment);

    assert.deepEqual(transformed.segments, [{ start: 10, end: 12 }, { start: 0, end: 2 }]);
    assert.equal(transformed.start, 0);
    assert.equal(transformed.end, 12);
    assert.equal(transformed.strand, 1);
});

test('single-base parser point features become bounded one-base half-open spans', () => {
    const exact = transformFeatureForAlignment({
        id: 'point',
        name: 'conflict',
        type: 'misc_difference',
        start: 425,
        end: 425,
        strand: 1 as const,
    }, { length: 4361, mode: 'exact', reverseComplement: false, rotation: 0 });
    assert.deepEqual(exact.segments, [{ start: 425, end: 426 }]);
    assert.equal(exact.start, 425);
    assert.equal(exact.end, 426);

    const rotated = transformFeatureForAlignment({
        id: 'point-at-origin',
        name: 'point',
        type: 'misc_feature',
        start: 0,
        end: 0,
        strand: 1 as const,
        segments: [{ start: 0, end: 0 }],
    }, { length: 12, mode: 'rotated', reverseComplement: false, rotation: 4 });
    assert.deepEqual(rotated.segments, [{ start: 8, end: 9 }]);
});

test('reverse-complement annotation transfer mirrors coordinates and strand', () => {
    const alignment = resolveAnnotationSequenceAlignment('AAAACCCG', 'CGGGTTTT', false);
    const transformed = transformFeatureForAlignment({
        id: 'feature',
        name: 'reverse',
        type: 'CDS',
        start: 0,
        end: 4,
        strand: 1 as const,
    }, alignment);

    assert.equal(alignment.mode, 'reverse_complement');
    assert.deepEqual(transformed.segments, [{ start: 4, end: 8 }]);
    assert.equal(transformed.strand, -1);
});

test('annotation transfer rejects topology mismatch, sequence mismatch, and ambiguous rotations', () => {
    assert.throws(() => assertAnnotationTopology(true, false), /topology/i);
    assert.throws(
        () => resolveAnnotationSequenceAlignment('AAAACCCC', 'AAAAGGGG', true),
        /does not match/i,
    );
    assert.throws(
        () => resolveAnnotationSequenceAlignment('ATAT', 'TATA', true),
        /ambiguous/i,
    );
});

test('annotation transfer only permits reverse-complement matching for explicit dsDNA', () => {
    assert.deepEqual(
        resolveAnnotationAlignmentPolicy(
            { sequenceType: 'dna', moleculeStrandedness: 'double' },
            { sequenceType: 'dna', moleculeStrandedness: 'double' },
        ),
        { sequenceType: 'dna', allowReverseComplement: true },
    );
    assert.deepEqual(
        resolveAnnotationAlignmentPolicy(
            { sequenceType: 'dna', moleculeStrandedness: 'single' },
            { sequenceType: 'dna', moleculeStrandedness: 'single' },
        ),
        { sequenceType: 'dna', allowReverseComplement: false },
    );
    assert.deepEqual(
        resolveAnnotationAlignmentPolicy(
            { sequenceType: 'dna', moleculeStrandedness: 'double', strandednessExplicit: false },
            { sequenceType: 'dna', moleculeStrandedness: 'double' },
        ),
        { sequenceType: 'dna', allowReverseComplement: false },
    );
    assert.deepEqual(
        resolveAnnotationAlignmentPolicy(
            { sequenceType: 'rna', moleculeStrandedness: 'single' },
            { sequenceType: 'rna', moleculeStrandedness: 'single' },
        ),
        { sequenceType: 'rna', allowReverseComplement: false },
    );
    assert.throws(
        () => resolveAnnotationAlignmentPolicy(
            { sequenceType: 'dna', moleculeStrandedness: 'double' },
            { sequenceType: 'dna', moleculeStrandedness: 'single' },
        ),
        /strandedness/i,
    );
});

test('authoritative alignment is literal, alphabet-strict, and jointly orientation-unique', () => {
    assert.throws(
        () => resolveAnnotationSequenceAlignment(
            'AAAAC',
            'GTTTT',
            false,
            { sequenceType: 'dna', allowReverseComplement: false },
        ),
        /does not match/i,
    );
    assert.throws(
        () => resolveAnnotationSequenceAlignment(
            'AUTG',
            'AUTG',
            false,
            { sequenceType: 'dna', allowReverseComplement: false },
        ),
        /unsupported.*DNA/i,
    );
    assert.throws(
        () => resolveAnnotationSequenceAlignment(
            'ATUG',
            'ATUG',
            false,
            { sequenceType: 'rna', allowReverseComplement: false },
        ),
        /unsupported.*RNA/i,
    );
    assert.throws(
        () => resolveAnnotationSequenceAlignment(
            'ATGCAT',
            'TGCATA',
            true,
            { sequenceType: 'dna', allowReverseComplement: true },
        ),
        /ambiguous/i,
    );
});

test('annotation dialog exposes clear and authoritative annotated-file transfer actions', () => {
    const panel = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/AutoAnnotatePanel.tsx'), 'utf8');
    const toolkit = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/MolBioToolkitV2.tsx'), 'utf8');

    assert.match(panel, /Clear all feature annotations/);
    assert.match(panel, /Import SnapGene \/ GenBank annotations/);
    assert.match(panel, /\.dna,\.gb,\.gbk,\.genbank/);
    assert.match(toolkit, /Clear .* feature annotations/);
    assert.match(toolkit, /annotation_import/);
    assert.match(toolkit, /Import .* annotations from/);
    assert.match(toolkit, /anyToJson\(file,\s*\{/);
    assert.match(toolkit, /user_provided_origin_unknown/);
    assert.match(toolkit, /crypto\.subtle\.digest\('SHA-256'/);
    assert.match(toolkit, /parser_messages/);
    assert.match(toolkit, /existing feature annotations.*clear/i);
});
