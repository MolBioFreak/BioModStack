import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    DEFAULT_STRUCTURE_MSA_PROVIDER,
    BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT,
    BOLTZ_NUM_SAMPLES_HELP_TEXT,
    buildBoltzCpSubmitParams,
    buildStructureMsaSubmitParams,
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
    buildStructureFrustraMpnnSubmitParams,
    resolveTargetPreviewSource,
} from '../src/components/structurePredictionUiState.js';
import { CANONICAL_FRUSTRAMPNN_SETTINGS } from '../src/components/frustrampnn/frustraMpnnSettingsState.js';

test('structure workflow cards request canonical FrustraMPNN by default and preserve an explicit operator opt-out', () => {
    const enabled = {
        run_frustrampnn: true,
        frustrampnn_requiredness: 'required',
        frustrampnn_settings: CANONICAL_FRUSTRAMPNN_SETTINGS,
    };
    assert.deepEqual(buildStructureFrustraMpnnSubmitParams(undefined, CANONICAL_FRUSTRAMPNN_SETTINGS), enabled);
    assert.deepEqual(buildStructureFrustraMpnnSubmitParams(true, CANONICAL_FRUSTRAMPNN_SETTINGS), enabled);
    assert.deepEqual(buildStructureFrustraMpnnSubmitParams(false, CANONICAL_FRUSTRAMPNN_SETTINGS), {
        run_frustrampnn: false,
        frustrampnn_requiredness: 'required',
    });
});

