import type { CmArtifact, CmLandscapeRow, CmLegacyLandscapeRow, CmRecord, CmResults } from './conformationalMappingApi';

export const APPROVED_CM_CONTRACTS = new Set([
    'conformational_mapping_protenix_v1',
    'conformational_mapping_confornets_v1',
    'conformational_mapping_import_v1',
    'conformational_mapping_analysis_v1',
    'conformational_mapping_resampling_v1',
]);

export const CANONICAL_AMINO_ACIDS = ['A', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'K', 'L', 'M', 'N', 'P', 'Q', 'R', 'S', 'T', 'V', 'W', 'Y'] as const;
const SHA256 = /^[0-9a-f]{64}$/;

export interface CmCandidate {
    candidate_id: string;
    backend_coordinates: Record<string, unknown>;
    authoritative_structure_path: string;
    authoritative_structure_sha256: string;
    sidecar_paths: string[];
}

export interface CmEnsemble {
    schema_name: 'cm_ensemble';
    schema_version: 1;
    request_id: string;
    request_sha256: string;
    source_snapshot_sha256: string;
    backend: string;
    runtime_identity: string;
    container_digest: string;
    checkpoint_sha256: string;
    feature_policy_sha256: string;
    expected_cardinality: number;
    expected_coordinates: Array<Record<string, unknown>>;
    candidates: CmCandidate[];
    native_manifest_path: string;
    native_manifest_sha256: string;
    warnings: string[];
    omissions: string[];
    terminal_status: string;
    started_at: string;
    completed_at: string;
    resumable: boolean;
    resume_key: string;
}

export interface CmStructureMapRow {
    entity_instance_id: string;
    source_entity_id: string;
    source_model: number;
    label_asym_id: string;
    auth_asym_id: string;
    label_seq_id: number;
    auth_seq_id: number;
    insertion_code: string;
    residue_name: string;
    sequence_index: number;
    pdb_chain_id: string;
    pdb_residue_id: number;
    pdb_insertion_code: string;
    model_position: number;
    backbone_atoms: Record<string, string | null>;
    selected_altloc: string;
    model_decision: string;
    status: string;
    reason: string | null;
}

export interface CmStructureMap {
    target_id: string;
    candidate_id: string;
    original_cif_sha256: string;
    source_format: string;
    source_sha256: string;
    source_bytes: number;
    normalized_pdb_sha256: string;
    selected_source_model: number;
    altloc_policy: string;
    normalizer_version: string;
    rows: CmStructureMapRow[];
}

export interface CmAnalysisResult {
    source_row_key: string;
    identity: Record<string, unknown>;
    status: 'robust' | 'conditional' | 'insufficient_support';
    expected_coordinate_count: number;
    valid_coordinate_count: number;
    outer_support_fraction: number;
    coordinate_support_fraction: number;
    hierarchical_mean: number | null;
    hotspot_score: number | null;
    switch_score: number | null;
    failure_reason: string | null;
    components: Record<string, unknown>;
    sort_keys: Record<string, unknown>;
}

export interface CmAnalysis {
    analysis_id: string;
    source_ensemble_sha256: string;
    source_landscape_sha256: string;
    formula_version: string;
    expected_strata: string[];
    results: CmAnalysisResult[];
    exclusions: Array<Record<string, unknown>>;
    pair_ledger: Array<Record<string, unknown>>;
    support_records: Array<Record<string, unknown>>;
    ranking_policy: Record<string, unknown>;
    clash_records: Array<Record<string, unknown>>;
}

export interface CmLandscapeResidue {
    key: string;
    entity_instance_id: string;
    source_entity_id: string | null;
    label_asym_id: string | null;
    label_seq_id: number | null;
    auth_asym_id: string;
    auth_seq_id: string;
    insertion_code: string;
    sequence_index: number;
    wt: string;
    pdb_chain_id: string;
    pdb_residue_id: number | null;
    pdb_insertion_code: string | null;
    model_position: number;
    residue_name: string | null;
    slots: CmLandscapeRow[];
}

