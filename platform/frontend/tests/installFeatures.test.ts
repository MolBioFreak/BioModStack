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
} from '../src/runtime/installFeatures.js';

test('optional hardware features default disabled until backend state is confirmed', () => {
    assert.deepEqual(DEFAULT_BMS_FEATURES, {
        bioxp: false,
        stats_tools: true,
        assay_db: true,
    });
    assert.deepEqual(DEFAULT_BMS_DEV_FEATURES, {
        bioxp: true,
        stats_tools: true,
        assay_db: true,
    });
    assert.deepEqual(normalizeBmsFeatures(null), DEFAULT_BMS_FEATURES);
    assert.deepEqual(normalizeBmsFeatures({}), DEFAULT_BMS_FEATURES);
});

test('install feature normalization honors resolved backend flags without dropping defaults', () => {
    const features = normalizeBmsFeatures({
        features: {
            bioxp: false,
            stats_tools: true,
        },
    });

    assert.deepEqual(features, {
        bioxp: false,
        stats_tools: true,
        assay_db: true,
    });
    assert.equal(isBmsFeatureEnabled(features, 'bioxp'), false);
    assert.equal(isBmsFeatureEnabled(features, 'stats_tools'), true);
});

test('failed feature refresh discards cached positive BioXP state', () => {
    const cached = normalizeBmsFeatureState({
        features: { bioxp: true, stats_tools: true, assay_db: true },
    });

    assert.equal(resolveBmsFeatureQueryState(cached, false).features.bioxp, true);
    assert.equal(resolveBmsFeatureQueryState(cached, true).features.bioxp, false);
});

test('dev feature flags hide developer-only install features until explicitly shown', () => {
    const state = normalizeBmsFeatureState({
        features: {
            bioxp: true,
            stats_tools: true,
            assay_db: true,
        },
        dev_features: {
            bioxp: true,
            stats_tools: true,
            assay_db: true,
        },
    });

    assert.equal(isBmsFeatureVisible(state, 'bioxp', false), false);
    assert.equal(isBmsFeatureVisible(state, 'stats_tools', false), false);
    assert.equal(isBmsFeatureVisible(state, 'assay_db', false), false);
    assert.equal(isBmsFeatureVisible(state, 'bioxp', true), true);
    assert.equal(isBmsFeatureVisible(state, 'stats_tools', true), true);
    assert.equal(isBmsFeatureVisible(state, 'assay_db', true), true);
});
