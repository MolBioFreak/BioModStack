import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildPrimersTsv,
    formatPrimerSites,
} from '../src/components/MolBioToolkit/utils/exportData.js';

const wrappedPrimer = {
    name: 'origin-primer',
    sequence: 'ACGTACGT',
    sequenceType: 'dna' as const,
    start: 12,
    end: 4,
    strand: 1 as const,
    tm: 61.2,
    gc_percent: 50,
    sites: [
        { start: 12, end: 16, strand: 1 as const },
        { start: 0, end: 4, strand: 1 as const },
    ],
};

test('primer-site export preserves ordered split geometry', () => {
    assert.equal(formatPrimerSites(wrappedPrimer), '12-16:1;0-4:1');
});

test('primer TSV exports explicit coordinate convention and ordered sites', () => {
    const tsv = buildPrimersTsv([wrappedPrimer], 'dna');
    const [header, row] = tsv.split('\n');

    assert.match(header, /Start \(0-based\)/);
    assert.match(header, /End \(half-open\)/);
    assert.match(header, /Ordered Sites/);
    assert.equal(
        row,
        'origin-primer\tACGTACGT\tdna\t12\t4\t12-16:1;0-4:1\t+\t61.2\t50',
    );
});

test('primer TSV canonicalizes an origin-crossing unsplit library placement', () => {
    const tsv = buildPrimersTsv([{
        name: 'library-wrap',
        sequence: 'AACCGG',
        start: 14,
        end: 20,
        strand: 1,
    }], 'dna', 16, true);
    assert.equal(
        tsv.split('\n')[1],
        'library-wrap\tAACCGG\tdna\t14\t4\t14-16:1;0-4:1\t+\t\t',
    );
});