export interface LegacyCmLandscapeResidue {
    key: string;
    candidate_id: string;
    entity_instance_id: string;
    auth_asym_id: string;
    auth_seq_id: string;
    insertion_code: string;
    sequence_index: number;
    wt: string;
    slots: CmLegacyLandscapeRow[];
    provenance: Record<string, unknown>;
}

const object = (value: unknown, message: string): Record<string, unknown> => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(message);
    return value as Record<string, unknown>;
};
const string = (value: unknown, message: string): string => {
    if (typeof value !== 'string' || !value) throw new Error(message);
    return value;
};
const sha = (value: unknown, message: string): string => {
    const result = string(value, message);
    if (!SHA256.test(result)) throw new Error(message);
    return result;
};
const integer = (value: unknown, message: string, minimum?: number): number => {
    if (!Number.isInteger(value) || (minimum !== undefined && (value as number) < minimum)) throw new Error(message);
    return value as number;
};
const validateCoordinates = (value: Record<string, unknown>): void => {
    string(value.target_id, 'Candidate target coordinate is missing');
    if (value.backend === 'protenix_v2_ensemble') {
        const seedValue = integer(value.ordered_seed, 'Protenix seed coordinate is malformed');
        if (seedValue < -2147483648 || seedValue > 2147483647) throw new Error('Protenix seed coordinate is outside signed 32-bit range');
        integer(value.sample_index, 'Protenix sample coordinate is malformed', 0);
        return;
    }
    if (value.backend === 'confornets') {
        if (!['diversity', 'mse', 'transfer'].includes(string(value.task, 'ConforNets task coordinate is missing'))) {
            throw new Error('ConforNets task coordinate is unknown');
        }
        string(value.test_case_id, 'ConforNets test-case coordinate is missing');
        if (value.reference_id !== null && typeof value.reference_id !== 'string') throw new Error('ConforNets reference coordinate is malformed');
        ['run_index', 'saved_step', 'confornet_index', 'sample_index'].forEach((key) => integer(value[key], `ConforNets ${key} coordinate is malformed`, 0));
        return;
    }
    if (value.backend === 'external_import') {
        integer(value.staged_index, 'Import staged coordinate is malformed', 0);
        sha(value.source_content_sha256, 'Import source hash coordinate is malformed');
        sha(value.staged_receipt_sha256, 'Import receipt hash coordinate is malformed');
        return;
    }
    throw new Error('Unknown candidate backend coordinate');
};
const sameFlatRecord = (left: Record<string, unknown>, rightValue: unknown): boolean => {
    if (!rightValue || typeof rightValue !== 'object' || Array.isArray(rightValue)) return false;
    const right = rightValue as Record<string, unknown>;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
        && leftKeys.every((key, index) => key === rightKeys[index] && left[key] === right[key]);
};
const record = (records: CmRecord[], type: string, key?: string): CmRecord => {
    const matches = records.filter((item) => item.type === type && (key === undefined || item.key === key));
    if (matches.length !== 1) throw new Error(`Canonical ${type} authority is missing or ambiguous`);
    return matches[0];
};

export const requireApprovedCmResults = (results: CmResults): CmResults => {
    if (!results || typeof results.request_id !== 'string' || !APPROVED_CM_CONTRACTS.has(results.result_contract_id)) {
        throw new Error('Unknown conformational-mapping result contract');
    }
    if (!Array.isArray(results.records) || !Array.isArray(results.artifacts)) {
        throw new Error('Malformed conformational-mapping result response');
    }
    const recordIdentities = new Set<string>();
    results.records.forEach((item) => {
        if (!item || typeof item.type !== 'string' || typeof item.key !== 'string' || !SHA256.test(item.sha256)) {
            throw new Error('Malformed canonical record identity');
        }
        if (item.payload !== undefined && (!item.payload || typeof item.payload !== 'object' || Array.isArray(item.payload))) {
            throw new Error('Malformed canonical record payload');
        }
        if (item.artifact !== undefined && item.artifact !== null
            && (!item.artifact.artifact_id || !SHA256.test(item.artifact.content_sha256)
                || !Number.isInteger(item.artifact.size_bytes) || item.artifact.size_bytes < 0
                || !Number.isInteger(item.artifact.row_count) || item.artifact.row_count < 0
                || !item.artifact.relative_path || item.artifact.relative_path.startsWith('/'))) {
            throw new Error('Malformed canonical record artifact identity');
        }
        const identity = `${item.type}\u0000${item.key}`;
        if (recordIdentities.has(identity)) throw new Error('Duplicate canonical record identity');
        recordIdentities.add(identity);
    });
    const artifactIds = new Set<string>();
    results.artifacts.forEach((item) => {
        if (!item?.artifact_id || artifactIds.has(item.artifact_id) || !SHA256.test(item.sha256)
            || !Number.isInteger(item.bytes) || item.bytes < 0 || !item.relative_path || item.relative_path.startsWith('/')) {
            throw new Error('Malformed content-addressed artifact identity');
        }
        artifactIds.add(item.artifact_id);
    });
    return results;
};

