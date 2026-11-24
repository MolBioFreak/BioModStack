import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { setOrigin, splitAtLinearization } from '../src/components/MolBioToolkit/utils/setOrigin.js';
import { setSequenceTopology, findLinearizationBlockers } from '../src/components/MolBioToolkit/utils/topology.js';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/set-origin-cases.json', import.meta.url), 'utf8'));
const segs = (f: { segments: Array<{ start: number; end: number }> }) => f.segments.map((s) => `${s.start}-${s.end}`);
const sortedSites = (sites: Array<{ start: number; end: number }>) => sites.map((s) => `${s.start}-${s.end}`).sort();
const base = () => ({
    sequence: fixture.sequence, circular: true, sequenceType: 'dna' as const,
    features: structuredClone(fixture.features),
    primers: fixture.primers.map((p: any) => ({ ...p, start: p.sites[0].start, end: p.sites[0].end })),
});

for (const c of fixture.cases) {
    test(`A9a set origin ${c.shift}${c.reverseComplement ? ' + reverse complement' : ''} equals the Python mapper`, () => {
        const out = setOrigin(base(), c.shift, c.reverseComplement);
        assert.equal(out.sequence, c.sequence);
        for (const expected of c.features) {
            const got = out.features.find((f: any) => f.id === expected.id)!;
            assert.deepEqual(segs(got as any), segs(expected), expected.id);
            assert.equal(got.strand, expected.strand, expected.id);
            assert.equal(got.start, expected.start); assert.equal(got.end, expected.end);
        }
        for (const expected of c.primerSites) {
            const got = out.primers.find((p: any) => p.id === expected.id)!;
            assert.deepEqual(sortedSites(got.sites), sortedSites(expected.segments), expected.id);
            assert.equal(got.strand, expected.strand, expected.id);
        }
    });
}

test('A9a rotating then rotating back restores features and sequence', () => {
    const start = base();
    const back = setOrigin(setOrigin(start, 15), fixture.sequence.length - 15);
    assert.equal(back.sequence, start.sequence);
    assert.deepEqual(back.features.map(segs as any), start.features.map(segs as any));
});

test('A9b linearization proceeds and keeps crossing entries as split two-segment entries', () => {
    const data = base();
    assert.deepEqual(findLinearizationBlockers(data), []);
    data.primers.push({ id: 'wp', strand: 1, start: 56, end: 4, sites: [{ start: 56, end: 60, strand: 1 }, { start: 0, end: 4, strand: 1 }] });
    const out = setSequenceTopology(data, false);
    assert.equal(out.circular, false);
    const wrap = out.features.find((f: any) => f.id === 'wrap')!;
    assert.deepEqual(segs(wrap as any), ['50-60', '0-8']);
    assert.equal((wrap as any).provenance.linearization_split, true);
    assert.equal((out.features.find((f: any) => f.id === 'plain') as any).provenance, undefined);
    const wp = out.primers.find((p: any) => p.id === 'wp') as any;
    assert.equal(wp.sites.length, 2); assert.equal(wp.provenance.linearization_split, true);
    assert.equal(splitAtLinearization(data).sequence, data.sequence);
});
