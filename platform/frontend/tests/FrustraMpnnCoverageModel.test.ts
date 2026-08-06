import assert from 'node:assert/strict';
import test from 'node:test';
import { buildFrustraMpnnCoverageReadiness } from '../src/components/frustraMpnnCoverageModel.js';

test('complete coverage becomes a scientist-facing ready state', () => {
    const readiness = buildFrustraMpnnCoverageReadiness(
        { expected: 540, mapped: 540, scoreable: 540, ambiguous: 0, excluded: 0 },
        { expected: 10_800, observed: 10_800, scoreable: 10_800 },
        {},
    );
    assert.equal(readiness.status, 'Complete');
    assert.equal(readiness.residueCoverage, 1);
    assert.equal(readiness.slotCoverage, 1);
    assert.equal(readiness.missingSlots, 0);
    assert.equal(readiness.issueCount, 0);
});

test('incomplete coverage exposes deficits without backend jargon as the headline', () => {
    const readiness = buildFrustraMpnnCoverageReadiness(
        { expected: 100, mapped: 96, scoreable: 94, ambiguous: 2, excluded: 4 },
        { expected: 2000, observed: 1920, scoreable: 1880 },
        { unresolved_identity: 80, unsupported_residue: 40 },
    );
    assert.equal(readiness.status, 'Review missing data');
    assert.equal(readiness.missingResidues, 6);
    assert.equal(readiness.missingSlots, 120);
    assert.equal(readiness.issueCount, 6);
    assert.deepEqual(readiness.missingness, [['unresolved_identity', 80], ['unsupported_residue', 40]]);
});