export const canonicalEnsemble = (results: CmResults): CmEnsemble => {
    requireApprovedCmResults(results);
    const payload = object(record(results.records, 'ensemble', 'primary').payload, 'Canonical ensemble is malformed');
    if (payload.schema_name !== 'cm_ensemble' || payload.schema_version !== 1 || payload.request_id !== results.request_id) {
        throw new Error('Canonical ensemble identity does not match the API request');
    }
    const candidates = payload.candidates;
    const expectedCoordinates = payload.expected_coordinates;
    if (!Array.isArray(candidates) || !Array.isArray(expectedCoordinates) || !Number.isInteger(payload.expected_cardinality)
        || candidates.length !== payload.expected_cardinality || expectedCoordinates.length !== payload.expected_cardinality) {
        throw new Error('Canonical ensemble cardinality is malformed');
    }
    const identities = new Set<string>();
    candidates.forEach((candidateValue, index) => {
        const candidate = object(candidateValue, 'Canonical candidate is malformed');
        const candidateId = string(candidate.candidate_id, 'Canonical candidate identity is missing');
        if (identities.has(candidateId)) throw new Error('Canonical candidate identity is duplicated');
        identities.add(candidateId);
        const coordinates = object(candidate.backend_coordinates, 'Canonical backend coordinates are missing');
        validateCoordinates(coordinates);
        if (!sameFlatRecord(coordinates, expectedCoordinates[index])) {
            throw new Error('Candidate order does not match the API coordinate authority');
        }
        if (coordinates.backend !== payload.backend) throw new Error('Candidate backend coordinate is inconsistent');
        string(candidate.authoritative_structure_path, 'Candidate structure authority is missing');
        sha(candidate.authoritative_structure_sha256, 'Candidate structure hash is malformed');
        const expectedSidecarCount = payload.backend === 'external_import' ? 0 : 2;
        if (!Array.isArray(candidate.sidecar_paths) || candidate.sidecar_paths.length !== expectedSidecarCount
            || candidate.sidecar_paths.some((item) => typeof item !== 'string' || !item)) {
            throw new Error('Candidate sidecar authority is malformed');
        }
    });
    sha(payload.request_sha256, 'Ensemble request hash is malformed');
    sha(payload.source_snapshot_sha256, 'Ensemble snapshot hash is malformed');
    sha(payload.feature_policy_sha256, 'Ensemble feature-policy hash is malformed');
    string(payload.runtime_identity, 'Ensemble runtime identity is missing');
    string(payload.container_digest, 'Ensemble container identity is missing');
    sha(payload.checkpoint_sha256, 'Ensemble checkpoint hash is malformed');
    sha(payload.native_manifest_sha256, 'Ensemble native-manifest hash is malformed');
    if (!Array.isArray(payload.warnings) || payload.warnings.some((item) => typeof item !== 'string')
        || !Array.isArray(payload.omissions) || payload.omissions.some((item) => typeof item !== 'string')) {
        throw new Error('Ensemble warnings are malformed');
    }
    return payload as unknown as CmEnsemble;
};

