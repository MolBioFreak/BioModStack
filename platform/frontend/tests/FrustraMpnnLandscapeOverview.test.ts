import assert from 'node:assert/strict';
import test from 'node:test';
import { buildFrustraMpnnOverviewModel } from '../src/components/FrustraMpnnLandscapeOverview.js';
import { CANONICAL_AMINO_ACIDS, type CmLandscapeResidue } from '../src/components/conformationalMapping/conformationalMappingSemantics.js';

const residue = (index: number, chain = 'A', insertionCode = ''): CmLandscapeResidue => ({
    key: `pdb:${chain}:${index}:${insertionCode}`,
    entity_instance_id: `pdb:${chain}`,
    auth_asym_id: chain,
    auth_seq_id: String(index),
    insertion_code: insertionCode,
    sequence_index: index - 1,
    wt: CANONICAL_AMINO_ACIDS[index % CANONICAL_AMINO_ACIDS.length],
    slots: CANONICAL_AMINO_ACIDS.map((mutationAa, mutationIndex) => ({
        candidate_id: 'candidate',
        entity_instance_id: `pdb:${chain}`,
        auth_asym_id: chain,
        auth_seq_id: String(index),
        insertion_code: insertionCode,
        sequence_index: index - 1,
        wt: CANONICAL_AMINO_ACIDS[index % CANONICAL_AMINO_ACIDS.length],
        mutation_aa: mutationAa,
        score: index + (mutationIndex / 100),
        class: mutationIndex % 3 === 0 ? 'high' : mutationIndex % 3 === 1 ? 'neutral' : 'minimal',
        scoreable: true,
        status: 'ok',
        reason: null,
        provenance: { source: 'persisted' },
    })),
});

test('complete overview preserves all 540 residues and exact 10,800 persisted substitutions', () => {
    const model = buildFrustraMpnnOverviewModel(Array.from({ length: 540 }, (_, index) => residue(index + 1)));
    assert.equal(model.residues.length, 540);
    assert.equal(model.cells.length, 10_800);
    assert.equal(model.chains.length, 1);
    assert.deepEqual(new Set(model.cells.map((cell) => cell.mutationAa)), new Set(CANONICAL_AMINO_ACIDS));
    assert.equal(model.cells.filter((cell) => cell.isNative).length, 540);
});

test('overview preserves chain boundaries, insertion codes, backend classes, scores, and missingness', () => {
    const first = residue(42, 'A', 'B');
    const second = residue(1, 'C');
    second.slots[0] = { ...second.slots[0], score: null, class: null, scoreable: false, status: 'missing', reason: 'unsupported_residue' };
    const model = buildFrustraMpnnOverviewModel([first, second]);
    assert.deepEqual(model.chains, [
        { authAsymId: 'A', start: 0, end: 1 },
        { authAsymId: 'C', start: 1, end: 2 },
    ]);
    assert.equal(model.cells[0].residue.auth_seq_id, '42');
    assert.equal(model.cells[0].residue.insertion_code, 'B');
    assert.equal(model.cells[20].score, null);
    assert.equal(model.cells[20].className, null);
    assert.equal(model.cells[20].status, 'missing');
    assert.equal(model.cells[20].reason, 'unsupported_residue');
});

test('overview fails closed when an exact-20 substitution slot is absent', () => {
    const incomplete = residue(1);
    incomplete.slots.pop();
    assert.throws(() => buildFrustraMpnnOverviewModel([incomplete]), /missing_exact_20_slot/);
});
