import assert from 'node:assert/strict';
import test from 'node:test';
import { buildFrustraMpnnPlotlyModel } from '../src/components/frustraMpnnPlotlyModel.js';
import { CANONICAL_AMINO_ACIDS, type CmLandscapeResidue } from '../src/components/conformationalMapping/conformationalMappingSemantics.js';

const makeResidue = (index: number, insertionCode = ''): CmLandscapeResidue => {
    const wt = CANONICAL_AMINO_ACIDS[index % CANONICAL_AMINO_ACIDS.length];
    return {
        key: `pdb:A:${index}:${insertionCode}`,
        entity_instance_id: 'pdb:A',
        auth_asym_id: 'A',
        auth_seq_id: String(index),
        insertion_code: insertionCode,
        sequence_index: index - 1,
        wt,
        slots: CANONICAL_AMINO_ACIDS.map((mutationAa, mutationIndex) => ({
            candidate_id: 'candidate',
            entity_instance_id: 'pdb:A',
            auth_asym_id: 'A',
            auth_seq_id: String(index),
            insertion_code: insertionCode,
            sequence_index: index - 1,
            wt,
            mutation_aa: mutationAa,
            score: index + mutationIndex / 100,
            class: mutationIndex % 3 === 0 ? 'high' : mutationIndex % 3 === 1 ? 'neutral' : 'minimal',
            scoreable: true,
            status: 'ok',
            reason: null,
            provenance: { source: 'persisted' },
        })),
    };
};

test('Plotly model covers the full 540 by 20 landscape without sampling', () => {
    const model = buildFrustraMpnnPlotlyModel(Array.from({ length: 540 }, (_, index) => makeResidue(index + 1)));
    assert.equal(model.residueLabels.length, 540);
    assert.equal(model.heatmapScores.length, 20);
    assert.ok(model.heatmapScores.every((row) => row.length === 540));
    assert.equal(model.heatmapScores.reduce((total, row) => total + row.length, 0), 10_800);
    assert.equal(model.nativeScores.length, 540);
    assert.ok(CANONICAL_AMINO_ACIDS.every((aa) => model.substitutionScores[aa].length === 540));
    assert.equal(model.bestAlternativeDeltas.length, 540);
    assert.equal(model.worstAlternativeDeltas.length, 540);
    assert.equal(model.medianAlternativeScores.length, 540);
    assert.ok(Math.abs(model.bestAlternativeDeltas[0]! - 0.18) < 1e-12);
    assert.ok(Math.abs(model.worstAlternativeDeltas[0]! - (-0.01)) < 1e-12);
    assert.ok(Math.abs(model.medianAlternativeScores[0]! - 1.10) < 1e-12);
    assert.ok(Math.abs(model.highAlternativeFractions[0]! - (7 / 19)) < 1e-12);
    assert.ok(Math.abs(model.minimalAlternativeFractions[0]! - (6 / 19)) < 1e-12);
    assert.deepEqual(model.substitutionClassFractions.A, { high: 1, neutral: 0, minimal: 0, missing: 0 });
});

test('Plotly hover authority preserves exact insertion identity, persisted class, and missingness', () => {
    const residue = makeResidue(42, 'B');
    residue.slots[0] = { ...residue.slots[0], score: null, class: null, scoreable: false, status: 'missing', reason: 'unsupported_residue' };
    const model = buildFrustraMpnnPlotlyModel([residue]);
    assert.deepEqual(model.residueLabels, ['A:42B']);
    assert.equal(model.heatmapScores[0][0], null);
    assert.deepEqual(model.heatmapCustomData[0][0], [residue.wt, 'unavailable', 'missing', 'unsupported_residue']);
});

test('Plotly model fails closed when an exact substitution or native slot is missing', () => {
    const residue = makeResidue(1);
    residue.slots.pop();
    assert.throws(() => buildFrustraMpnnPlotlyModel([residue]), /missing_exact_20_slot/);
});