export const ensembleCandidates = (results: CmResults): CmCandidate[] => canonicalEnsemble(results).candidates;

export const candidateStructureArtifact = (candidate: CmCandidate, artifacts: CmArtifact[]): CmArtifact => {
    const matches = artifacts.filter((artifact) => artifact.candidate_id === candidate.candidate_id
        && artifact.role === 'authoritative_cif'
        && artifact.relative_path === candidate.authoritative_structure_path);
    if (matches.length !== 1 || matches[0].sha256 !== candidate.authoritative_structure_sha256) {
        throw new Error('Candidate structure artifact does not match API identity');
    }
    return matches[0];
};

export const candidateLabel = (candidate: CmCandidate): string => {
    const coordinate = candidate.backend_coordinates;
    const backend = coordinate.backend;
    const target = string(coordinate.target_id, 'Candidate target coordinate is missing');
    if (backend === 'protenix_v2_ensemble') {
        return `${target} · seed ${String(coordinate.ordered_seed)} · sample ${Number(coordinate.sample_index) + 1}`;
    }
    if (backend === 'confornets') {
        const reference = coordinate.reference_id == null ? '' : ` · reference ${String(coordinate.reference_id)}`;
        return `${target} · ${String(coordinate.task)}${reference} · run ${Number(coordinate.run_index) + 1} · step ${String(coordinate.saved_step)} · network ${Number(coordinate.confornet_index) + 1} · sample ${Number(coordinate.sample_index) + 1}`;
    }
    if (backend === 'external_import') return `${target} · imported candidate ${Number(coordinate.staged_index) + 1}`;
    throw new Error('Unknown backend coordinate');
};

export const candidateStructureMap = (results: CmResults, candidateId: string): CmStructureMap => {
    const payload = object(record(results.records, 'structure_map', candidateId).payload, 'Structure map is malformed');
    if (payload.schema_name !== 'cm_structure_map' || payload.schema_version !== 1 || payload.candidate_id !== candidateId
        || !Array.isArray(payload.rows) || payload.rows.length === 0) throw new Error('Structure-map identity does not match the selected candidate');
    sha(payload.original_cif_sha256, 'Structure-map original hash is malformed');
    sha(payload.source_sha256, 'Structure-map source hash is malformed');
    sha(payload.normalized_pdb_sha256, 'Structure-map normalized hash is malformed');
    string(payload.source_format, 'Structure-map source format is missing');
    string(payload.normalizer_version, 'Structure-map normalizer identity is missing');
    string(payload.altloc_policy, 'Structure-map alternate-location policy is missing');
    validateStructureMapRows(payload.rows);
    return payload as unknown as CmStructureMap;
};

export const validateStructureMapRows = (values: unknown[]): CmStructureMapRow[] => {
    values.forEach((value) => {
        const row = object(value, 'Structure-map row is malformed');
        string(row.entity_instance_id, 'Structure-map entity instance is missing');
        string(row.label_asym_id, 'Structure-map label chain is missing');
        string(row.auth_asym_id, 'Structure-map author chain is missing');
        integer(row.sequence_index, 'Structure-map sequence identity is malformed', 1);
        if (!['mapped', 'missing_backbone', 'nonstandard_residue', 'mapping_failed'].includes(String(row.status))) {
            throw new Error('Structure-map row status is unknown');
        }
    });
    return values as CmStructureMapRow[];
};

export const canonicalAnalysis = (results: CmResults): CmAnalysis => {
    const matches = results.records.filter((item) => item.type === 'analysis');
    if (matches.length === 0) throw new Error('Canonical analysis is explicitly unavailable');
    if (matches.length !== 1) throw new Error('Canonical analysis authority is ambiguous');
    const payload = object(matches[0].payload, 'Canonical analysis is malformed');
    if (payload.schema_name !== 'cm_analysis' || payload.schema_version !== 1 || payload.formula_version !== 'cm_analysis_v1'
        || !Array.isArray(payload.results) || payload.results.length === 0
        || !Array.isArray(payload.expected_strata) || payload.expected_strata.length === 0
        || !Array.isArray(payload.support_records) || !Array.isArray(payload.pair_ledger)
        || !Array.isArray(payload.exclusions) || !Array.isArray(payload.clash_records)
        || !payload.ranking_policy || typeof payload.ranking_policy !== 'object' || Array.isArray(payload.ranking_policy)) {
        throw new Error('Canonical analysis contract is malformed');
    }
    validateCanonicalAnalysisRows(payload.results);
    return payload as unknown as CmAnalysis;
};

