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
