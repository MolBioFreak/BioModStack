import assert from 'node:assert/strict';
import test from 'node:test';

import {
    DEFAULT_BMS_FEATURES,
    isBmsFeatureEnabled,
    normalizeBmsFeatures,
} from '../src/runtime/installFeatures.js';

test('install features default to enabled for existing local installs', () => {
    assert.deepEqual(DEFAULT_BMS_FEATURES, {
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
