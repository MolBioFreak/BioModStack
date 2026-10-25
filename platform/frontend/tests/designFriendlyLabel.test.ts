import { test } from 'node:test';
import assert from 'node:assert/strict';
import { predictionDisplayLabel, predictionJobLabel } from '../src/lib/designFriendlyLabel';

test('prediction labels retain readable names and hide machine identities without mutation', () => {
    const design = { name: `protenix_${'a'.repeat(64)}_complex_seed_42_prediction_3` };
    const original = design.name;
    assert.equal(predictionDisplayLabel(design, 0), 'Prediction 3');
    assert.equal(design.name, original);
    assert.equal(predictionDisplayLabel({ name: 'TNF complex' }), 'TNF complex');
    assert.equal(predictionDisplayLabel({ name: 'a'.repeat(64), native_sample: { producer_rank: 0 } }), 'Prediction · producer rank 0');
    assert.equal(predictionDisplayLabel({ name: 'a'.repeat(64) }, 2), 'Prediction 3');
    assert.equal(predictionDisplayLabel({ name: 'a'.repeat(64), provenance: { native_producer: { producer_rank: 2 } } }, 5), 'Sample 2');
});
test('only prediction job display labels are shortened', () => {
    const name = 'binder-round-83680512-0496-5a8e-a5bf-fdd95b1226c7';
    assert.equal(predictionJobLabel({ name, model_id: 'protenix' }), 'Protenix prediction');
    assert.equal(predictionJobLabel({ name, model_id: 'bindcraft2' }), name);
});
