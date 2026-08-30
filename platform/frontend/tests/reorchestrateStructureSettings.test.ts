import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    buildStructureReorchestrateOverrides,
    deriveStructureReorchestrateSettings,
    isStructureReorchestrateJob,
} from '../src/components/dashboard/reorchestrateStructureSettings.js';

test('dashboard hydrates a summary row before opening re-orchestration settings', () => {
    const dashboardSource = readFileSync(
        new URL('../src/components/Dashboard.tsx', import.meta.url),
        'utf8',
    );
    assert.match(dashboardSource, /const handleResumeWithSettings = async \(job: Job\)/);
    assert.match(dashboardSource, /detailedJob = await hydrateJobForDetail\(job\)/);
    assert.match(dashboardSource, /setResumeSettingsJob\(detailedJob\)/);
    assert.match(dashboardSource, /deriveStructureReorchestrateSettings\(detailedJob\)/);
});

test('always submits the visible MSA provider when re-orchestrating from a summary row', () => {
    const summaryJob = {
        model_id: 'boltz_cp_experimental',
        mode: 'design',
        params: {},
    };

    const settings = deriveStructureReorchestrateSettings(summaryJob);
    assert.equal(settings.msaProvider, 'colabfold_api');
    assert.deepEqual(buildStructureReorchestrateOverrides(summaryJob, settings), {
        msa_provider: 'colabfold_api',
    });
});

test('recognizes structure-validation jobs and derives boltz retry controls from current params', () => {
    const job = {
        model_id: 'boltz2',
        mode: 'complex',
        params: {
            pred_method: 'boltz',
            structure_validator: 'boltz2',
            msa_provider: 'local',
            msa_preset: 'fast',
            msa_target_shard_mode: 'off',
            msa_target_shards: 2,
            msa_target_shard_min_size_gb: 0.5,
            boltz_use_msa: true,
            boltz_sampling_steps: 200,
            boltz_recycling_steps: 10,
            boltz_num_samples: 1,
            boltz_max_parallel_samples: 1,
            boltz_use_potentials: true,
        },
    };

    assert.equal(isStructureReorchestrateJob(job), true);

    const settings = deriveStructureReorchestrateSettings(job);
    assert.deepEqual(settings.predictors, ['boltz']);
    assert.equal(settings.msaProvider, 'local');
    assert.equal(settings.skipMsa, false);
    assert.equal(settings.msaPreset, 'fast');
    assert.equal(settings.msaTargetShardMode, 'off');
    assert.equal(settings.msaTargetShards, 2);
    assert.equal(settings.msaTargetShardMinSizeGb, 0.5);
    assert.equal(settings.boltz.samplingSteps, 200);
    assert.equal(settings.boltz.recyclingSteps, 10);
    assert.equal(settings.boltz.numSamples, 1);
    assert.equal(settings.boltz.maxParallelSamples, 1);
    assert.equal(settings.boltz.usePotentials, true);
});

test('builds focused overrides for re-orchestrating a boltz structure run', () => {
    const job = {
        model_id: 'boltz2',
        mode: 'complex',
        params: {
            pred_method: 'boltz',
            structure_validator: 'boltz2',
            msa_provider: 'local',
            msa_preset: 'fast',
            msa_allow_empty_fallback: false,
            boltz_use_msa: true,
            boltz_sampling_steps: 200,
            boltz_recycling_steps: 10,
            boltz_num_samples: 1,
            boltz_max_parallel_samples: 1,
            boltz_use_potentials: true,
        },
    };

    const settings = deriveStructureReorchestrateSettings(job);
    const overrides = buildStructureReorchestrateOverrides(job, {
        ...settings,
        msaProvider: 'colabfold_api',
        skipMsa: true,
        msaAllowEmptyFallback: true,
        msaTargetShardMode: 'required',
        msaTargetShards: 2,
        msaTargetShardMinSizeGb: 0,
        boltz: {
            ...settings.boltz,
            samplingSteps: 400,
            numSamples: 4,
            maxParallelSamples: 2,
        },
    });

    assert.deepEqual(overrides, {
        msa_provider: 'colabfold_api',
        msa_allow_empty_fallback: true,
        msa_target_shard_mode: 'required',
        msa_target_shards: 2,
        msa_target_shard_min_size_gb: 0,
        boltz_use_msa: false,
        boltz_sampling_steps: 400,
        boltz_num_samples: 4,
        boltz_max_parallel_samples: 2,
    });
});

test('marks multi-predictor structure jobs as relevant and exposes only the active model families', () => {
    const job = {
        model_id: 'boltz2',
        mode: 'predict',
        params: {
            pred_method: 'all',
            msa_provider: 'local',
            boltz_use_msa: true,
            rf3_use_msa: true,
            protenix_use_msa: false,
        },
    };

    const settings = deriveStructureReorchestrateSettings(job);
    assert.equal(isStructureReorchestrateJob(job), true);
    assert.deepEqual(settings.predictors, ['boltz', 'rf3', 'protenix']);
    assert.equal(settings.skipMsa, false);
});

