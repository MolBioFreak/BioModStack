import assert from 'node:assert/strict';
import test from 'node:test';
import { MSA_POLICY, resolveMsaSearchBackend } from '../src/lib/msaPolicy';
import { buildStructureMsaSubmitParams } from '../src/components/structurePredictionUiState';

test('canonical policy resolves auto visibly and rejects saved local', () => {
    assert.equal(MSA_POLICY.enabled_search_backend, 'colabfold_api');
    assert.equal(resolveMsaSearchBackend('auto'), 'colabfold_api');
    assert.throws(() => resolveMsaSearchBackend('local'), /re-preview/);
    assert.match(MSA_POLICY.disclosure, /third party/);
});

test('real structure preview/submit compiler rejects local without rewriting', () => {
    const settings = { provider: 'local' as const, preset: 'fast' as const,
        targetShardMode: 'auto' as const, targetShards: 4, targetShardMinSizeGb: 1 };
    assert.throws(() => buildStructureMsaSubmitParams(settings), /Local MSA search is disabled/);
    assert.equal(settings.provider, 'local');
    const effective = buildStructureMsaSubmitParams({ ...settings, provider: 'colabfold_api' });
    assert.equal(effective.msa_provider, 'colabfold_api');
    assert.equal('msa_target_shards' in effective, false);
});