test('structure prediction defaults new MSA submissions to the ColabFold server', () => {
    assert.equal(DEFAULT_STRUCTURE_MSA_PROVIDER, 'colabfold_api');
    const templateSource = readFileSync(
        new URL('../src/components/StructurePredictionTemplate.tsx', import.meta.url),
        'utf8',
    );
    assert.doesNotMatch(
        templateSource,
        /if \(numParallelJobs > 1 && msaProvider === 'colabfold_api'\) \{\s*setMsaProvider\('local'\);/,
    );
    assert.match(templateSource, /Local MMseqs2 \(manual override\)/);
    assert.match(templateSource, /ColabFold API \(default; single-job only\)/);
    assert.doesNotMatch(templateSource, /Local MMseqs2 \(recommended\)/);
});

type PredictorOption = {
    id: string;
    disabled?: boolean;
    disabledReason?: string;
};

test('predict mode keeps all surfaced predictor combinations available', () => {
    const options = getStructurePredictorOptions('predict');

    assert.deepEqual(options.map((option: PredictorOption) => option.id), ['boltz', 'fold_cp', 'boltz_api', 'rf3', 'protenix', 'esmfold2', 'both', 'all']);
    assert.equal(options.every((option: PredictorOption) => option.disabled !== true), true);
});

test('complex mode only exposes truthful predictor choices and disables RF3 explicitly', () => {
    const options = getStructurePredictorOptions('complex');
    const rf3Option = options.find((option: PredictorOption) => option.id === 'rf3');

    assert.deepEqual(options.map((option: PredictorOption) => option.id), ['boltz', 'fold_cp', 'boltz_api', 'rf3', 'protenix', 'esmfold2', 'boltz_protenix']);
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

test('legacy boltz cp jobs reopen as an editable Fold-CP predictor inside Structure Prediction', () => {
    const config = resolveStructureLaunchConfig({
        template_model_id: 'boltz_cp_experimental',
        structure_launch_variant: 'boltz_cp_experimental',
    });

    assert.equal(config.variant, 'boltz_cp_experimental');
    assert.equal(config.submitModelId, 'boltz_cp_experimental');
    assert.equal(config.submitMode, 'design');
    assert.equal(config.allowPredictorSelection, true);
    assert.equal(config.showParallelJobs, false);
    assert.equal(config.showSequenceBatch, false);
    assert.equal(config.showMsaControls, true);
    assert.equal(config.forcedPredictor, 'fold_cp');
});

test('Fold-CP is a normal Structure predictor with its own execution identity', () => {
    const options = getStructurePredictorOptions('predict');
    const foldCp = options.find((option) => option.id === 'fold_cp');
    assert.equal(foldCp?.name, 'NVIDIA Fold-CP');

    const config = resolveStructureLaunchConfig();
    assert.deepEqual(
        resolveStructurePredictorSelection('predict', 'fold_cp'),
        {
            requestedSelection: 'fold_cp',
            canonicalSelection: 'fold_cp',
            families: ['fold_cp'],
            valid: true,
        },
    );
    assert.deepEqual(
        resolveStructureSubmitTarget({
            launchConfig: config,
            predictionMode: 'predict',
            predictorSelection: 'fold_cp',
        }),
        { modelId: 'boltz_cp_experimental', mode: 'design' },
    );
});

test('esmfold2 compatibility IDs no longer create dedicated structure launch variants', () => {
    for (const compatibilityId of ['esmfold2', 'esmfold2_experimental']) {
        const config = resolveStructureLaunchConfig({
            template_model_id: compatibilityId,
            structure_launch_variant: compatibilityId,
        });
        assert.equal(config.variant, 'default');
        assert.equal(config.submitModelId, 'boltz2');
        assert.equal(config.allowPredictorSelection, true);
        assert.equal(config.forcedPredictor, null);
    }
    assert.deepEqual(getPredictorFamiliesForSelection('complex', 'esmfold2'), ['esmfold2']);
});

test('structure submit target routes the selected Fold-CP predictor onto its execution identity', () => {
    const defaultConfig = resolveStructureLaunchConfig({ template_model_id: 'boltz2' });
    assert.deepEqual(
        resolveStructureSubmitTarget({
            launchConfig: defaultConfig,
            predictionMode: 'complex',
            predictorSelection: 'boltz_api',
        }),
        { modelId: 'boltz_api', mode: 'complex' },
    );
    assert.deepEqual(
        resolveStructureSubmitTarget({
            launchConfig: defaultConfig,
            predictionMode: 'complex',
            predictorSelection: 'protenix',
        }),
        { modelId: 'protenix', mode: 'complex' },
    );
    assert.deepEqual(
        resolveStructureSubmitTarget({
            launchConfig: defaultConfig,
            predictionMode: 'complex',
            predictorSelection: 'esmfold2',
        }),
        { modelId: 'esmfold2', mode: 'complex' },
    );

    const cpConfig = resolveStructureLaunchConfig({
        template_model_id: 'boltz_cp_experimental',
        structure_launch_variant: 'boltz_cp_experimental',
    });
    assert.deepEqual(
        resolveStructureSubmitTarget({
            launchConfig: cpConfig,
            predictionMode: 'complex',
            predictorSelection: 'fold_cp',
        }),
        { modelId: 'boltz_cp_experimental', mode: 'design' },
    );

});

test('esmfold2 controls are driven by the parent structure predictor selection', () => {
    const source = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');

    assert.match(source, /usesEsmFold2 = predictorFamilies\.includes\('esmfold2'\)/);
    assert.match(source, /params\.model_variant = esmfold2Variant/);
    assert.match(source, /params\.local_files_only = true/);
    assert.doesNotMatch(source, /isEsmFold2Launch/);
    assert.doesNotMatch(source, /structure_launch_variant = launchConfig\.variant/);
    assert.match(source, /PDB coordinates are not structural templates/);
});

test('boltz cp gpu launch settings use pinned gpus directly and clamp size_cp to a valid square divisor', () => {
    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [0, 1, 2, 3], requestedSizeCp: 4 }),
        { gpuIds: '0,1,2,3', sizeCp: 4 },
    );

    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [2, 3], requestedSizeCp: 16 }),
        { gpuIds: '2,3', sizeCp: 1 },
    );

    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [0, 2, 3], requestedSizeCp: undefined }),
        { gpuIds: '0,2,3', sizeCp: 1 },
    );

    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [], requestedSizeCp: 16, fallbackGpuIds: '0,1,2,3' }),
        { gpuIds: '0,1,2,3', sizeCp: 4 },
    );
});

