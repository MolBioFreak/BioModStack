import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    getAlignmentDisplayName,
    resolveQueryLabel,
    resolveSubmittedQueryName,
} from '../src/components/MolBioToolkit/utils/alignmentLabels.js';

const ALIGNMENT_PANEL_PATH = resolve(process.cwd(), 'src/components/MolBioToolkit/panels/AlignmentPanel.tsx');

test('unnamed alignments get an explicit fallback title', () => {
    assert.equal(getAlignmentDisplayName(undefined), 'Unnamed alignment');
    assert.equal(getAlignmentDisplayName(null), 'Unnamed alignment');
    assert.equal(getAlignmentDisplayName('   '), 'Unnamed alignment');
    assert.equal(getAlignmentDisplayName('SacB alignment'), 'SacB alignment');
});

test('submitted query names preserve real names but omit the generic placeholder', () => {
    assert.equal(resolveSubmittedQueryName('Custom insert', 'Ignored FASTA'), 'Custom insert');
    assert.equal(resolveSubmittedQueryName('   ', 'FASTA header'), 'FASTA header');
    assert.equal(resolveSubmittedQueryName('   ', 'Untitled Sequence'), undefined);
    assert.equal(resolveSubmittedQueryName('   ', '   '), undefined);
});

test('query labels still keep the generic placeholder for unnamed inputs', () => {
    assert.equal(resolveQueryLabel('Custom insert', 'Ignored FASTA'), 'Custom insert');
    assert.equal(resolveQueryLabel('   ', 'FASTA header'), 'FASTA header');
    assert.equal(resolveQueryLabel('   ', 'Untitled Sequence'), 'Query sequence');
});

test('alignment panel source includes explicit sequence-alignment naming copy', () => {
    const source = readFileSync(ALIGNMENT_PANEL_PATH, 'utf8');
    assert.match(source, /Sequence alignment/i);
    assert.match(source, /getAlignmentDisplayName/);
});

test('zero-length alignment events describe boundaries, not fabricated residues', async () => {
    const {formatAlignmentSpan} = await import('../src/components/MolBioToolkit/utils/alignmentLabels.js');
    assert.equal(formatAlignmentSpan(0, 0, false, 2), 'before base 1');
    assert.equal(formatAlignmentSpan(1, 1, false, 2), 'between bases 1 and 2');
    assert.equal(formatAlignmentSpan(2, 2, false, 2), 'after base 2');
    assert.equal(formatAlignmentSpan(0, 0, false, 2, true), 'between bases 2 and 1');
    assert.equal(formatAlignmentSpan(2, 2, false, 2, true), 'between bases 2 and 1');
    assert.equal(formatAlignmentSpan(0, 2), '1-2');
    assert.equal(formatAlignmentSpan(7, 2, true, 10), '8->2');
});

test('wrapped alignment blocks display the last base rather than base zero', async () => {
    const {circularAlignmentDisplayEnd} = await import('../src/components/MolBioToolkit/utils/alignmentLabels.js');
    assert.equal(circularAlignmentDisplayEnd(30, 70, 100), 100);
    assert.equal(circularAlignmentDisplayEnd(30, 80, 100), 10);
    assert.equal(circularAlignmentDisplayEnd(0, 100, 100), 100);
    assert.equal(circularAlignmentDisplayEnd(0, 1, 100), 1);
});
