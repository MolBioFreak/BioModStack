import assert from 'node:assert/strict';
import test from 'node:test';
import { buildFrustraMpnn3dModel, parseFrustraMpnnMultidimensionalPage } from '../src/components/frustraMpnnMultidimensionalModel.js';

const page = {
    schema_version: 'frustrampnn_multidimensional_v1',
    level: 'result',
    dimensions: [
        { id: 'mean_score', kind: 'number', unit: 'FrustraMPNN score', formula: 'mean' },
        { id: 'high_fraction', kind: 'fraction', unit: 'fraction', formula: 'high / scoreable' },
        { id: 'minimal_fraction', kind: 'fraction', unit: 'fraction', formula: 'minimal / scoreable' },
        { id: 'scoreable_fraction', kind: 'fraction', unit: 'fraction', formula: 'scoreable / slots' },
    ],
    total: 2,
    limit: 1000,
    offset: 0,
    next_offset: null,
    items: [
        { point_id: 'job-1:invoke-1', dataset_id: 'job-1', workflow_family: 'de_novo_nanobody', job_id: 'job-1', design_id: 'design-1', candidate_id: 'Nb-1', invocation_id: 'invoke-1', source_artifact_sha256: 'a'.repeat(64), checkpoint_sha256: 'b'.repeat(64), threshold_policy_id: 'v1', metrics: { mean_score: -0.2, high_fraction: 0.3, minimal_fraction: 0.2, scoreable_fraction: 1 } },
        { point_id: 'job-2:invoke-2', dataset_id: 'job-2', workflow_family: 'de_novo_nanobody', job_id: 'job-2', design_id: 'design-2', candidate_id: 'Nb-2', invocation_id: 'invoke-2', source_artifact_sha256: 'c'.repeat(64), checkpoint_sha256: 'd'.repeat(64), threshold_policy_id: 'v1', metrics: { mean_score: 0.4, high_fraction: 0.1, minimal_fraction: 0.6, scoreable_fraction: 0.95 } },
    ],
};

test('multidimensional page parser preserves traceable cross-dataset identities', () => {
    const parsed = parseFrustraMpnnMultidimensionalPage(page);
    assert.equal(parsed.items.length, 2);
    assert.equal(parsed.items[1].design_id, 'design-2');
    assert.equal(parsed.items[1].source_artifact_sha256, 'c'.repeat(64));
});

test('3D model maps selected declared dimensions without anonymous points', () => {
    const parsed = parseFrustraMpnnMultidimensionalPage(page);
    const model = buildFrustraMpnn3dModel(parsed, 'mean_score', 'high_fraction', 'minimal_fraction', 'scoreable_fraction');
    assert.deepEqual(model.x, [-0.2, 0.4]);
    assert.deepEqual(model.y, [0.3, 0.1]);
    assert.deepEqual(model.z, [0.2, 0.6]);
    assert.deepEqual(model.color, [1, 0.95]);
    assert.deepEqual(model.pointIds, ['job-1:invoke-1', 'job-2:invoke-2']);
    assert.match(model.hover[0], /job-1.*design-1.*Nb-1/s);
});

test('parser rejects non-finite metrics and untraceable points', () => {
    const invalidMetric = structuredClone(page);
    invalidMetric.items[0].metrics.mean_score = Number.NaN;
    assert.throws(() => parseFrustraMpnnMultidimensionalPage(invalidMetric), /finite/);
    const missingIdentity = structuredClone(page);
    missingIdentity.items[0].design_id = '';
    assert.throws(() => parseFrustraMpnnMultidimensionalPage(missingIdentity), /identity/);
});