test('boltz cp submit params use the OEM context-parallel contract', () => {
    assert.deepEqual(
        buildBoltzCpSubmitParams({
            outputFormat: 'pdb',
            writeFullPae: true,
            seed: '17',
            gpuIds: '0,1,2,3',
            sizeCp: 4,
        }),
        {
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
            num_parallel_jobs: 1,
            bcp_input_format: 'config_files',
            bcp_output_format: 'mmcif',
            bcp_write_full_pae: false,
            bcp_size_cp: 1,
        },
    );
});

test('boltz cp template exposes only the OEM context-parallel control', () => {
    const componentText = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');

    assert.match(componentText, /Context Parallel Size Request/);
    assert.doesNotMatch(componentText, /Logical shard plan/);
    assert.doesNotMatch(componentText, /Triangle attention query tile/);
});

test('structure MSA submit params carry adaptive target-DB sharding controls for local high-quality runs', () => {
    assert.deepEqual(
        buildStructureMsaSubmitParams({
            provider: 'local',
            preset: 'balanced',
            targetShardMode: 'auto',
            targetShards: 4,
            targetShardMinSizeGb: 1,
        }),
        {
            msa_provider: 'local',
            msa_preset: 'balanced',
            msa_target_shard_mode: 'auto',
            msa_target_shards: 4,
            msa_target_shard_min_size_gb: 1,
        },
    );

    assert.deepEqual(
        buildStructureMsaSubmitParams({
            provider: 'local',
            preset: 'maximum',
            targetShardMode: 'off',
            targetShards: 2,
            targetShardMinSizeGb: 0,
        }),
        {
            msa_provider: 'local',
            msa_preset: 'maximum',
            msa_target_shard_mode: 'off',
            msa_target_shards: 2,
            msa_target_shard_min_size_gb: 0,
        },
    );
});

test('structure MSA submit params leave ColabFold API provider free of local target-sharding knobs', () => {
    assert.deepEqual(
        buildStructureMsaSubmitParams({
            provider: 'colabfold_api',
            preset: 'fast',
            targetShardMode: 'required',
            targetShards: 8,
            targetShardMinSizeGb: 0,
        }),
        {
            msa_provider: 'colabfold_api',
            msa_preset: 'fast',
        },
    );
});

test('structure prediction template wires adaptive target sharding through state, template save, submit, and UI surfaces', () => {
    const source = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');

    assert.match(source, /const \[msaTargetShardMode, setMsaTargetShardMode\]/);
    assert.match(source, /const \[msaTargetShards, setMsaTargetShards\]/);
    assert.match(source, /const \[msaTargetShardMinSizeGb, setMsaTargetShardMinSizeGb\]/);
    assert.equal((source.match(/buildStructureMsaSubmitParams\(/g) || []).length, 2);
    assert.match(source, /EnvDB Target Sharding/);
    assert.match(source, /Auto for balanced\/maximum/);
    assert.match(source, /Off \/ unsharded fallback/);
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

test('boltz sampling copy distinguishes final candidate count from denoiser chunking', () => {
    assert.match(BOLTZ_NUM_SAMPLES_HELP_TEXT, /final ranked candidate/i);
    assert.match(BOLTZ_NUM_SAMPLES_HELP_TEXT, /model_0/i);
    assert.match(BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT, /denoiser-forward/i);
    assert.doesNotMatch(BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT, /serial/i);
    assert.doesNotMatch(BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT, /independent/i);
});

test('structure prediction launcher wires the corrected boltz sampling copy', () => {
    const source = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');
    assert.match(source, /BOLTZ_NUM_SAMPLES_HELP_TEXT/);
    assert.match(source, /BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT/);
    assert.doesNotMatch(source, /1 = serial/);
});

test('boltz potentials UI copy avoids blanket more-accurate wording on active launcher surfaces', () => {
    const structureSource = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');
    const mutagenesisSource = readFileSync('src/components/MutagenesisTemplate.tsx', 'utf8');
    assert.match(structureSource, /physics\/FK steering potentials/);
    assert.match(mutagenesisSource, /physics\/FK steering potentials/);
    assert.doesNotMatch(structureSource, /More accurate but slower/);
    assert.doesNotMatch(mutagenesisSource, /More accurate but slower/);
});
