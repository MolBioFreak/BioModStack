import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildBoltzCpSubmitParams,
    buildTargetPreviewSelection,
    buildTargetPreviewSelections,
    deriveBoltzCpGpuLaunchSettings,
    getBoltzQualityPresetValues,
    getBoltzQualitySliderState,
    getPredictorFamiliesForSelection,
    getStructurePredictorOptions,
    inferTargetStructureFormat,
    resolveBoltzSamplingStepsFromSlider,
    resolveStructureLaunchConfig,
    resolveStructurePredictorSelection,
    resolveStructureSubmitTarget,
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

test('boltz cp experimental launch config locks the structure template into single-fold boltz mode', () => {
    const config = resolveStructureLaunchConfig({
        template_model_id: 'boltz_cp_experimental',
        structure_launch_variant: 'boltz_cp_experimental',
    });

    assert.equal(config.variant, 'boltz_cp_experimental');
    assert.equal(config.submitModelId, 'boltz_cp_experimental');
    assert.equal(config.submitMode, 'design');
    assert.equal(config.allowPredictorSelection, false);
    assert.equal(config.showParallelJobs, false);
    assert.equal(config.showSequenceBatch, false);
    assert.equal(config.showMsaControls, false);
    assert.equal(config.forcedPredictor, 'boltz');
});

test('structure submit target preserves native predictor routing but forces boltz cp experimental onto its workflow identity', () => {
    const defaultConfig = resolveStructureLaunchConfig({ template_model_id: 'boltz2' });
    assert.deepEqual(
        resolveStructureSubmitTarget({
            launchConfig: defaultConfig,
            predictionMode: 'complex',
            predictorSelection: 'protenix',
        }),
        { modelId: 'protenix', mode: 'complex' },
    );

    const cpConfig = resolveStructureLaunchConfig({
        template_model_id: 'boltz_cp_experimental',
        structure_launch_variant: 'boltz_cp_experimental',
    });
    assert.deepEqual(
        resolveStructureSubmitTarget({
            launchConfig: cpConfig,
            predictionMode: 'complex',
            predictorSelection: 'boltz',
        }),
        { modelId: 'boltz_cp_experimental', mode: 'design' },
    );
});

test('boltz cp gpu launch settings use pinned gpus directly and clamp size_cp to a valid square divisor', () => {
    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [0, 1, 2, 3], requestedSizeCp: undefined }),
        { gpuIds: '0,1,2,3', sizeCp: 4 },
    );

    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [0, 2, 3], requestedSizeCp: undefined }),
        { gpuIds: '0,2,3', sizeCp: 1 },
    );

    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [], requestedSizeCp: 9, fallbackGpuIds: '0,1,2,3' }),
        { gpuIds: '0,1,2,3', sizeCp: 4 },
    );
});

test('boltz cp submit params expose only the workflow-specific knobs on top of shared structure inputs', () => {
    assert.deepEqual(
        buildBoltzCpSubmitParams({
            outputFormat: 'pdb',
            writeFullPae: true,
            seed: '17',
            gpuIds: '0,1,2,3',
            sizeCp: 4,
        }),
        {
            structure_launch_variant: 'boltz_cp_experimental',
            num_parallel_jobs: 1,
            bcp_input_format: 'config_files',
            bcp_output_format: 'pdb',
            bcp_write_full_pae: true,
            bcp_gpu_ids: '0,1,2,3',
            bcp_size_cp: 4,
            bcp_seed: 17,
        },
    );

    assert.deepEqual(
        buildBoltzCpSubmitParams({
            outputFormat: 'mmcif',
            writeFullPae: false,
            seed: '  ',
            gpuIds: '',
            sizeCp: 1,
        }),
        {
            structure_launch_variant: 'boltz_cp_experimental',
            num_parallel_jobs: 1,
            bcp_input_format: 'config_files',
            bcp_output_format: 'mmcif',
            bcp_write_full_pae: false,
            bcp_size_cp: 1,
        },
    );
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
