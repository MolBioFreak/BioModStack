import assert from 'node:assert/strict';
import test from 'node:test';

import type { CmResults } from '../src/components/conformationalMapping/conformationalMappingApi.js';
import { canonicalStateLandscapeAnalysis } from '../src/components/conformationalMapping/stateLandscapeSemantics.js';

const sha = (letter: string) => letter.repeat(64);

const coordinate = (sampleIndex: number) => ({
    backend: 'protenix_v2_ensemble', target_id: 'target-a', ordered_seed: 101, sample_index: sampleIndex,
});

const candidate = (candidateId: string, sampleIndex: number) => ({
    candidate_id: candidateId,
    backend_coordinates: coordinate(sampleIndex),
    authoritative_structure_path: `native/${candidateId}/structure.cif`,
    authoritative_structure_sha256: sha(candidateId === 'candidate-a' ? 'b' : 'c'),
    sidecar_paths: [`native/${candidateId}/confidence.json`, `native/${candidateId}/full-data.json`],
});

const ensemble = {
    schema_name: 'cm_ensemble', schema_version: 1, request_id: 'request-a', request_sha256: sha('a'),
    source_snapshot_sha256: sha('d'), backend: 'protenix_v2_ensemble', runtime_identity: 'runtime',
    container_digest: `sha256:${sha('e')}`, checkpoint_sha256: sha('f'), feature_policy_sha256: sha('1'),
    expected_cardinality: 2, expected_coordinates: [coordinate(0), coordinate(1)],
    candidates: [candidate('candidate-a', 0), candidate('candidate-b', 1)],
    native_manifest_path: 'cm_native_artifacts_v1.json', native_manifest_sha256: sha('2'), warnings: [], omissions: [],
    terminal_status: 'complete', started_at: '2026-07-23T00:00:00Z', completed_at: '2026-07-23T00:01:00Z',
    resumable: true, resume_key: sha('3'),
};

const numericMetric = () => ({ a: 1.25, b: 2.5, delta_b_minus_a: 1.25, status: 'ok' as const, reason: null });
const classMetric = () => ({ a: 'neutral', b: 'high', transition: 'neutral_to_high', status: 'ok', reason: null });

interface TestNumericMetric {
    a: number | null;
    b: number | null;
    delta_b_minus_a: number | null;
    status: 'ok' | 'unavailable';
    reason: string | null;
}

interface TestAnalysis {
    schema_name: string;
    schema_version: number;
    analysis_id: string;
    source_ensemble_sha256: string;
    source_landscape_sha256: string;
    source_structure_map_sha256: string;
    comparison_mode: 'pairwise' | 'reference';
    comparison_target_id: string;
    comparison_scope: 'all_within_target' | 'all_other_within_target';
    reference_backend_coordinates: Record<string, unknown> | null;
    reference_candidate_id: string | null;
    resolved_pairs: Array<{ pair_id: string; candidate_a_id: string; candidate_b_id: string }>;
    comparison_sha256: string;
    formula_version: string;
    formula_sha256: string;
    policy_sha256: string;
    rows: Array<{
        pair_id: string;
        candidate_a_id: string;
        candidate_b_id: string;
        identity: Record<string, unknown>;
        metrics: {
            native_score: TestNumericMetric;
            high_non_native_highly_frustrated_fraction: TestNumericMetric;
            maximum_non_native_substitution_delta_relative_to_native: TestNumericMetric;
            native_class: Record<string, unknown>;
        };
    }>;
    support_ledger: Array<Record<string, unknown>>;
    exclusion_ledger: Array<Record<string, unknown>>;
}

const analysis = (): TestAnalysis => ({
    schema_name: 'cm_state_landscape_analysis', schema_version: 1,
    analysis_id: `cm_state_landscape_analysis_${'a'.repeat(32)}`,
    source_ensemble_sha256: sha('4'), source_landscape_sha256: sha('5'), source_structure_map_sha256: sha('6'),
    comparison_mode: 'pairwise', comparison_target_id: 'target-a', comparison_scope: 'all_within_target',
    reference_backend_coordinates: null, reference_candidate_id: null,
    resolved_pairs: [{ pair_id: 'candidate-a__candidate-b', candidate_a_id: 'candidate-a', candidate_b_id: 'candidate-b' }],
    comparison_sha256: sha('7'), formula_version: 'cm_state_landscape_analysis_v1', formula_sha256: sha('8'), policy_sha256: sha('9'),
    rows: [{
        pair_id: 'candidate-a__candidate-b', candidate_a_id: 'candidate-a', candidate_b_id: 'candidate-b',
        identity: {
            target_id: 'target-a', entity_instance_id: 'copy-1', auth_asym_id: 'A', auth_seq_id: 42,
            insertion_code: '', sequence_index: 42, validated_wt: 'A',
        },
        metrics: {
            native_score: numericMetric(),
            high_non_native_highly_frustrated_fraction: numericMetric(),
            maximum_non_native_substitution_delta_relative_to_native: numericMetric(),
            native_class: classMetric(),
        },
    }],
    support_ledger: [{
        pair_id: 'candidate-a__candidate-b', candidate_a_id: 'candidate-a', candidate_b_id: 'candidate-b',
        eligible_row_count: 1, excluded_row_count: 0,
    }],
    exclusion_ledger: [],
});

