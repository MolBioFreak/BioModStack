import type {
    CmStateLandscapeAnalysis,
    CmStateLandscapeClass,
    CmStateLandscapeClassMetric,
    CmStateLandscapeExclusion,
    CmStateLandscapeIdentity,
    CmStateLandscapeNumericMetric,
    CmStateLandscapeResolvedPair,
    CmStateLandscapeRow,
    CmStateLandscapeSupport,
    CmResults,
} from './conformationalMappingApi';
import { canonicalEnsemble, requireApprovedCmResults } from './conformationalMappingSemantics';

const SHA256 = /^[0-9a-f]{64}$/;
const ANALYSIS_ID = /^cm_state_landscape_analysis_[0-9a-f]{32}$/;
const AMINO_ACIDS = new Set(['A', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'K', 'L', 'M', 'N', 'P', 'Q', 'R', 'S', 'T', 'V', 'W', 'Y']);
const CLASS_VALUES = new Set<CmStateLandscapeClass>(['high', 'neutral', 'minimally_frustrated']);
const EXCLUSION_REASONS = new Set([
    'identity_mismatch', 'missing_map', 'missing_row', 'missing_slot', 'wt_mismatch',
    'nonfinite_or_unavailable_slot', 'provenance_mismatch', 'candidate_analysis_unavailable',
]);

const object = (value: unknown, message: string): Record<string, unknown> => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(message);
    return value as Record<string, unknown>;
};

const array = (value: unknown, message: string): unknown[] => {
    if (!Array.isArray(value)) throw new Error(message);
    return value;
};

const string = (value: unknown, message: string): string => {
    if (typeof value !== 'string' || !value) throw new Error(message);
    return value;
};

const nullableString = (value: unknown, message: string): string | null => {
    if (value === null) return null;
    return string(value, message);
};

const integer = (value: unknown, message: string, minimum?: number): number => {
    if (!Number.isInteger(value) || (minimum !== undefined && (value as number) < minimum)) throw new Error(message);
    return value as number;
};

const finiteNumber = (value: unknown, message: string): number => {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(message);
    return value;
};

const sha = (value: unknown, message: string): string => {
    const result = string(value, message);
    if (!SHA256.test(result)) throw new Error(message);
    return result;
};

const exactFields = (value: Record<string, unknown>, fields: string[], message: string): void => {
    const keys = Object.keys(value).sort();
    const expected = [...fields].sort();
    if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) throw new Error(message);
};

const sameFlatRecord = (left: Record<string, unknown>, rightValue: unknown): boolean => {
    if (!rightValue || typeof rightValue !== 'object' || Array.isArray(rightValue)) return false;
    const right = rightValue as Record<string, unknown>;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
        && leftKeys.every((key, index) => key === rightKeys[index] && left[key] === right[key]);
};

const pairKey = (pair: CmStateLandscapeResolvedPair): string =>
    `${pair.pair_id}\u0000${pair.candidate_a_id}\u0000${pair.candidate_b_id}`;

const validateIdentity = (value: unknown, message: string): CmStateLandscapeIdentity => {
    const identity = object(value, message);
    exactFields(identity, [
        'target_id', 'entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'validated_wt',
    ], message);
    string(identity.target_id, message);
    string(identity.entity_instance_id, message);
    string(identity.auth_asym_id, message);
    integer(identity.auth_seq_id, message);
    if (typeof identity.insertion_code !== 'string' || identity.insertion_code.length > 1) throw new Error(message);
    integer(identity.sequence_index, message, 1);
    const wt = string(identity.validated_wt, message);
    if (!AMINO_ACIDS.has(wt)) throw new Error(message);
    return identity as unknown as CmStateLandscapeIdentity;
};

const stableRowIdentity = (pair: CmStateLandscapeResolvedPair, identity: CmStateLandscapeIdentity): string => JSON.stringify([
    pair.pair_id, identity.target_id, identity.entity_instance_id, identity.auth_asym_id,
    identity.auth_seq_id, identity.insertion_code, identity.sequence_index, identity.validated_wt,
]);

const validateNumericMetric = (value: unknown, metricName: string): CmStateLandscapeNumericMetric => {
    const metric = object(value, `${metricName} metric is malformed`);
    exactFields(metric, ['a', 'b', 'delta_b_minus_a', 'status', 'reason'], `${metricName} metric is malformed`);
    if (metric.status === 'ok') {
        finiteNumber(metric.a, `${metricName} available metric value is malformed`);
        finiteNumber(metric.b, `${metricName} available metric value is malformed`);
        finiteNumber(metric.delta_b_minus_a, `${metricName} available metric value is malformed`);
        if (metric.reason !== null) throw new Error(`${metricName} available metric must not have a reason`);
    } else if (metric.status === 'unavailable') {
        if (metric.a !== null || metric.b !== null || metric.delta_b_minus_a !== null) {
            throw new Error(`${metricName} unavailable metric must not fabricate values`);
        }
        string(metric.reason, `${metricName} unavailable metric requires a reason`);
    } else {
        throw new Error(`${metricName} metric availability state is unknown`);
    }
    return metric as unknown as CmStateLandscapeNumericMetric;
};

