import assert from 'node:assert/strict';
import test from 'node:test';

import {
    effectiveVramLimitMb,
    thresholdFromEffectiveVramLimit,
} from '../src/lib/gpuSchedulerMath.js';

test('GPU effective-limit round trip does not apply the safety margin twice', () => {
    const maxVramMb = 32_768;
    const safetyMarginMb = 2_048;
    const configuredThreshold = 0.9;
    const effectiveLimit = effectiveVramLimitMb(maxVramMb, configuredThreshold, safetyMarginMb);

    const savedThreshold = thresholdFromEffectiveVramLimit(
        maxVramMb,
        effectiveLimit,
        safetyMarginMb,
    );
    const limitAfterOneSave = effectiveVramLimitMb(maxVramMb, savedThreshold, safetyMarginMb);
    const thresholdAfterSecondSave = thresholdFromEffectiveVramLimit(
        maxVramMb,
        limitAfterOneSave,
        safetyMarginMb,
    );

    assert.equal(effectiveLimit, 27_443);
    assert.equal(limitAfterOneSave, effectiveLimit);
    assert.equal(
        effectiveVramLimitMb(maxVramMb, thresholdAfterSecondSave, safetyMarginMb),
        effectiveLimit,
    );
});

test('GPU floor-clamped no-op save preserves the configured threshold', () => {
    const maxVramMb = 4_096;
    const safetyMarginMb = 2_048;
    const configuredThreshold = 0.5;
    const effectiveLimit = effectiveVramLimitMb(maxVramMb, configuredThreshold, safetyMarginMb);

    assert.equal(effectiveLimit, 1_024);
    assert.equal(
        thresholdFromEffectiveVramLimit(
            maxVramMb,
            effectiveLimit,
            safetyMarginMb,
            configuredThreshold,
        ),
        configuredThreshold,
    );
});

test('GPU floor-aware inverse still honors an edited limit', () => {
    assert.equal(thresholdFromEffectiveVramLimit(4_096, 1_200, 2_048, 0.5), 0.79296875);
});