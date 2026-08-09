import assert from 'node:assert/strict';
import test from 'node:test';
import { buildFrustraMpnn3dModel, parseFrustraMpnnMultidimensionalPage } from '../src/components/frustraMpnnMultidimensionalModel.js';

const hash = (character: string) => character.repeat(64);
const dimension = (
    id: string,
    kind: 'number' | 'fraction' | 'count' | 'boolean' | 'identifier' | 'category',
    description: string | null = null,
    unit: string | null = null,
    formula: string | null = null,
) => ({ id, kind, description, unit, formula });

const identityDimensions = [
    dimension('dataset_id', 'identifier', 'Stable workflow-result dataset identity (parent job ID).'),
    dimension('workflow_family', 'category', 'Persisted parent workflow family.'),
    dimension('job_id', 'identifier', 'Scheduler-owned parent job identity.'),
    dimension('design_id', 'identifier', 'Persisted source Design identity.'),
    dimension('invocation_id', 'identifier', 'FrustraMPNN invocation identity within the parent job.'),
    dimension('configuration_id', 'identifier', 'Global FrustraMPNN configuration identity.'),
    dimension('configuration_sha256', 'identifier', 'Content hash of the global FrustraMPNN configuration.'),
    dimension('threshold_policy_id', 'identifier', 'Versioned classification threshold policy.'),
];
const resultDimensions = [
    ...identityDimensions,
    dimension('mean_score', 'number', null, 'FrustraMPNN score', 'mean of finite scoreable persisted slots'),
    dimension('native_score', 'number', null, 'FrustraMPNN score', 'mean of persisted native slots'),
    dimension('high_fraction', 'fraction', null, 'fraction', 'high-class scoreable slots / scoreable slots'),
    dimension('minimal_fraction', 'fraction', null, 'fraction', 'minimal-class scoreable slots / scoreable slots'),
    dimension('scoreable_fraction', 'fraction', null, 'fraction', 'scoreable slots / persisted slots'),
    dimension('slot_count', 'count', null, 'slots', 'persisted landscape rows'),
    dimension('residue_count', 'count', null, 'residues', 'distinct exact author residue identities'),
];
const basePoint = {
    point_id: 'job-1:invoke-1',
    dataset_id: 'job-1',
    workflow_family: 'de_novo_nanobody',
    job_id: 'job-1',
    design_id: 'design-1',
    candidate_id: 'Nb-1',
    invocation_id: 'invoke-1',
    source_artifact_sha256: hash('a'),
    checkpoint_sha256: hash('b'),
    configuration_id: 'frustrampnn_global_v1',
    configuration_sha256: hash('c'),
    threshold_policy_id: 'frustrampnn_class_v1',
};
const page = {
    schema_version: 'frustrampnn_multidimensional_v1',
    level: 'result',
    dimensions: resultDimensions,
    total: 2,
    limit: 1000,
    offset: 0,
    next_offset: null,
    items: [
        {
            ...basePoint,
            metrics: {
                mean_score: -0.2, native_score: -0.1, high_fraction: 0.3,
                minimal_fraction: 0.2, scoreable_fraction: 1, slot_count: 20, residue_count: 1,
            },
        },
        {
            ...basePoint,
            point_id: 'job-2:invoke-2', dataset_id: 'job-2', job_id: 'job-2', design_id: 'design-2',
            candidate_id: 'Nb-2', invocation_id: 'invoke-2', source_artifact_sha256: hash('d'),
            metrics: {
                mean_score: 0.4, native_score: 0.2, high_fraction: 0.1,
                minimal_fraction: 0.6, scoreable_fraction: 0.95, slot_count: 20, residue_count: 1,
            },
        },
    ],
};

test('multidimensional result page parser preserves exact traceable identities and metrics', () => {
    const parsed = parseFrustraMpnnMultidimensionalPage(page);
    assert.equal(parsed.level, 'result');
    assert.equal(parsed.items.length, 2);
    assert.equal(parsed.items[1]?.design_id, 'design-2');
    assert.equal(parsed.items[1]?.configuration_sha256, hash('c'));
    assert.equal(parsed.items[1]?.source_artifact_sha256, hash('d'));
});