export const validateCanonicalAnalysisRows = (values: unknown[]): CmAnalysisResult[] => {
    values.forEach((value) => {
        const row = object(value, 'Analysis result is malformed');
        if (!['robust', 'conditional', 'insufficient_support'].includes(String(row.status))
            || typeof row.components !== 'object' || typeof row.sort_keys !== 'object') throw new Error('Analysis result contract is malformed');
        string(row.source_row_key, 'Analysis row identity is missing');
        const identity = object(row.identity, 'Analysis residue identity is malformed');
        string(identity.target_id, 'Analysis target identity is missing');
        string(identity.entity_instance_id, 'Analysis entity identity is missing');
        string(identity.auth_asym_id, 'Analysis author chain is missing');
        integer(identity.auth_seq_id, 'Analysis author residue is malformed');
        integer(identity.sequence_index, 'Analysis sequence identity is malformed', 1);
        integer(row.expected_coordinate_count, 'Analysis expected support is malformed', 1);
        integer(row.valid_coordinate_count, 'Analysis valid support is malformed', 0);
    });
    return values as CmAnalysisResult[];
};

export const recordsByType = (results: CmResults, type: string): CmRecord[] =>
    requireApprovedCmResults(results).records.filter((item) => item.type === type);

export const groupExact20Landscape = (rows: CmLandscapeRow[]): CmLandscapeResidue[] => {
    if (rows.length === 0) throw new Error('Canonical candidate landscape is explicitly unavailable');
    if (rows.length % CANONICAL_AMINO_ACIDS.length !== 0) throw new Error('Landscape page does not contain complete exact-20 residues');
    const residues: CmLandscapeResidue[] = [];
    for (let offset = 0; offset < rows.length; offset += CANONICAL_AMINO_ACIDS.length) {
        const slots = rows.slice(offset, offset + CANONICAL_AMINO_ACIDS.length);
        const first = slots[0];
        const identity = `${first.entity_instance_id}\u0000${first.sequence_index}`;
        if (slots.some((slot) => `${slot.entity_instance_id}\u0000${slot.sequence_index}` !== identity)
            || slots.some((slot) => slot.source_entity_id !== first.source_entity_id
                || slot.label_asym_id !== first.label_asym_id
                || slot.label_seq_id !== first.label_seq_id
                || slot.pdb_chain_id !== first.pdb_chain_id
                || slot.pdb_residue_id !== first.pdb_residue_id
                || slot.pdb_insertion_code !== first.pdb_insertion_code
                || slot.model_position !== first.model_position
                || slot.residue_name !== first.residue_name
                || slot.auth_asym_id !== first.auth_asym_id
                || slot.auth_seq_id !== first.auth_seq_id
                || slot.insertion_code !== first.insertion_code
                || slot.wt !== first.wt)
            || slots.some((slot, index) => slot.mutation_aa !== CANONICAL_AMINO_ACIDS[index])) {
            throw new Error('Landscape slots do not match the canonical exact-20 API order');
        }
        residues.push({
            key: `${first.entity_instance_id}:${first.sequence_index}`,
            entity_instance_id: first.entity_instance_id, source_entity_id: first.source_entity_id,
            label_asym_id: first.label_asym_id, label_seq_id: first.label_seq_id,
            auth_asym_id: first.auth_asym_id,
            auth_seq_id: first.auth_seq_id, insertion_code: first.insertion_code,
            sequence_index: first.sequence_index, wt: first.wt,
            pdb_chain_id: first.pdb_chain_id, pdb_residue_id: first.pdb_residue_id,
            pdb_insertion_code: first.pdb_insertion_code, model_position: first.model_position,
            residue_name: first.residue_name, slots,
        });
    }
    return residues;
};