const results = (): CmResults => ({
    request_id: 'request-a', result_contract_id: 'conformational_mapping_analysis_v1',
    records: [{ type: 'ensemble', key: 'primary', sha256: sha('0'), payload: ensemble }],
    artifacts: [],
});

const withAnalysis = (): CmResults => {
    const value = results();
    const payload = analysis();
    value.records.push({
        type: 'state_landscape_analysis', key: payload.analysis_id, sha256: sha('a'),
        payload: payload as unknown as Record<string, unknown>,
    });
    return value;
};

test('accepts one canonical pairwise state landscape analysis without reordering its scientific rows', () => {
    const value = withAnalysis();
    const parsed = canonicalStateLandscapeAnalysis(value);

    assert.ok(parsed);
    assert.equal(parsed.analysis_id, value.records[1].key);
    assert.strictEqual(parsed.rows, (value.records[1].payload as unknown as TestAnalysis).rows);
    assert.deepEqual(parsed.resolved_pairs.map((pair) => pair.pair_id), ['candidate-a__candidate-b']);
});

test('accepts a reference selector only when it binds the reference candidate and canonical pair direction', () => {
    const value = withAnalysis();
    const payload = value.records[1].payload as unknown as TestAnalysis;
    payload.comparison_mode = 'reference';
    payload.comparison_scope = 'all_other_within_target';
    payload.reference_candidate_id = 'candidate-a';
    payload.reference_backend_coordinates = coordinate(0);

    const parsed = canonicalStateLandscapeAnalysis(value);
    assert.ok(parsed);
    assert.equal(parsed.reference_candidate_id, 'candidate-a');
    assert.strictEqual(parsed.reference_backend_coordinates, payload.reference_backend_coordinates);
});

test('returns absent cleanly when no state-analysis authority record exists', () => {
    assert.equal(canonicalStateLandscapeAnalysis(results()), null);
});

test('fails closed when state-analysis authority is duplicated', () => {
    const value = withAnalysis();
    value.records.push({ ...value.records[1], key: 'duplicate-analysis-id' });
    assert.throws(() => canonicalStateLandscapeAnalysis(value), /exactly.*once|ambiguous/i);
});

test('fails closed when a resolved pair names an unknown artifact candidate', () => {
    const value = withAnalysis();
    const payload = value.records[1].payload as unknown as TestAnalysis;
    payload.resolved_pairs[0].candidate_b_id = 'candidate-not-in-ensemble';
    assert.throws(() => canonicalStateLandscapeAnalysis(value), /candidate/i);
});

test('fails closed when a row does not bind its resolved pair', () => {
    const value = withAnalysis();
    const payload = value.records[1].payload as unknown as TestAnalysis;
    payload.rows[0].candidate_b_id = 'candidate-a';
    assert.throws(() => canonicalStateLandscapeAnalysis(value), /row.*pair|pair.*row/i);
});

test('fails closed when an unavailable metric lacks a reason or fabricates zero values', () => {
    const missingReason = withAnalysis();
    const missingReasonPayload = missingReason.records[1].payload as unknown as TestAnalysis;
    missingReasonPayload.rows[0].metrics.native_score = {
        a: null, b: null, delta_b_minus_a: null, status: 'unavailable', reason: null,
    };
    assert.throws(() => canonicalStateLandscapeAnalysis(missingReason), /unavailable.*reason/i);

    const fabricatedZero = withAnalysis();
    const fabricatedZeroPayload = fabricatedZero.records[1].payload as unknown as TestAnalysis;
    fabricatedZeroPayload.rows[0].metrics.native_score = {
        a: 0, b: 0, delta_b_minus_a: 0, status: 'unavailable', reason: 'missing_slot',
    };
    assert.throws(() => canonicalStateLandscapeAnalysis(fabricatedZero), /unavailable.*values/i);
});

test('fails closed when canonical row identity is duplicated', () => {
    const value = withAnalysis();
    const payload = value.records[1].payload as unknown as TestAnalysis;
    payload.rows.push(structuredClone(payload.rows[0]));
    assert.throws(() => canonicalStateLandscapeAnalysis(value), /row identity/i);
});
