import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildTargetPreviewSelection,
    buildTargetPreviewSelections,
    getBoltzQualityPresetValues,
    getBoltzQualitySliderState,
    getPredictorFamiliesForSelection,
    getStructurePredictorOptions,
    inferTargetStructureFormat,
    resolveBoltzSamplingStepsFromSlider,
    resolveStructurePredictorSelection,
    resolveTargetPreviewSource,
} from '../src/components/structurePredictionUiState.js';

type PredictorOption = {
    id: string;
    disabled?: boolean;
    disabledReason?: string;
};

test('predict mode keeps all surfaced predictor combinations available', () => {
    const options = getStructurePredictorOptions('predict');

    assert.deepEqual(options.map((option: PredictorOption) => option.id), ['boltz', 'rf3', 'protenix', 'both', 'all']);
    assert.equal(options.every((option: PredictorOption) => option.disabled !== true), true);
});

test('complex mode only exposes truthful predictor choices and disables RF3 explicitly', () => {
    const options = getStructurePredictorOptions('complex');
    const rf3Option = options.find((option: PredictorOption) => option.id === 'rf3');

    assert.deepEqual(options.map((option: PredictorOption) => option.id), ['boltz', 'rf3', 'protenix', 'boltz_protenix']);
    assert.equal(rf3Option?.disabled, true);
    assert.match(rf3Option?.disabledReason || '', /predict-only/i);
});

test('complex mode resolves legacy ensemble aliases to the canonical boltz_protenix token', () => {
    const resolvedFromAll = resolveStructurePredictorSelection('complex', 'all');
    const resolvedFromBoth = resolveStructurePredictorSelection('complex', 'both');

    assert.equal(resolvedFromAll.valid, true);
    assert.equal(resolvedFromAll.canonicalSelection, 'boltz_protenix');
    assert.deepEqual(resolvedFromAll.families, ['boltz', 'protenix']);

    assert.equal(resolvedFromBoth.valid, true);
    assert.equal(resolvedFromBoth.canonicalSelection, 'boltz_protenix');
    assert.deepEqual(getPredictorFamiliesForSelection('complex', 'boltz_protenix'), ['boltz', 'protenix']);
});

test('complex mode rejects RF3-only selections instead of silently lying about support', () => {
    const resolved = resolveStructurePredictorSelection('complex', 'rf3');

    assert.equal(resolved.valid, false);
    assert.match(resolved.error || '', /predict-only/i);
});

test('boltz quality slider treats 200-step runs as the max preset and preserves legacy 1000-step runs as custom', () => {
    assert.deepEqual(getBoltzQualityPresetValues('max'), {
        samplingSteps: 200,
    });

    const maxState = getBoltzQualitySliderState({ samplingSteps: 200, recyclingSteps: 3 });
    assert.equal(maxState.presetId, 'max');
    assert.equal(maxState.label, 'High');
    assert.equal(maxState.sliderValue, 2);
    assert.equal(maxState.sliderMax, 2);

    const customState = getBoltzQualitySliderState({ samplingSteps: 1000, recyclingSteps: 10 });
    assert.equal(customState.presetId, 'custom');
    assert.equal(customState.sliderValue, 3);
    assert.equal(customState.sliderMax, 3);
});

test('boltz quality slider maps knob positions back to truthful preset steps without losing legacy custom values', () => {
    assert.equal(resolveBoltzSamplingStepsFromSlider({ currentSamplingSteps: 200, sliderValue: 0 }), 50);
    assert.equal(resolveBoltzSamplingStepsFromSlider({ currentSamplingSteps: 50, sliderValue: 1 }), 100);
    assert.equal(resolveBoltzSamplingStepsFromSlider({ currentSamplingSteps: 100, sliderValue: 2 }), 200);

    assert.equal(resolveBoltzSamplingStepsFromSlider({ currentSamplingSteps: 1000, sliderValue: 3 }), 1000);
    assert.equal(resolveBoltzSamplingStepsFromSlider({ currentSamplingSteps: 1000, sliderValue: 99 }), 200);
});

test('target preview helpers prefer local blob previews, infer mmcif correctly, and highlight the imported primary chain', () => {
    assert.equal(inferTargetStructureFormat('example_target.cif'), 'cif');
    assert.equal(inferTargetStructureFormat('example_target.mmcif'), 'cif');
    assert.equal(inferTargetStructureFormat('example_target.pdb'), 'pdb');

    assert.deepEqual(
        resolveTargetPreviewSource({
            previewUrl: 'blob:target-preview',
            stagedPath: '/inputs/target_model.cif',
            targetSource: { name: 'UploadedTarget.cif', url: 'https://files.rcsb.org/download/1ABC.pdb' },
        }),
        {
            structureUrl: 'blob:target-preview',
            format: 'cif',
        },
    );

    assert.deepEqual(buildTargetPreviewSelection('B'), [
        {
            chain_id: 'B',
            color: { r: 59, g: 130, b: 246 },
            focus: true,
        },
    ]);
});

test('target preview selections support multi-chain modal highlighting without duplicate focus churn', () => {
    assert.deepEqual(buildTargetPreviewSelections(['B', 'A', 'B', '', 'C']), [
        {
            chain_id: 'B',
            color: { r: 59, g: 130, b: 246 },
            focus: true,
        },
        {
            chain_id: 'A',
            color: { r: 59, g: 130, b: 246 },
            focus: false,
        },
        {
            chain_id: 'C',
            color: { r: 59, g: 130, b: 246 },
            focus: false,
        },
    ]);
});
