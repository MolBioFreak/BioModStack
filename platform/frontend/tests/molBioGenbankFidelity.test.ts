import assert from 'node:assert/strict';
import test from 'node:test';

import { anyToJson, jsonToGenbank } from '@teselagen/bio-parsers';
import { genbankFeature, genbankPrimerFeatures, buildAssemblyPrimersCsv, buildFragmentsFasta } from '../src/components/MolBioToolkit/utils/exportData.js';
import { browserParsedFeature } from '../src/components/MolBioToolkit/utils/sequenceRecord.js';

const seq = Array.from({ length: 120 }, (_, i) => 'ACGT'[(i * 7 + (i >> 2)) % 4]).join('');
const rc = (s: string) => s.split('').reverse().map((c) => ({ A: 'T', C: 'G', G: 'C', T: 'A' } as Record<string, string>)[c]).join('');
const bases = (f: { segments: Array<{ start: number; end: number }>; strand: number }) => {
    const text = f.segments.map((s) => seq.slice(s.start, s.end)).join('');
    return f.strand === -1 ? rc(f.segments.map((s) => seq.slice(s.start, s.end)).reverse().join('')) : text;
};

const features = [
    { id: 'a', name: 'plain', type: 'misc_feature', start: 9, end: 30, strand: 1, segments: [{ start: 9, end: 30 }] },
    { id: 'b', name: 'wrap', type: 'gene', start: 0, end: 120, strand: 1, segments: [{ start: 109, end: 120 }, { start: 0, end: 8 }] },
    { id: 'c', name: 'minus', type: 'CDS', start: 9, end: 70, strand: -1, segments: [{ start: 49, end: 70 }, { start: 9, end: 30 }],
      qualifiers: { codon_start: 2, translation: ['MK'] }, notes: { note: 'scalar note' }, provenance: { mapping_status: 'truncated' } },
];

async function roundTrip(extraPrimers: any[] = []) {
    const text = jsonToGenbank({
        name: 'rt', sequence: seq, circular: true, type: 'DNA',
        features: [...features.map(genbankFeature), ...genbankPrimerFeatures(extraPrimers, seq.length, true)],
    }, { inclusive1BasedEnd: true }) as string;
    const result: any = await anyToJson(text, { fileName: 'rt.gb', inclusive1BasedStart: false, inclusive1BasedEnd: true, jsonType: 'json' } as any);
    const parsed = (Array.isArray(result) ? result[0] : result).parsedSequence;
    return { text, parsed };
}

test('A6d GenBank export then import preserves bases, strands, part order, codon_start, translation and notes', async () => {
    const { parsed } = await roundTrip();
    const back = Object.fromEntries(parsed.features.map((f: any) => [f.name, browserParsedFeature(f, f.name, seq.length)]));
    for (const original of features) {
        const got = back[original.name] as any;
        assert.deepEqual(got.segments, original.segments, original.name);
        assert.equal(got.strand, original.strand);
        assert.equal(bases(got), bases(original as any), `${original.name} bases`);
    }
    const minus = parsed.features.find((f: any) => f.name === 'minus');
    assert.deepEqual(minus.notes.codon_start.map(String), ['2']);
    assert.deepEqual(minus.notes.translation, ['MK']);
    assert.deepEqual(minus.notes.note, ['scalar note']);
    assert.match(minus.notes.bms_provenance[0], /truncated/);
});

test('A6e primer tails stay separate and unplaced primers are not written as 1..1', async () => {
    const primers = [
        { name: 'placed', sequence: 'GGGG' + seq.slice(20, 40), start: 20, end: 40, strand: 1 as const, notes: { tail_sequence: 'GGGG', annealing_sequence: seq.slice(20, 40) } },
        { name: 'unplaced', sequence: 'ACGTACGTACGTACGTAC', start: 0, end: 0, strand: 1 as const, binding_status: 'needs_review' as const },
    ];
    const { text, parsed } = await roundTrip(primers);
    assert.doesNotMatch(text, /primer_bind\s+1\.\.1\b/);
    assert.doesNotMatch(text, /unplaced/);
    const placed = parsed.primers.find((p: any) => p.name === 'placed');
    assert.equal(placed.start, 20); assert.equal(placed.end, 40);
    assert.match(text, /tail_sequence="GGGG"/);
});

test('CSV and FASTA order builders', () => {
    const csv = buildAssemblyPrimersCsv([{ id: 'p1', name: 'F1', direction: 'forward', full_sequence: 'GGGGACGT', annealing_sequence: 'ACGT', tail_sequence: 'GGGG', tm: 60.1, tm_model: 'nn', warnings: ['w'] }]);
    assert.match(csv.split('\n')[0], /annealing_sequence.*tail_sequence.*tm.*tm_model/);
    assert.match(csv.split('\n')[1], /"GGGG"/);
    assert.equal(buildFragmentsFasta([{ id: 'f', name: 'frag', sequence: 'ACGT' }]), '>frag|length=4\nACGT\n');
});