const LEGACY_CM_LANDSCAPE_STATUSES = new Set([
    'ok',
    'unscoreable_residue',
    'missing_row',
    'duplicate_row',
    'malformed_row',
    'nonfinite_score',
    'mapping_failed',
    'conformer_missing',
]);

const LEGACY_CM_LANDSCAPE_ROW_KEYS = [
    'candidate_id',
    'entity_instance_id',
    'auth_asym_id',
    'auth_seq_id',
    'insertion_code',
    'sequence_index',
    'wt',
    'mutation_aa',
    'score',
    'class',
    'scoreable',
    'status',
    'reason',
    'provenance',
] as const;
const LEGACY_CM_LANDSCAPE_ROW_KEY_SET = new Set<string>(LEGACY_CM_LANDSCAPE_ROW_KEYS);

const LEGACY_CM_PROVENANCE_REQUIRED_KEYS = [
    'raw_csv_sha256',
    'checkpoint_sha256',
    'tool_sha256',
    'threshold_policy_sha256',
] as const;

const legacyCmProvenanceIdentity = (value: unknown): string => {
    const provenance = object(value, 'Landscape slot provenance is malformed');
    const expectedKeys = Object.hasOwn(provenance, 'container_sha256')
        ? [...LEGACY_CM_PROVENANCE_REQUIRED_KEYS, 'container_sha256']
        : [...LEGACY_CM_PROVENANCE_REQUIRED_KEYS];
    const actualKeys = Object.keys(provenance).sort();
    const sortedExpectedKeys = [...expectedKeys].sort();
    if (actualKeys.length !== sortedExpectedKeys.length
        || actualKeys.some((key, index) => key !== sortedExpectedKeys[index])
        || expectedKeys.some((key) => typeof provenance[key] !== 'string'
            || !SHA256.test(provenance[key] as string))) {
        throw new Error('Landscape slot provenance must contain exact SHA-256 identity fields');
    }
    return JSON.stringify(expectedKeys.map((key) => [key, provenance[key]]));
};

const legacyCmScoreClass = (score: number): 'high' | 'neutral' | 'minimally_frustrated' =>
    score <= -1.0 ? 'high' : score >= 0.58 ? 'minimally_frustrated' : 'neutral';

const validateLegacyCmLandscapeSlot = (
    slot: CmLegacyLandscapeRow,
    expectedCandidateId: string,
    expectedProvenance: string,
): void => {
    const keys = Object.keys(slot);
    if (keys.length !== LEGACY_CM_LANDSCAPE_ROW_KEYS.length
        || keys.some((key) => !LEGACY_CM_LANDSCAPE_ROW_KEY_SET.has(key))
        || LEGACY_CM_LANDSCAPE_ROW_KEYS.some((key) => !Object.hasOwn(slot, key))) {
        throw new Error('Landscape slot fields do not match the historical API contract');
    }
    if (slot.candidate_id !== expectedCandidateId) {
        throw new Error('Landscape slot does not match the selected candidate');
    }
    if (typeof slot.entity_instance_id !== 'string' || !slot.entity_instance_id
        || typeof slot.auth_asym_id !== 'string' || !slot.auth_asym_id
        || typeof slot.auth_seq_id !== 'string' || !/^-?(?:0|[1-9]\d*)$/.test(slot.auth_seq_id)
        || typeof slot.insertion_code !== 'string' || slot.insertion_code.length > 1
        || !Number.isInteger(slot.sequence_index) || slot.sequence_index < 1
        || !CANONICAL_AMINO_ACIDS.includes(slot.wt as typeof CANONICAL_AMINO_ACIDS[number])
        || !CANONICAL_AMINO_ACIDS.includes(slot.mutation_aa as typeof CANONICAL_AMINO_ACIDS[number])) {
        throw new Error('Landscape slot residue identity is malformed');
    }
    const provenance = object(slot.provenance, 'Landscape slot provenance is malformed');
    if (legacyCmProvenanceIdentity(provenance) !== expectedProvenance) {
        throw new Error('Landscape slot provenance is inconsistent');
    }
    if (!LEGACY_CM_LANDSCAPE_STATUSES.has(slot.status)) {
        throw new Error('Landscape slot status is unknown');
    }
    if (typeof slot.scoreable !== 'boolean') {
        throw new Error('Landscape slot scoreable value is malformed');
    }
    if (slot.status === 'ok') {
        if (!slot.scoreable || typeof slot.score !== 'number' || !Number.isFinite(slot.score)
            || slot.class !== legacyCmScoreClass(slot.score) || slot.reason !== null) {
            throw new Error('Landscape slot score, class, or availability semantics are invalid');
        }
        return;
    }
    if (slot.scoreable || slot.score !== null || slot.class !== null
        || typeof slot.reason !== 'string' || !slot.reason) {
        throw new Error('Landscape slot missingness semantics are invalid');
    }
};

