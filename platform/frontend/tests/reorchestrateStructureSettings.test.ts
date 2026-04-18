import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildStructureReorchestrateOverrides,
    deriveStructureReorchestrateSettings,
    isStructureReorchestrateJob,
} from '../src/components/dashboard/reorchestrateStructureSettings.js';

test('recognizes structure-validation jobs and derives boltz retry controls from current params', () => {
    const job = {
        model_id: 'boltz2',
        mode: 'complex',
        params: {
            pred_method: 'boltz',
            structure_validator: 'boltz2',
            msa_provider: 'local',
            msa_preset: 'fast',
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