const validateClassMetric = (value: unknown): CmStateLandscapeClassMetric => {
    const metric = object(value, 'Native class metric is malformed');
    exactFields(metric, ['a', 'b', 'transition', 'status', 'reason'], 'Native class metric is malformed');
    if (metric.status === 'ok') {
        const a = string(metric.a, 'Available native class metric is malformed');
        const b = string(metric.b, 'Available native class metric is malformed');
        if (!CLASS_VALUES.has(a as CmStateLandscapeClass) || !CLASS_VALUES.has(b as CmStateLandscapeClass)) {
            throw new Error('Available native class metric is malformed');
        }
        string(metric.transition, 'Available native class metric is malformed');
        if (metric.reason !== null) throw new Error('Available native class metric must not have a reason');
    } else if (metric.status === 'unavailable') {
        if (metric.a !== null || metric.b !== null || metric.transition !== null) {
            throw new Error('Unavailable native class metric must not fabricate values');
        }
        string(metric.reason, 'Unavailable native class metric requires a reason');
    } else {
        throw new Error('Native class metric availability state is unknown');
    }
    return metric as unknown as CmStateLandscapeClassMetric;
};

const validatePairs = (
    value: unknown,
    candidateCoordinates: Map<string, Record<string, unknown>>,
    comparisonTargetId: string,
    comparisonMode: 'pairwise' | 'reference',
    referenceCandidateId: string | null,
): CmStateLandscapeResolvedPair[] => {
    const pairs = array(value, 'State landscape resolved pair ledger is malformed');
    if (pairs.length === 0) throw new Error('State landscape resolved pair ledger is empty');
    const parsed = pairs.map((value) => {
        const pair = object(value, 'State landscape resolved pair is malformed');
        exactFields(pair, ['pair_id', 'candidate_a_id', 'candidate_b_id'], 'State landscape resolved pair is malformed');
        const parsedPair = {
            pair_id: string(pair.pair_id, 'State landscape pair identity is missing'),
            candidate_a_id: string(pair.candidate_a_id, 'State landscape pair candidate identity is missing'),
            candidate_b_id: string(pair.candidate_b_id, 'State landscape pair candidate identity is missing'),
        };
        if (parsedPair.candidate_a_id === parsedPair.candidate_b_id) throw new Error('State landscape pair candidates must differ');
        if (parsedPair.pair_id !== `${parsedPair.candidate_a_id}__${parsedPair.candidate_b_id}`) {
            throw new Error('State landscape pair ID does not bind its candidates');
        }
        const candidateA = candidateCoordinates.get(parsedPair.candidate_a_id);
        const candidateB = candidateCoordinates.get(parsedPair.candidate_b_id);
        if (!candidateA || !candidateB) throw new Error('State landscape pair candidate is absent from canonical artifact candidates');
        if (candidateA.target_id !== comparisonTargetId || candidateB.target_id !== comparisonTargetId) {
            throw new Error('State landscape pair candidate target is inconsistent with comparison authority');
        }
        return parsedPair;
    });
    if (new Set(parsed.map((pair) => pair.pair_id)).size !== parsed.length) throw new Error('State landscape pair IDs are duplicated');
    if (parsed.some((pair, index) => index > 0 && pair.pair_id < parsed[index - 1].pair_id)) {
        throw new Error('State landscape pair ledger is not in canonical stable order');
    }

    const candidateIds = [...candidateCoordinates]
        .filter(([, coordinates]) => coordinates.target_id === comparisonTargetId)
        .map(([candidateId]) => candidateId)
        .sort();
    const expected = comparisonMode === 'pairwise'
        ? candidateIds.flatMap((candidateA, index) => candidateIds.slice(index + 1).map((candidateB) => ({
            pair_id: `${candidateA}__${candidateB}`, candidate_a_id: candidateA, candidate_b_id: candidateB,
        })))
        : candidateIds.filter((candidateId) => candidateId !== referenceCandidateId).map((candidateId) => ({
            pair_id: `${referenceCandidateId}__${candidateId}`, candidate_a_id: referenceCandidateId as string, candidate_b_id: candidateId,
        }));
    if (parsed.length !== expected.length || parsed.some((pair, index) => pairKey(pair) !== pairKey(expected[index]))) {
        throw new Error('State landscape resolved pairs do not exactly match canonical comparison authority');
    }
    return parsed;
};