export const groupLegacyExact20Landscape = (
    rows: CmLegacyLandscapeRow[],
    expectedCandidateId: string,
): LegacyCmLandscapeResidue[] => {
    if (!expectedCandidateId) throw new Error('Landscape selected candidate is missing');
    if (rows.length === 0) throw new Error('Canonical candidate landscape is explicitly unavailable');
    if (rows.length % CANONICAL_AMINO_ACIDS.length !== 0) {
        throw new Error('Landscape page does not contain complete exact-20 residues');
    }
    const firstProvenance = object(rows[0]?.provenance, 'Landscape slot provenance is malformed');
    const provenanceIdentity = legacyCmProvenanceIdentity(firstProvenance);
    const residues: LegacyCmLandscapeResidue[] = [];
    const seenResidueIdentities = new Set<string>();
    for (let offset = 0; offset < rows.length; offset += CANONICAL_AMINO_ACIDS.length) {
        const slots = rows.slice(offset, offset + CANONICAL_AMINO_ACIDS.length);
        slots.forEach((slot) => validateLegacyCmLandscapeSlot(
            slot,
            expectedCandidateId,
            provenanceIdentity,
        ));
        const first = slots[0];
        const identity = `${first.candidate_id}\u0000${first.entity_instance_id}\u0000${first.auth_asym_id}\u0000${first.auth_seq_id}\u0000${first.insertion_code}\u0000${first.sequence_index}`;
        if (slots.some((slot) => `${slot.candidate_id}\u0000${slot.entity_instance_id}\u0000${slot.auth_asym_id}\u0000${slot.auth_seq_id}\u0000${slot.insertion_code}\u0000${slot.sequence_index}` !== identity)
            || slots.some((slot) => slot.wt !== first.wt)
            || slots.some((slot, index) => slot.mutation_aa !== CANONICAL_AMINO_ACIDS[index])) {
            throw new Error('Landscape slots do not match the canonical exact-20 API order');
        }
        if (seenResidueIdentities.has(identity)) {
            throw new Error('Landscape page repeats one residue identity');
        }
        seenResidueIdentities.add(identity);
        residues.push({
            key: `${first.candidate_id}:${first.entity_instance_id}:${first.auth_asym_id}:${first.auth_seq_id}${first.insertion_code}:${first.sequence_index}`,
            candidate_id: first.candidate_id,
            entity_instance_id: first.entity_instance_id,
            auth_asym_id: first.auth_asym_id,
            auth_seq_id: first.auth_seq_id,
            insertion_code: first.insertion_code,
            sequence_index: first.sequence_index,
            wt: first.wt,
            slots,
            provenance: firstProvenance,
        });
    }
    return residues;
};

export const formatCoordinate = (coordinate: Record<string, unknown>): string =>
    Object.entries(coordinate).map(([key, value]) => `${key.replaceAll('_', ' ')}: ${value == null ? 'none' : String(value)}`).join(' · ');

export const CM_SCIENTIFIC_LIMIT =
    'Independent structural hypotheses with empirical backend confidence and post-hoc FrustraMPNN backbone-context analysis; no physical-state, energetic, or functional-effect interpretation is provided.';