test('normalizes legacy complex all runs to boltz plus protenix for re-orchestration', () => {
    const job = {
        model_id: 'boltz2',
        mode: 'complex',
        params: {
            pred_method: 'all',
            boltz_use_msa: true,
            protenix_use_msa: true,
        },
    };

    const settings = deriveStructureReorchestrateSettings(job);
    assert.deepEqual(settings.predictors, ['boltz', 'protenix']);
    assert.equal(settings.msaProvider, 'colabfold_api');
});

test('uses the 200-step Boltz default when retry metadata omitted sampling steps', () => {
    const job = {
        model_id: 'boltz2',
        mode: 'complex',
        params: {
            pred_method: 'boltz',
            boltz_use_msa: true,
        },
    };

    const settings = deriveStructureReorchestrateSettings(job);
    assert.equal(settings.boltz.samplingSteps, 200);
    assert.equal(settings.boltz.recyclingSteps, 3);
});

test('derives Boltz-CP re-orchestrate settings from a prior CP launch', () => {
    const job = {
        model_id: 'boltz_cp_experimental',
        mode: 'design',
        params: {
            structure_launch_variant: 'boltz_cp_experimental',
            bcp_shard_plan_id: '4x4',
            pinned_gpus: [2, 3],
            lock_gpus: true,
            bcp_size_cp: 1,
            bcp_output_format: 'pdb',
            bcp_write_full_pae: true,
            bcp_seed: 17,
            boltz_sampling_steps: 200,
        },
    };

    const settings = deriveStructureReorchestrateSettings(job);
    assert.equal(settings.boltzCp.enabled, true);
    assert.deepEqual(settings.boltzCp.pinnedGpus, [2, 3]);
    assert.equal(settings.boltzCp.lockGpus, true);
    assert.equal(settings.boltzCp.sizeCp, 1);
    assert.equal(settings.boltzCp.outputFormat, 'pdb');
    assert.equal(settings.boltzCp.writeFullPae, true);
    assert.equal(settings.boltzCp.seed, '17');
});

test('builds Boltz-CP overrides using OEM square-divisor sizing', () => {
    const job = {
        model_id: 'boltz_cp_experimental',
        mode: 'design',
        params: {
            structure_launch_variant: 'boltz_cp_experimental',
            pinned_gpus: [0, 1, 2, 3],
            lock_gpus: true,
            bcp_shard_plan_id: '2x2',
            bcp_gpu_ids: '0,1,2,3',
            bcp_size_cp: 4,
            bcp_output_format: 'mmcif',
            bcp_write_full_pae: false,
        },
    };

    const settings = deriveStructureReorchestrateSettings(job);
    const overrides = buildStructureReorchestrateOverrides(job, {
        ...settings,
        boltzCp: {
            ...settings.boltzCp,
            pinnedGpus: [2, 3],
            sizeCp: 16,
        },
    });

    assert.deepEqual(overrides.pinned_gpus, [2, 3]);
    assert.equal(overrides.bcp_gpu_ids, '2,3');
    assert.equal(overrides.bcp_size_cp, 1);
    assert.equal(Object.prototype.hasOwnProperty.call(overrides, 'bcp_shard_plan_id'), false);
});

test('clears Boltz-CP pinning and seed overrides without reviving shard plans', () => {
    const job = {
        model_id: 'boltz_cp_experimental',
        mode: 'design',
        params: {
            structure_launch_variant: 'boltz_cp_experimental',
            bcp_shard_plan_id: '4x4',
            pinned_gpus: [2, 3],
            lock_gpus: true,
            bcp_gpu_ids: '2,3',
            bcp_size_cp: 1,
            bcp_output_format: 'pdb',
            bcp_write_full_pae: true,
            bcp_seed: 17,
        },
    };

    const settings = deriveStructureReorchestrateSettings(job);
    const overrides = buildStructureReorchestrateOverrides(job, {
        ...settings,
        boltzCp: {
            ...settings.boltzCp,
            pinnedGpus: [],
            lockGpus: false,
            sizeCp: 4,
            outputFormat: 'mmcif',
            writeFullPae: false,
            seed: '',
        },
    });

    assert.equal(overrides.pinned_gpus, null);
    assert.equal(overrides.lock_gpus, false);
    assert.equal(overrides.bcp_gpu_ids, null);
    assert.equal(Object.prototype.hasOwnProperty.call(overrides, 'bcp_shard_plan_id'), false);
    assert.equal(overrides.bcp_output_format, 'mmcif');
    assert.equal(overrides.bcp_write_full_pae, false);
    assert.equal(overrides.bcp_seed, null);
});