const validatePairBoundEntry = (
    value: unknown,
    pairs: Set<string>,
    message: string,
): CmStateLandscapeResolvedPair => {
    const entry = object(value, message);
    const pair = {
        pair_id: string(entry.pair_id, message),
        candidate_a_id: string(entry.candidate_a_id, message),
        candidate_b_id: string(entry.candidate_b_id, message),
    };
    if (!pairs.has(pairKey(pair))) throw new Error(message);
    return pair;
};

const validateRows = (value: unknown, pairs: Set<string>, comparisonTargetId: string): CmStateLandscapeRow[] => {
    const rows = array(value, 'State landscape rows are malformed');
    const identities = new Set<string>();
    rows.forEach((value) => {
        const row = object(value, 'State landscape row is malformed');
        exactFields(row, ['pair_id', 'candidate_a_id', 'candidate_b_id', 'identity', 'metrics'], 'State landscape row is malformed');
        const pair = validatePairBoundEntry(row, pairs, 'State landscape row does not bind a resolved pair');
        const identity = validateIdentity(row.identity, 'State landscape row identity is malformed');
        if (identity.target_id !== comparisonTargetId) throw new Error('State landscape row target is inconsistent with comparison authority');
        const stableIdentity = stableRowIdentity(pair, identity);
        if (identities.has(stableIdentity)) throw new Error('State landscape canonical row identity is duplicated');
        identities.add(stableIdentity);
        const metrics = object(row.metrics, 'State landscape row metrics are malformed');
        exactFields(metrics, [
            'native_score', 'high_non_native_highly_frustrated_fraction',
            'maximum_non_native_substitution_delta_relative_to_native', 'native_class',
        ], 'State landscape row metrics are malformed');
        validateNumericMetric(metrics.native_score, 'Native score');
        validateNumericMetric(metrics.high_non_native_highly_frustrated_fraction, 'High non-native highly frustrated fraction');
        validateNumericMetric(metrics.maximum_non_native_substitution_delta_relative_to_native, 'Maximum non-native substitution delta');
        validateClassMetric(metrics.native_class);
    });
    return rows as unknown as CmStateLandscapeRow[];
};

const validateSupport = (
    value: unknown,
    pairs: Set<string>,
    exclusions: CmStateLandscapeExclusion[],
): CmStateLandscapeSupport[] => {
    const support = array(value, 'State landscape support ledger is malformed');
    const seen = new Set<string>();
    const excludedCounts = new Map<string, number>();
    exclusions.forEach((entry) => {
        const key = pairKey(entry);
        excludedCounts.set(key, (excludedCounts.get(key) ?? 0) + 1);
    });
    support.forEach((value) => {
        const entry = object(value, 'State landscape support entry is malformed');
        exactFields(entry, ['pair_id', 'candidate_a_id', 'candidate_b_id', 'eligible_row_count', 'excluded_row_count'], 'State landscape support entry is malformed');
        const pair = validatePairBoundEntry(entry, pairs, 'State landscape support entry does not bind a resolved pair');
        const key = pairKey(pair);
        if (seen.has(key)) throw new Error('State landscape support ledger contains a duplicate pair');
        seen.add(key);
        integer(entry.eligible_row_count, 'State landscape support count is malformed', 0);
        const excludedRowCount = integer(entry.excluded_row_count, 'State landscape support count is malformed', 0);
        if (excludedRowCount !== (excludedCounts.get(key) ?? 0)) {
            throw new Error('State landscape support exclusion count does not match exclusion ledger');
        }
    });
    if (seen.size !== pairs.size) throw new Error('State landscape support ledger does not cover every resolved pair');
    return support as unknown as CmStateLandscapeSupport[];
};

const validateExclusions = (value: unknown, pairs: Set<string>, comparisonTargetId: string): CmStateLandscapeExclusion[] => {
    const exclusions = array(value, 'State landscape exclusion ledger is malformed');
    exclusions.forEach((value) => {
        const entry = object(value, 'State landscape exclusion entry is malformed');
        exactFields(entry, ['pair_id', 'candidate_a_id', 'candidate_b_id', 'identity', 'reason', 'detail'], 'State landscape exclusion entry is malformed');
        validatePairBoundEntry(entry, pairs, 'State landscape exclusion does not bind a resolved pair');
        if (entry.identity !== null) {
            const identity = validateIdentity(entry.identity, 'State landscape exclusion identity is malformed');
            if (identity.target_id !== comparisonTargetId) throw new Error('State landscape exclusion target is inconsistent with comparison authority');
        }
        const reason = string(entry.reason, 'State landscape exclusion reason is malformed');
        if (!EXCLUSION_REASONS.has(reason)) throw new Error('State landscape exclusion reason is unknown');
        string(entry.detail, 'State landscape exclusion detail is malformed');
    });
    return exclusions as unknown as CmStateLandscapeExclusion[];
};

