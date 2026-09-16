import assert from 'node:assert/strict';
import { test } from 'node:test';
import { hydrateEsmfold2Settings, buildEsmfold2Params, esmfold2SettingsError, ESMFOLD2_PRESETS } from '../src/components/esmfold2Settings';
import { deriveStructureReorchestrateSettings, buildStructureReorchestrateOverrides } from '../src/components/dashboard/reorchestrateStructureSettings';

test('legacy omissions retain actual closed compiler defaults, explicit standard is separate', () => {
    const fresh = hydrateEsmfold2Settings();
    assert.deepEqual([fresh.num_loops, fresh.num_sampling_steps, fresh.num_diffusion_samples, fresh.use_msa], [3, 50, 1, false]);
    const old = hydrateEsmfold2Settings({ model_variant: 'full' });
    assert.deepEqual([old.num_loops, old.num_sampling_steps, old.num_diffusion_samples, old.use_msa], [3, 50, 1, false]);
    assert.equal(old.quality_preset, 'custom');
    assert.equal(hydrateEsmfold2Settings({ quality_preset: 'standard' }).num_sampling_steps, 50);
});
test('presets match compiler and canonical controls override saved aliases/presets without clamping', () => {
    for (const [quality_preset, expected] of Object.entries(ESMFOLD2_PRESETS)) {
        const settings = hydrateEsmfold2Settings({ quality_preset });
        for (const [key, value] of Object.entries(expected)) assert.equal(settings[key as keyof typeof settings], value);
    }
    const settings = hydrateEsmfold2Settings({ quality_preset: 'thorough', esmf_num_diffusion_samples: 7, num_diffusion_samples: 1, esmf_seed: 0, esmf_use_msa: true });
    assert.equal(settings.num_diffusion_samples, 1); assert.equal(settings.seed, 0);
    assert.equal(hydrateEsmfold2Settings({ seed: null, esmf_seed: 42 }).seed, null);
    const params = buildEsmfold2Params(settings);
    assert.equal(params.esmf_use_msa, true); assert.equal(params.use_msa, undefined);
    assert.deepEqual(hydrateEsmfold2Settings(params), settings);
});
test('Fast MSA conditioning is rejected without changing saved intent', () => {
    const settings = hydrateEsmfold2Settings({ esmf_use_msa: true, model_variant: 'fast' });
    assert.match(esmfold2SettingsError(settings)!, /Choose Full/);
    assert.equal(settings.use_msa, true); assert.equal(settings.model_variant, 'fast');
    assert.equal(esmfold2SettingsError({ ...settings, model_variant: 'full' }), null);
});
test('bounds reject invalid draft values rather than silently lowering scientific intent', () => {
    for (const [key, min, max] of [['num_loops', 1, 12], ['num_sampling_steps', 1, 1000], ['num_diffusion_samples', 1, 8], ['seed', 0, 2147483647], ['msa_max_sequences', 1, 10000]] as const) {
        for (const n of [min, max]) assert.equal(esmfold2SettingsError({ ...hydrateEsmfold2Settings(), [key]: n }), null);
        for (const n of [min - 1, max + 1, 1.5, NaN]) assert.match(esmfold2SettingsError({ ...hydrateEsmfold2Settings(), [key]: n })!, new RegExp(key));
    }
});
test('retry hydrates MSA enabled and keeps one sample while changing explicit settings', () => {
    const job = { model_id: 'esmfold2', mode: 'predict', params: { model_variant: 'full', esmf_use_msa: true, msa_provider: 'neurosnap_api', num_loops: 12, num_sampling_steps: 1000, num_diffusion_samples: 1, quality_preset: 'custom' } };
    const settings = deriveStructureReorchestrateSettings(job);
    assert.equal(settings.skipMsa, false);
    const unchanged = buildStructureReorchestrateOverrides(job, settings);
    assert.equal(unchanged.num_diffusion_samples, undefined);
    const next = { ...settings, skipMsa: true, esmfold2: { ...settings.esmfold2, seed: 0 } };
    const overrides = buildStructureReorchestrateOverrides(job, next);
    assert.equal(overrides.esmf_use_msa, false); assert.equal(overrides.seed, 0);
    const remount = hydrateEsmfold2Settings({ ...job.params, ...overrides });
    assert.equal(remount.num_diffusion_samples, 1); assert.equal(remount.num_sampling_steps, 1000);
});
