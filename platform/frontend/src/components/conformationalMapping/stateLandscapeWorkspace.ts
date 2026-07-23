import type { ResidueRef } from '../../structureViewer/contracts/structureIdentity.js';
import type { CmStructureMap } from './conformationalMappingSemantics.js';
import type {
    CmStateLandscapeAnalysis,
    CmStateLandscapeAnalysisRowsPage,
    CmStateLandscapeAnalysisSummary,
    CmStateLandscapeClassMetric,
    CmStateLandscapeIdentity,
    CmStateLandscapeNumericMetric,
    CmStateLandscapeResolvedPair,
    CmStateLandscapeRow,
} from './conformationalMappingApi.js';

export type StateLandscapeWorkspaceTab = 'ensemble' | 'mapping' | 'landscape' | 'state-analysis' | 'analysis' | 'evidence' | 'downloads';

const WORKSPACE_TABS: StateLandscapeWorkspaceTab[] = ['ensemble', 'mapping', 'landscape', 'analysis', 'evidence', 'downloads'];
export const stateLandscapeSummaryEnabled = (authority: CmStateLandscapeAnalysis | null): boolean => authority !== null;
/** C1 fail-closed gate: absent or malformed authority never exposes a state-analysis lens. */
export const stateLandscapeWorkspaceTabs = (available: boolean): StateLandscapeWorkspaceTab[] => available
    ? [...WORKSPACE_TABS.slice(0, 3), 'state-analysis', ...WORKSPACE_TABS.slice(3)]
    : WORKSPACE_TABS;

const SHA256 = /^[0-9a-f]{64}$/;
const AMINO_ACIDS = new Set(['A', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'K', 'L', 'M', 'N', 'P', 'Q', 'R', 'S', 'T', 'V', 'W', 'Y']);
const CLASSES = new Set(['high', 'neutral', 'minimally_frustrated']);
const METRIC_NAMES = [
    'native_score', 'high_non_native_highly_frustrated_fraction',
    'maximum_non_native_substitution_delta_relative_to_native', 'native_class',
] as const;

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
const integer = (value: unknown, message: string, minimum = Number.NEGATIVE_INFINITY): number => {
    if (!Number.isInteger(value) || (value as number) < minimum) throw new Error(message);
    return value as number;
};
const finite = (value: unknown, message: string): number => {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(message);
    return value;
};
const sha = (value: unknown, message: string): string => {
    const result = string(value, message);
    if (!SHA256.test(result)) throw new Error(message);
    return result;
};
const exact = (value: Record<string, unknown>, fields: readonly string[], message: string): void => {
    const keys = Object.keys(value).sort();
    const expected = [...fields].sort();
    if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) throw new Error(message);
};
const equalJson = (left: unknown, right: unknown): boolean => JSON.stringify(left) === JSON.stringify(right);
const pairKey = (pair: CmStateLandscapeResolvedPair): string => `${pair.pair_id}\u0000${pair.candidate_a_id}\u0000${pair.candidate_b_id}`;

const validatePair = (value: unknown): CmStateLandscapeResolvedPair => {
    const pair = object(value, 'State-analysis pair is malformed');
    exact(pair, ['pair_id', 'candidate_a_id', 'candidate_b_id'], 'State-analysis pair is malformed');
    const parsed = {
        pair_id: string(pair.pair_id, 'State-analysis pair ID is missing'),
        candidate_a_id: string(pair.candidate_a_id, 'State-analysis pair candidate is missing'),
        candidate_b_id: string(pair.candidate_b_id, 'State-analysis pair candidate is missing'),
    };
    if (parsed.candidate_a_id === parsed.candidate_b_id || parsed.pair_id !== `${parsed.candidate_a_id}__${parsed.candidate_b_id}`) {
        throw new Error('State-analysis pair does not bind distinct candidates');
    }
    return parsed;
};

