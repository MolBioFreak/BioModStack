import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    BOLTZ_CP_DEFAULT_SHARD_PLAN_ID,
    BOLTZ_CP_SHARD_PLAN_DEFINITIONS,
    DEFAULT_BOLTZ_CP_CONTEXT_QUERY_TILE_TOKENS,
    DEFAULT_STRUCTURE_MSA_PROVIDER,
    BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT,
    BOLTZ_NUM_SAMPLES_HELP_TEXT,
    buildBoltzCpSubmitParams,
    buildStructureMsaSubmitParams,
    buildTargetPreviewSelection,
    buildTargetPreviewSelections,
    deriveBoltzCpGpuLaunchSettings,
    getBoltzCpLogicalSizeCp,
    getBoltzCpRuntimeBridgeSummary,
    getBoltzQualityPresetValues,
    getBoltzQualitySliderState,
    getPredictorFamiliesForSelection,
    getStructurePredictorOptions,
    inferBoltzCpShardPlanId,
    inferTargetStructureFormat,
    resolveBoltzSamplingStepsFromSlider,
    resolveStructureLaunchConfig,
    resolveStructurePredictorSelection,
    resolveStructureSubmitTarget,
    resolveTargetPreviewSource,
} from '../src/components/structurePredictionUiState.js';

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

    assert.deepEqual(options.map((option: PredictorOption) => option.id), ['boltz', 'rf3', 'protenix', 'esmfold2', 'both', 'all']);
    assert.equal(options.every((option: PredictorOption) => option.disabled !== true), true);
});

test('complex mode only exposes truthful predictor choices and disables RF3 explicitly', () => {
    const options = getStructurePredictorOptions('complex');
    const rf3Option = options.find((option: PredictorOption) => option.id === 'rf3');

    assert.deepEqual(options.map((option: PredictorOption) => option.id), ['boltz', 'rf3', 'protenix', 'esmfold2', 'boltz_protenix']);
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

test('boltz cp experimental launch config keeps the structure template locked to single-fold boltz mode while exposing MSA controls', () => {
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
    assert.equal(config.showMsaControls, true);
    assert.equal(config.forcedPredictor, 'boltz');
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
            predictorSelection: 'boltz',
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
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [0, 1, 2, 3], requestedSizeCp: getBoltzCpLogicalSizeCp('2x2') }),
        { gpuIds: '0,1,2,3', sizeCp: 4 },
    );

    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [2, 3], requestedSizeCp: getBoltzCpLogicalSizeCp('4x4') }),
        { gpuIds: '2,3', sizeCp: 1 },
    );

    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [0, 2, 3], requestedSizeCp: undefined }),
        { gpuIds: '0,2,3', sizeCp: 1 },
    );

    assert.deepEqual(
        deriveBoltzCpGpuLaunchSettings({ pinnedGpus: [], requestedSizeCp: getBoltzCpLogicalSizeCp('4x4'), fallbackGpuIds: '0,1,2,3' }),
        { gpuIds: '0,1,2,3', sizeCp: 4 },
    );
});

test('boltz cp shard plan helpers expose stable logical topologies and non-collapsing plan descriptions', () => {
    assert.equal(BOLTZ_CP_DEFAULT_SHARD_PLAN_ID, '2x2');
    assert.equal(getBoltzCpLogicalSizeCp('1x1'), 1);
    assert.equal(getBoltzCpLogicalSizeCp('2x2'), 4);
    assert.equal(getBoltzCpLogicalSizeCp('4x4'), 16);
    assert.equal(inferBoltzCpShardPlanId(1), '1x1');
    assert.equal(inferBoltzCpShardPlanId(4), '2x2');
    assert.equal(inferBoltzCpShardPlanId(16), '4x4');
    assert.match(BOLTZ_CP_SHARD_PLAN_DEFINITIONS.find((plan) => plan.id === '4x4')?.description || '', /does not change with GPU count/i);
});

test('boltz cp runtime bridge summary makes the logical plan primary and bridge sizing secondary', () => {
    assert.equal(
        getBoltzCpRuntimeBridgeSummary({ shardPlanId: '4x4', gpuIds: '0,1,2,3', sizeCp: 4 }),
        'The selected logical plan stays 4x4 (16 logical shards); GPU count only affects the current runtime bridge. 0,1,2,3 → current physical launch = 4 CP ranks.',
    );
    assert.match(
        getBoltzCpRuntimeBridgeSummary({ shardPlanId: '2x2', gpuIds: '', sizeCp: 1 }),
        /selected logical plan stays 2x2/i,
    );
    assert.match(
        getBoltzCpRuntimeBridgeSummary({ shardPlanId: '2x2', gpuIds: '', sizeCp: 1 }),
        /auto-selected GPU pool → current physical launch = 1 CP rank/i,
    );
});

test('boltz cp submit params expose reference triangle query tiling default and override', () => {
    assert.equal(DEFAULT_BOLTZ_CP_CONTEXT_QUERY_TILE_TOKENS, 512);

    assert.deepEqual(
        buildBoltzCpSubmitParams({
            shardPlanId: '4x4',
            outputFormat: 'pdb',
            writeFullPae: true,
            seed: '17',
            gpuIds: '0,1,2,3',
            contextQueryTileTokens: 256,
        }),
        {
            structure_launch_variant: 'boltz_cp_experimental',
            num_parallel_jobs: 1,
            bcp_input_format: 'config_files',
            bcp_shard_plan_id: '4x4',
            bcp_output_format: 'pdb',
            bcp_write_full_pae: true,
            bcp_context_query_tile_tokens: 256,
            bcp_gpu_ids: '0,1,2,3',
            bcp_seed: 17,
        },
    );

    assert.deepEqual(
        buildBoltzCpSubmitParams({
            shardPlanId: '1x1',
            outputFormat: 'mmcif',
            writeFullPae: false,
            seed: '  ',
            gpuIds: '',
        }),
        {
            structure_launch_variant: 'boltz_cp_experimental',
            num_parallel_jobs: 1,
            bcp_input_format: 'config_files',
            bcp_shard_plan_id: '1x1',
            bcp_output_format: 'mmcif',
            bcp_write_full_pae: false,
            bcp_context_query_tile_tokens: 512,
        },
    );
});

test('boltz cp template component exposes query tiling as live UI control', () => {
    const componentText = readFileSync('src/components/StructurePredictionTemplate.tsx', 'utf8');

    assert.match(componentText, /Triangle attention query tile/);
    assert.match(componentText, /setBcpContextQueryTileTokens/);
    assert.match(componentText, /contextQueryTileTokens: bcpContextQueryTileTokens/);
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
