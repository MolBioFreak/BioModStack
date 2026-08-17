import assert from 'node:assert/strict';
import test from 'node:test';

import {
    normalizeProteinLocalValidators,
    toggleProteinLocalValidator,
} from '../src/components/proteinLocalRedesignValidators';

test('Protein Local Redesign defaults to Protenix V2', () => {
    assert.deepEqual(normalizeProteinLocalValidators({}), ['protenix_v2']);
});

test('Protein Local Redesign restores every supported validator subset', () => {
    assert.deepEqual(
        normalizeProteinLocalValidators({ structure_validators: ['esmfold2', 'protenix_v2'] }),
        ['esmfold2', 'protenix_v2'],
    );
    assert.deepEqual(normalizeProteinLocalValidators({ run_boltz_validation: true }), ['boltz2']);
});

test('validator toggles preserve one required selection and canonical order', () => {
    assert.deepEqual(toggleProteinLocalValidator(['protenix_v2'], 'protenix_v2'), ['protenix_v2']);
    assert.deepEqual(
        toggleProteinLocalValidator(['protenix_v2'], 'esmfold2'),
        ['esmfold2', 'protenix_v2'],
    );
    assert.deepEqual(
        toggleProteinLocalValidator(['boltz2', 'esmfold2'], 'boltz2'),
        ['esmfold2'],
    );
});

test('saved invalid validator suites fail closed', () => {
    assert.throws(
        () => normalizeProteinLocalValidators({ structure_validators: [] }),
        /between one and three validators/i,
    );
    assert.throws(
        () => normalizeProteinLocalValidators({ structure_validators: ['esmfold2', 'unknown'] }),
        /unsupported validator/i,
    );
    assert.throws(
        () => normalizeProteinLocalValidators({ structure_validators: ['esmfold2', 'esmfold2'] }),
        /duplicates/i,
    );
});
