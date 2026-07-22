import assert from 'node:assert/strict';
import test from 'node:test';

import {
    DEFAULT_BMS_DEV_FEATURES,
    DEFAULT_BMS_FEATURES,
    isBmsFeatureEnabled,
    isBmsFeatureVisible,
    normalizeBmsFeatureState,
    normalizeBmsFeatures,
    resolveBmsFeatureQueryState,
    resolveShowDevFeaturesDefault,
} from '../src/runtime/installFeatures.js';

test('optional hardware features default disabled until backend state is confirmed', () => {
    assert.deepEqual(DEFAULT_BMS_FEATURES, { bioxp: false });
    assert.deepEqual(DEFAULT_BMS_DEV_FEATURES, { bioxp: true });
    assert.deepEqual(normalizeBmsFeatures(null), DEFAULT_BMS_FEATURES);
    assert.deepEqual(normalizeBmsFeatures({}), DEFAULT_BMS_FEATURES);
});

test('install feature normalization honors resolved backend flags', () => {
    const features = normalizeBmsFeatures({ features: { bioxp: false } });
    assert.deepEqual(features, { bioxp: false });
    assert.equal(isBmsFeatureEnabled(features, 'bioxp'), false);
});

test('failed feature refresh discards cached positive BioXP state', () => {
    const cached = normalizeBmsFeatureState({
        features: { bioxp: true },
    });

    assert.equal(resolveBmsFeatureQueryState(cached, false).features.bioxp, true);
    assert.equal(resolveBmsFeatureQueryState(cached, true).features.bioxp, false);
});

test('dev feature flags hide developer-only install features until explicitly shown', () => {
    const state = normalizeBmsFeatureState({
        features: { bioxp: true },
        dev_features: { bioxp: true },
    });
    assert.equal(isBmsFeatureVisible(state, 'bioxp', false), false);
    assert.equal(isBmsFeatureVisible(state, 'bioxp', true), true);
});

test('Vite development starts with developer surfaces visible without changing production defaults', () => {
    assert.equal(resolveShowDevFeaturesDefault(true, null), true);
    assert.equal(resolveShowDevFeaturesDefault(false, null), false);
    assert.equal(resolveShowDevFeaturesDefault(false, 'true'), true);
    assert.equal(resolveShowDevFeaturesDefault(true, 'false'), false);
});
