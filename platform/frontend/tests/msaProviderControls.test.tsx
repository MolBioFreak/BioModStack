import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ColabfoldMsaControls } from '../src/components/ColabfoldMsaControls';
import { NeurosnapMsaControls } from '../src/components/NeurosnapMsaControls';
import { hydrateColabfoldMsaSettings, MSA_POLICY, hydrateMsaProvider, hydrateNeurosnapMsaSettings, resolveMsaSearchBackend } from '../src/lib/msaPolicy';
import { buildStructureMsaSubmitParams } from '../src/components/structurePredictionUiState';
import { deriveStructureReorchestrateSettings, buildStructureReorchestrateOverrides } from '../src/components/dashboard/reorchestrateStructureSettings';

test('both providers and historical local/auto intent survive hydration', () => {
    for (const provider of ['auto', 'local', 'colabfold_api', 'neurosnap_api']) assert.equal(hydrateMsaProvider(provider), provider);
    assert.equal(resolveMsaSearchBackend('auto'), 'colabfold_api');
    assert.equal(resolveMsaSearchBackend('neurosnap_api'), 'neurosnap_api');
    assert.throws(() => resolveMsaSearchBackend('local'), /disabled/);
    assert.throws(() => hydrateMsaProvider('unknown'), /Unsupported/);
});

test('all native values including unsupported flags survive save clone retry', () => {
    const native = hydrateNeurosnapMsaSettings({ msa_neurosnap_coverage_percent: 42.5, msa_neurosnap_identity_percent: 65, msa_neurosnap_max_sequences: 321, msa_neurosnap_force_uppercase: true, msa_neurosnap_pad_sequences: true });
    const saved = buildStructureMsaSubmitParams({ provider: 'neurosnap_api', preset: 'maximum', neurosnap: native });
    assert.deepEqual(saved, { msa_provider: 'neurosnap_api', ...native });
    const job = { model_id: 'protenix', mode: 'predict', params: JSON.parse(JSON.stringify(saved)) };
    const retry = deriveStructureReorchestrateSettings(job);
    assert.equal(retry.msaProvider, 'neurosnap_api');
    assert.deepEqual(retry.neurosnapMsa, native);
    const overrides = buildStructureReorchestrateOverrides(job, retry);
    assert.deepEqual(overrides, saved);
    assert.deepEqual(hydrateNeurosnapMsaSettings(native), native);
    assert.equal(hydrateNeurosnapMsaSettings({ msa_neurosnap_coverage_percent: 200 }).msa_neurosnap_coverage_percent, 200);
});

test('defaults and typed visible controls share the canonical schema', () => {
    const defaults = hydrateNeurosnapMsaSettings({});
    const html = renderToStaticMarkup(<NeurosnapMsaControls value={defaults} onChange={() => {}} />);
    for (const [key, field] of Object.entries(MSA_POLICY.neurosnap_settings)) {
        assert.equal(defaults[key as keyof typeof defaults], field.default);
        assert.ok(html.includes(field.native_name));
    }
    assert.equal((html.match(/type="number"/g) || []).length, 3);
    assert.equal((html.match(/type="checkbox"/g) || []).length, 2);
    assert.ok(html.includes('min="10"'));
    assert.ok(html.includes('max="1000000"'));
});

test('old ColabFold requests keep settings and omit local sharding and Neurosnap fields', () => {
    assert.deepEqual(buildStructureMsaSubmitParams({ provider: 'colabfold_api', preset: 'maximum', targetShards: 4 }), { msa_provider: 'colabfold_api', msa_preset: 'maximum' });
    assert.throws(() => buildStructureMsaSubmitParams({ provider: 'local', preset: 'fast' }), /disabled/);
});


test('ColabFold native controls preserve booleans, enums and retry values', () => {
    const value = hydrateColabfoldMsaSettings({ colabfold_use_env: false, colabfold_use_filter: false, colabfold_use_templates: true, colabfold_pairing_mode: 'unpaired_paired', colabfold_pairing_strategy: 'complete' });
    const html = renderToStaticMarkup(<ColabfoldMsaControls value={value} onChange={() => {}} />);
    assert.equal((html.match(/type="checkbox"/g) || []).length, 3);
    assert.equal((html.match(/<select/g) || []).length, 2);
    const params = buildStructureMsaSubmitParams({ provider: 'auto', preset: 'fast', colabfold: value });
    assert.equal(params.msa_provider, 'auto');
    const job = { model_id: 'boltz2', mode: 'predict', params };
    const next = deriveStructureReorchestrateSettings(job);
    assert.deepEqual(next.colabfoldMsa, value);
    const retry = buildStructureReorchestrateOverrides(job, next);
    assert.deepEqual(retry, { msa_provider: 'auto', ...value });
    assert.equal(hydrateColabfoldMsaSettings({ msa_use_env: false }).colabfold_use_env, false);
});


test('switching provider preserves inactive native values in saved requests', () => {
    const neurosnap = hydrateNeurosnapMsaSettings({ msa_neurosnap_identity_percent: 70 });
    const colabfold = hydrateColabfoldMsaSettings({ colabfold_use_env: false });
    for (const provider of ['colabfold_api', 'neurosnap_api'] as const) {
        const saved = buildStructureMsaSubmitParams({ provider, preset: 'balanced', neurosnap, colabfold });
        assert.deepEqual(hydrateNeurosnapMsaSettings(saved), neurosnap);
        assert.deepEqual(hydrateColabfoldMsaSettings(saved), colabfold);
    }
});
