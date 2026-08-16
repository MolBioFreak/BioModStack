import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const structureSource = readFileSync(new URL('../src/components/StructurePredictionTemplate.tsx', import.meta.url), 'utf8');
const apiSource = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');

test('Structure Prediction exposes Boltz API as a real remote submission backend', () => {
    assert.match(structureSource, /data-bms-boltz-api-submit/);
    assert.match(structureSource, /Boltz API submission/);
    assert.match(structureSource, /Estimate API cost/);
    assert.match(structureSource, /Queue Boltz API job/);
    assert.match(structureSource, /submitBoltzApiJob/);
    assert.match(structureSource, /buildBoltzApiStructureRequest\(\{/);
    assert.match(structureSource, /BoltzApiNativeSettingsPanel/);
    assert.match(structureSource, /showParallelJobs && !isBoltzApi/);
    assert.match(structureSource, /showSequenceBatch && !isBoltzApi/);
    assert.match(structureSource, /!isBoltzApi && <div[^>]+>[\s\S]*?Frustration analysis[\s\S]*?Allow Retries[\s\S]*?<\/div>}/);
    assert.doesNotMatch(structureSource, /BoltzApiImportPanel|Import downloaded Boltz API result/);
});

test('Boltz API submission estimates cost, requires approval, and posts to the dedicated remote queue endpoint', () => {
    assert.match(structureSource, /estimateBoltzApiJob/);
    assert.match(structureSource, /approved_estimate_fingerprint/);
    assert.match(structureSource, /I approve this provider estimate/);
    assert.match(apiSource, /\/api\/jobs\/boltz-api\/estimate/);
    assert.match(apiSource, /\/api\/jobs\/boltz-api'/);
    assert.doesNotMatch(structureSource, /previewExternalImport|createExternalImport|data\/boltz_results/);
});