/**
 * Returns the sole immutable Phase-A state analysis without deriving or reordering
 * candidate pairs, rows, metric values, support, or exclusions for presentation.
 */
export const canonicalStateLandscapeAnalysis = (results: CmResults): CmStateLandscapeAnalysis | null => {
    requireApprovedCmResults(results);
    const matches = results.records.filter((item) => item.type === 'state_landscape_analysis');
    if (matches.length === 0) return null;
    if (matches.length !== 1) throw new Error('Canonical state landscape analysis authority must exist exactly once');

    const record = matches[0];
    const payload = object(record.payload, 'Canonical state landscape analysis is malformed');
    exactFields(payload, [
        'schema_name', 'schema_version', 'analysis_id', 'source_ensemble_sha256', 'source_landscape_sha256',
        'source_structure_map_sha256', 'comparison_mode', 'comparison_target_id', 'comparison_scope',
        'reference_backend_coordinates', 'reference_candidate_id', 'resolved_pairs', 'comparison_sha256',
        'formula_version', 'formula_sha256', 'policy_sha256', 'rows', 'support_ledger', 'exclusion_ledger',
    ], 'Canonical state landscape analysis is malformed');
    if (payload.schema_name !== 'cm_state_landscape_analysis' || payload.schema_version !== 1
        || payload.formula_version !== 'cm_state_landscape_analysis_v1') {
        throw new Error('Canonical state landscape analysis schema is malformed');
    }
    const analysisId = string(payload.analysis_id, 'Canonical state landscape analysis ID is missing');
    if (!ANALYSIS_ID.test(analysisId) || record.key !== analysisId) {
        throw new Error('Canonical state landscape analysis record identity is inconsistent');
    }
    sha(payload.source_ensemble_sha256, 'Canonical state landscape source ensemble hash is malformed');
    sha(payload.source_landscape_sha256, 'Canonical state landscape source landscape hash is malformed');
    sha(payload.source_structure_map_sha256, 'Canonical state landscape source structure-map hash is malformed');
    sha(payload.comparison_sha256, 'Canonical state landscape comparison hash is malformed');
    sha(payload.formula_sha256, 'Canonical state landscape formula hash is malformed');
    sha(payload.policy_sha256, 'Canonical state landscape policy hash is malformed');

    const comparisonTargetId = string(payload.comparison_target_id, 'Canonical state landscape comparison target is missing');
    if (payload.comparison_mode !== 'pairwise' && payload.comparison_mode !== 'reference') {
        throw new Error('Canonical state landscape comparison mode is malformed');
    }
    const comparisonMode = payload.comparison_mode;
    const candidateCoordinates = new Map(canonicalEnsemble(results).candidates.map((candidate) => [
        candidate.candidate_id, candidate.backend_coordinates,
    ]));
    let referenceCandidateId: string | null;
    if (comparisonMode === 'pairwise') {
        if (payload.comparison_scope !== 'all_within_target' || payload.reference_candidate_id !== null
            || payload.reference_backend_coordinates !== null) {
            throw new Error('Pairwise state landscape comparison authority is inconsistent');
        }
        referenceCandidateId = null;
    } else {
        if (payload.comparison_scope !== 'all_other_within_target') {
            throw new Error('Reference state landscape comparison scope is inconsistent');
        }
        referenceCandidateId = nullableString(payload.reference_candidate_id, 'Reference state landscape candidate is malformed');
        if (!referenceCandidateId) throw new Error('Reference state landscape candidate is missing');
        const referenceCoordinates = object(payload.reference_backend_coordinates, 'Reference state landscape selector is malformed');
        const candidateCoordinatesForReference = candidateCoordinates.get(referenceCandidateId);
        if (!candidateCoordinatesForReference || !sameFlatRecord(candidateCoordinatesForReference, referenceCoordinates)
            || referenceCoordinates.target_id !== comparisonTargetId) {
            throw new Error('Reference state landscape selector does not bind the canonical reference candidate');
        }
    }

    const pairs = validatePairs(payload.resolved_pairs, candidateCoordinates, comparisonTargetId, comparisonMode, referenceCandidateId);
    const pairSet = new Set(pairs.map(pairKey));
    validateRows(payload.rows, pairSet, comparisonTargetId);
    const exclusions = validateExclusions(payload.exclusion_ledger, pairSet, comparisonTargetId);
    validateSupport(payload.support_ledger, pairSet, exclusions);
    return payload as unknown as CmStateLandscapeAnalysis;
};