test('multidimensional parser discriminates exact residue and mutation contracts', () => {
    const residueIdentityDimensions = ['target_id', 'entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'wt']
        .map((id) => dimension(id, 'identifier', 'Exact persisted residue identity.'));
    const residuePage = {
        ...page,
        level: 'residue',
        dimensions: [...identityDimensions, ...residueIdentityDimensions,
            dimension('native_score', 'number', null, 'FrustraMPNN score', 'persisted WT→WT slot'),
            dimension('alternative_mean_score', 'number', null, 'FrustraMPNN score', 'mean across finite scoreable non-native slots'),
            dimension('best_alternative_delta', 'number', null, 'score delta', 'max(non-native score) − native score'),
            dimension('worst_alternative_delta', 'number', null, 'score delta', 'min(non-native score) − native score'),
            dimension('high_alternative_fraction', 'fraction', null, 'fraction', 'high-class scoreable non-native slots / scoreable non-native slots'),
            dimension('minimal_alternative_fraction', 'fraction', null, 'fraction', 'minimal-class scoreable non-native slots / scoreable non-native slots'),
            dimension('alternative_count', 'count', null, 'slots', 'finite scoreable non-native slots'),
        ],
        total: 1,
        items: [{
            ...basePoint,
            point_id: 'job-1:invoke-1:target-1:entity-1:A:10::1',
            target_id: 'target-1', entity_instance_id: 'entity-1', auth_asym_id: 'A', auth_seq_id: '10',
            insertion_code: '', sequence_index: 1, wt: 'G',
            metrics: {
                native_score: 0, alternative_mean_score: 0.25, best_alternative_delta: 1,
                worst_alternative_delta: -1, high_alternative_fraction: 0.1,
                minimal_alternative_fraction: 0.2, alternative_count: 19,
            },
        }],
    };
    const parsedResidue = parseFrustraMpnnMultidimensionalPage(residuePage);
    assert.equal(parsedResidue.level, 'residue');
    assert.equal(parsedResidue.items[0]?.wt, 'G');

    const mutationIdentityDimensions = [...residueIdentityDimensions.slice(0, 7),
        dimension('mutation_aa', 'identifier', 'Exact persisted residue/substitution identity.'),
        dimension('score_class', 'identifier', 'Exact persisted residue/substitution identity.'),
        dimension('status', 'identifier', 'Exact persisted residue/substitution identity.'),
    ].map((item) => typeof item === 'string'
        ? dimension(item, 'identifier', 'Exact persisted residue/substitution identity.')
        : item);
    const mutationPage = {
        ...page,
        level: 'mutation',
        dimensions: [...identityDimensions, ...mutationIdentityDimensions,
            dimension('score', 'number', null, 'FrustraMPNN score', 'persisted exact substitution score'),
            dimension('scoreable', 'boolean', null, null, 'persisted scoreability'),
        ],
        total: 1,
        items: [{
            ...basePoint,
            point_id: 'job-1:invoke-1:target-1:entity-1:A:10::1:A',
            target_id: 'target-1', entity_instance_id: 'entity-1', auth_asym_id: 'A', auth_seq_id: '10',
            insertion_code: '', sequence_index: 1, wt: 'G', mutation_aa: 'A', score_class: 'minimal',
            status: 'ok', reason: null, metrics: { score: 0.5, scoreable: true },
        }],
    };
    const parsedMutation = parseFrustraMpnnMultidimensionalPage(mutationPage);
    assert.equal(parsedMutation.level, 'mutation');
    assert.equal(parsedMutation.items[0]?.mutation_aa, 'A');
});

test('3D model maps selected declared result metrics without anonymous points', () => {
    const parsed = parseFrustraMpnnMultidimensionalPage(page);
    const model = buildFrustraMpnn3dModel(parsed, 'mean_score', 'high_fraction', 'minimal_fraction', 'scoreable_fraction');
    assert.deepEqual(model.x, [-0.2, 0.4]);
    assert.deepEqual(model.y, [0.3, 0.1]);
    assert.deepEqual(model.z, [0.2, 0.6]);
    assert.deepEqual(model.color, [1, 0.95]);
    assert.deepEqual(model.pointIds, ['job-1:invoke-1', 'job-2:invoke-2']);
    assert.match(model.hover[0]!, /job-1.*design-1.*Nb-1/s);
});

test('multidimensional parser rejects unknown, missing, non-finite, and cross-level fields', () => {
    const invalidMetric = structuredClone(page);
    invalidMetric.items[0]!.metrics.mean_score = Number.NaN;
    assert.throws(() => parseFrustraMpnnMultidimensionalPage(invalidMetric), /finite/);
    const missingConfiguration = structuredClone(page);
    delete missingConfiguration.items[0]!.configuration_id;
    assert.throws(() => parseFrustraMpnnMultidimensionalPage(missingConfiguration), /missing|identity/);
    const extraPoint = structuredClone(page);
    Object.assign(extraPoint.items[0]!, { browser_metric: 7 });
    assert.throws(() => parseFrustraMpnnMultidimensionalPage(extraPoint), /unknown|keys/);
    const missingMetric = structuredClone(page);
    delete missingMetric.items[0]!.metrics.residue_count;
    assert.throws(() => parseFrustraMpnnMultidimensionalPage(missingMetric), /missing|metrics/);
    const wrongDimension = structuredClone(page);
    wrongDimension.dimensions[0]!.kind = 'number';
    assert.throws(() => parseFrustraMpnnMultidimensionalPage(wrongDimension), /dimension/);
});