const validateIdentity = (value: unknown): CmStateLandscapeIdentity => {
    const identity = object(value, 'State-analysis residue identity is malformed');
    exact(identity, ['target_id', 'entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'validated_wt'], 'State-analysis residue identity is malformed');
    const parsed = {
        target_id: string(identity.target_id, 'State-analysis target identity is missing'),
        entity_instance_id: string(identity.entity_instance_id, 'State-analysis entity identity is missing'),
        auth_asym_id: string(identity.auth_asym_id, 'State-analysis author chain is missing'),
        auth_seq_id: integer(identity.auth_seq_id, 'State-analysis author residue is malformed'),
        insertion_code: typeof identity.insertion_code === 'string' && identity.insertion_code.length <= 1 ? identity.insertion_code : (() => { throw new Error('State-analysis insertion code is malformed'); })(),
        sequence_index: integer(identity.sequence_index, 'State-analysis sequence index is malformed', 1),
        validated_wt: string(identity.validated_wt, 'State-analysis wild type is missing'),
    };
    if (!AMINO_ACIDS.has(parsed.validated_wt)) throw new Error('State-analysis wild type is unknown');
    return parsed;
};

const validateNumericMetric = (value: unknown, name: string): CmStateLandscapeNumericMetric => {
    const metric = object(value, `${name} metric is malformed`);
    exact(metric, ['a', 'b', 'delta_b_minus_a', 'status', 'reason'], `${name} metric is malformed`);
    if (metric.status === 'ok') {
        finite(metric.a, `${name} available metric is malformed`);
        finite(metric.b, `${name} available metric is malformed`);
        finite(metric.delta_b_minus_a, `${name} available metric is malformed`);
        if (metric.reason !== null) throw new Error(`${name} available metric has a reason`);
    } else if (metric.status === 'unavailable') {
        if (metric.a !== null || metric.b !== null || metric.delta_b_minus_a !== null || typeof metric.reason !== 'string' || !metric.reason) {
            throw new Error(`${name} unavailable metric is malformed`);
        }
    } else throw new Error(`${name} metric availability is unknown`);
    return metric as CmStateLandscapeNumericMetric;
};

const validateClassMetric = (value: unknown): CmStateLandscapeClassMetric => {
    const metric = object(value, 'Native class metric is malformed');
    exact(metric, ['a', 'b', 'transition', 'status', 'reason'], 'Native class metric is malformed');
    if (metric.status === 'ok') {
        if (!CLASSES.has(string(metric.a, 'Native class A is malformed')) || !CLASSES.has(string(metric.b, 'Native class B is malformed'))
            || !string(metric.transition, 'Native class transition is malformed') || metric.reason !== null) {
            throw new Error('Native class metric is malformed');
        }
    } else if (metric.status === 'unavailable') {
        if (metric.a !== null || metric.b !== null || metric.transition !== null || typeof metric.reason !== 'string' || !metric.reason) {
            throw new Error('Unavailable native class metric is malformed');
        }
    } else throw new Error('Native class metric availability is unknown');
    return metric as CmStateLandscapeClassMetric;
};

const validateRow = (value: unknown, pair: CmStateLandscapeResolvedPair, targetId: string): CmStateLandscapeRow & { availability: Record<string, unknown> } => {
    const row = object(value, 'State-analysis row is malformed');
    exact(row, ['pair_id', 'candidate_a_id', 'candidate_b_id', 'identity', 'metrics', 'availability'], 'State-analysis row is malformed');
    if (row.pair_id !== pair.pair_id || row.candidate_a_id !== pair.candidate_a_id || row.candidate_b_id !== pair.candidate_b_id) {
        throw new Error('State-analysis row does not bind the selected pair');
    }
    const identity = validateIdentity(row.identity);
    if (identity.target_id !== targetId) throw new Error('State-analysis row target does not bind summary authority');
    const metrics = object(row.metrics, 'State-analysis row metrics are malformed');
    exact(metrics, METRIC_NAMES, 'State-analysis row metrics are malformed');
    const parsedMetrics = {
        native_score: validateNumericMetric(metrics.native_score, 'Native score'),
        high_non_native_highly_frustrated_fraction: validateNumericMetric(metrics.high_non_native_highly_frustrated_fraction, 'High non-native highly frustrated fraction'),
        maximum_non_native_substitution_delta_relative_to_native: validateNumericMetric(metrics.maximum_non_native_substitution_delta_relative_to_native, 'Maximum non-native substitution delta'),
        native_class: validateClassMetric(metrics.native_class),
    };
    const availability = object(row.availability, 'State-analysis row availability is malformed');
    exact(availability, METRIC_NAMES, 'State-analysis row availability is malformed');
    for (const name of METRIC_NAMES) {
        const entry = object(availability[name], 'State-analysis metric availability is malformed');
        exact(entry, ['status', 'reason'], 'State-analysis metric availability is malformed');
        if (entry.status !== parsedMetrics[name].status || entry.reason !== parsedMetrics[name].reason) {
            throw new Error('State-analysis metric availability disagrees with persisted metrics');
        }
    }
    return { ...row as unknown as CmStateLandscapeRow, identity, metrics: parsedMetrics, availability };
};

/** Validates a compact B2 header without calculating scientific values or reordering its persisted pair ledger. */
export const validateStateLandscapeWorkspaceSummary = (
    value: unknown,
    canonicalAuthority: CmStateLandscapeAnalysis | null,
): CmStateLandscapeAnalysisSummary => {
    const summary = object(value, 'State-analysis summary is malformed');
    exact(summary, ['request_id', 'analysis_id', 'authority', 'comparison', 'counts', 'pairs', 'artifact'], 'State-analysis summary is malformed');
    const requestId = string(summary.request_id, 'State-analysis request identity is missing');
    const analysisId = string(summary.analysis_id, 'State-analysis analysis identity is missing');
    const authority = object(summary.authority, 'State-analysis authority is malformed');
    exact(authority, ['content_sha256', 'source_ensemble_sha256', 'source_landscape_sha256', 'source_structure_map_sha256', 'comparison_sha256', 'formula_version', 'formula_sha256', 'policy_sha256'], 'State-analysis authority is malformed');
    const parsedAuthority = {
        content_sha256: sha(authority.content_sha256, 'State-analysis content hash is malformed'),
        source_ensemble_sha256: sha(authority.source_ensemble_sha256, 'State-analysis ensemble hash is malformed'),
        source_landscape_sha256: sha(authority.source_landscape_sha256, 'State-analysis landscape hash is malformed'),
        source_structure_map_sha256: sha(authority.source_structure_map_sha256, 'State-analysis structure-map hash is malformed'),
        comparison_sha256: sha(authority.comparison_sha256, 'State-analysis comparison hash is malformed'),
        formula_version: string(authority.formula_version, 'State-analysis formula version is missing'),
        formula_sha256: sha(authority.formula_sha256, 'State-analysis formula hash is malformed'),
        policy_sha256: sha(authority.policy_sha256, 'State-analysis policy hash is malformed'),
    };
    if (parsedAuthority.formula_version !== 'cm_state_landscape_analysis_v1') throw new Error('State-analysis formula version is unsupported');
    const comparison = object(summary.comparison, 'State-analysis comparison authority is malformed');
    exact(comparison, ['mode', 'target_id', 'scope', 'reference_backend_coordinates', 'reference_candidate_id'], 'State-analysis comparison authority is malformed');
    if ((comparison.mode !== 'pairwise' && comparison.mode !== 'reference') || (comparison.scope !== 'all_within_target' && comparison.scope !== 'all_other_within_target')) {
        throw new Error('State-analysis comparison authority is malformed');
    }
    const parsedComparison: CmStateLandscapeAnalysisSummary['comparison'] = {
        mode: comparison.mode as CmStateLandscapeAnalysisSummary['comparison']['mode'],
        target_id: string(comparison.target_id, 'State-analysis comparison target is missing'),
        scope: comparison.scope as CmStateLandscapeAnalysisSummary['comparison']['scope'],
        reference_backend_coordinates: comparison.reference_backend_coordinates === null ? null : object(comparison.reference_backend_coordinates, 'State-analysis reference selector is malformed'),
        reference_candidate_id: comparison.reference_candidate_id === null ? null : string(comparison.reference_candidate_id, 'State-analysis reference candidate is malformed'),
    };
    if ((parsedComparison.mode === 'pairwise' && (parsedComparison.scope !== 'all_within_target' || parsedComparison.reference_candidate_id !== null || parsedComparison.reference_backend_coordinates !== null))
        || (parsedComparison.mode === 'reference' && (parsedComparison.scope !== 'all_other_within_target' || !parsedComparison.reference_candidate_id || !parsedComparison.reference_backend_coordinates))) {
        throw new Error('State-analysis comparison authority is inconsistent');
    }
    const counts = object(summary.counts, 'State-analysis counts are malformed');
    exact(counts, ['pairs', 'rows', 'exclusions'], 'State-analysis counts are malformed');
    const parsedCounts = { pairs: integer(counts.pairs, 'State-analysis pair count is malformed', 1), rows: integer(counts.rows, 'State-analysis row count is malformed', 0), exclusions: integer(counts.exclusions, 'State-analysis exclusion count is malformed', 0) };
    const pairs = array(summary.pairs, 'State-analysis pair ledger is malformed').map(validatePair);
    if (pairs.length !== parsedCounts.pairs || new Set(pairs.map(pairKey)).size !== pairs.length) throw new Error('State-analysis pair ledger is inconsistent');
    const artifact = summary.artifact === null ? null : (() => {
        const descriptor = object(summary.artifact, 'State-analysis artifact descriptor is malformed');
        exact(descriptor, ['artifact_id', 'content_sha256', 'size_bytes', 'media_type', 'download_url'], 'State-analysis artifact descriptor is malformed');
        const parsed = { artifact_id: string(descriptor.artifact_id, 'State-analysis artifact ID is missing'), content_sha256: sha(descriptor.content_sha256, 'State-analysis artifact hash is malformed'), size_bytes: integer(descriptor.size_bytes, 'State-analysis artifact size is malformed', 0), media_type: string(descriptor.media_type, 'State-analysis artifact media type is missing'), download_url: string(descriptor.download_url, 'State-analysis artifact URL is missing') };
        if (parsed.content_sha256 !== parsedAuthority.content_sha256) throw new Error('State-analysis artifact descriptor does not bind authority');
        return parsed;
    })();
    if (canonicalAuthority) {
        if (canonicalAuthority.analysis_id !== analysisId
            || canonicalAuthority.source_ensemble_sha256 !== parsedAuthority.source_ensemble_sha256
            || canonicalAuthority.source_landscape_sha256 !== parsedAuthority.source_landscape_sha256
            || canonicalAuthority.source_structure_map_sha256 !== parsedAuthority.source_structure_map_sha256
            || canonicalAuthority.comparison_sha256 !== parsedAuthority.comparison_sha256
            || canonicalAuthority.formula_version !== parsedAuthority.formula_version
            || canonicalAuthority.formula_sha256 !== parsedAuthority.formula_sha256
            || canonicalAuthority.policy_sha256 !== parsedAuthority.policy_sha256
            || !equalJson(canonicalAuthority.resolved_pairs, pairs)
            || canonicalAuthority.rows.length !== parsedCounts.rows
            || canonicalAuthority.exclusion_ledger.length !== parsedCounts.exclusions
            || canonicalAuthority.comparison_mode !== parsedComparison.mode
            || canonicalAuthority.comparison_target_id !== parsedComparison.target_id
            || canonicalAuthority.comparison_scope !== parsedComparison.scope
            || canonicalAuthority.reference_candidate_id !== parsedComparison.reference_candidate_id
            || !equalJson(canonicalAuthority.reference_backend_coordinates, parsedComparison.reference_backend_coordinates)) {
            throw new Error('State-analysis B2 projection does not bind canonical authority');
        }
    }
    return { request_id: requestId, analysis_id: analysisId, authority: parsedAuthority, comparison: parsedComparison, counts: parsedCounts, pairs, artifact };
};

/** Validates one authoritative pair-filtered B2 page; no rows, metrics, or ordering are computed in the browser. */
export const validateStateLandscapeWorkspaceRowsPage = (
    value: unknown,
    summary: CmStateLandscapeAnalysisSummary,
    selectedPairId: string,
    expectedOffset: number,
): CmStateLandscapeAnalysisRowsPage => {
    const page = object(value, 'State-analysis rows page is malformed');
    exact(page, ['request_id', 'selected_analysis_id', 'offset', 'limit', 'applied_filters', 'next_offset', 'rows'], 'State-analysis rows page is malformed');
    if (page.request_id !== summary.request_id || page.selected_analysis_id !== summary.analysis_id || page.offset !== expectedOffset) throw new Error('State-analysis rows page identity is inconsistent');
    const limit = integer(page.limit, 'State-analysis rows page limit is malformed', 1);
    const filters = object(page.applied_filters, 'State-analysis rows filters are malformed');
    exact(filters, ['pair_id', 'candidate_id', 'entity_instance_id', 'auth_asym_id', 'sequence_start', 'sequence_end'], 'State-analysis rows filters are malformed');
    if (filters.pair_id !== selectedPairId || filters.candidate_id !== null || filters.entity_instance_id !== null || filters.auth_asym_id !== null || filters.sequence_start !== null || filters.sequence_end !== null) {
        throw new Error('State-analysis rows page is not scoped to one selected pair');
    }
    const selectedPair = summary.pairs.find((pair) => pair.pair_id === selectedPairId);
    if (!selectedPair) throw new Error('Selected state-analysis pair is absent from authority');
    const rows = array(page.rows, 'State-analysis rows are malformed').map((row) => validateRow(row, selectedPair, summary.comparison.target_id));
    const nextOffset = page.next_offset === null ? null : integer(page.next_offset, 'State-analysis next offset is malformed', expectedOffset + 1);
    if (rows.length > limit || (nextOffset !== null && nextOffset !== expectedOffset + rows.length)) {
        throw new Error('State-analysis rows page bounds are malformed');
    }
    return { request_id: summary.request_id, selected_analysis_id: summary.analysis_id, offset: expectedOffset, limit, applied_filters: filters as CmStateLandscapeAnalysisRowsPage['applied_filters'], next_offset: nextOffset, rows };
};

export interface StateLandscapeWorkspaceState { selectedPairId: string; selectedStateRowKey: string | null; pageOffset: number; }
export const initialStateLandscapeWorkspaceState = (summary: CmStateLandscapeAnalysisSummary): StateLandscapeWorkspaceState => ({ selectedPairId: summary.pairs[0]?.pair_id ?? '', selectedStateRowKey: null, pageOffset: 0 });
export const selectStateLandscapeWorkspacePair = (_state: StateLandscapeWorkspaceState, selectedPairId: string): StateLandscapeWorkspaceState => ({ selectedPairId, selectedStateRowKey: null, pageOffset: 0 });
export const selectStateLandscapeWorkspaceRow = (state: StateLandscapeWorkspaceState, selectedStateRowKey: string): StateLandscapeWorkspaceState => ({ ...state, selectedStateRowKey });
/** Candidate inspection belongs to the parent viewer; pair/residue selection intentionally stays unchanged. */
export const inspectStateLandscapeWorkspaceCandidate = (state: StateLandscapeWorkspaceState): StateLandscapeWorkspaceState => state;
export const stateLandscapeRowKey = (row: Pick<CmStateLandscapeRow, 'pair_id' | 'candidate_a_id' | 'candidate_b_id' | 'identity'>): string => JSON.stringify([row.pair_id, row.candidate_a_id, row.candidate_b_id, row.identity.target_id, row.identity.entity_instance_id, row.identity.auth_asym_id, row.identity.auth_seq_id, row.identity.insertion_code, row.identity.sequence_index, row.identity.validated_wt]);
export const stateLandscapeMetricText = (metric: CmStateLandscapeNumericMetric | CmStateLandscapeClassMetric): string => metric.status === 'unavailable' ? `Unavailable: ${metric.reason}` : `${metric.a} → ${metric.b}${'delta_b_minus_a' in metric ? ` (Δ ${metric.delta_b_minus_a})` : ` (${metric.transition})`}`;

/** Maps only an exact persisted identity to the selected candidate's structure map; ambiguous/missing mappings return null. */
export const resolveStateLandscapeResidueRef = (identity: CmStateLandscapeIdentity, structureMap: CmStructureMap | null): ResidueRef | null => {
    if (!structureMap || structureMap.target_id !== identity.target_id) return null;
    const matches = structureMap.rows.filter((row) => row.status === 'mapped'
        && row.entity_instance_id === identity.entity_instance_id
        && row.auth_asym_id === identity.auth_asym_id
        && row.auth_seq_id === identity.auth_seq_id
        && row.insertion_code === identity.insertion_code
        && row.sequence_index === identity.sequence_index);
    if (matches.length !== 1) return null;
    const row = matches[0];
    return { documentId: 'primary', entityId: row.source_entity_id, sourceEntityId: row.source_entity_id, sourceInstanceId: row.entity_instance_id, labelAsymId: row.label_asym_id, authAsymId: row.auth_asym_id, labelSeqId: row.label_seq_id, authSeqId: row.auth_seq_id, insertionCode: row.insertion_code };
};
