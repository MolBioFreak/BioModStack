import assert from 'node:assert/strict';
import test from 'node:test';

import { shouldComputeRestrictionPositions } from '../src/components/MolBioToolkit/utils/gcTrackPolicy.js';

test('restriction-site scans run only for the restriction-density metric', () => {
    assert.equal(shouldComputeRestrictionPositions('gc'), false);
    assert.equal(shouldComputeRestrictionPositions('ambiguity_density'), false);
    assert.equal(shouldComputeRestrictionPositions('homopolymer_burden'), false);
    assert.equal(shouldComputeRestrictionPositions('restriction_density'), true);
});