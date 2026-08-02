import type { CmLandscapePage, CmLandscapeRow } from './conformationalMappingApi.js';
import {
    groupExact20Landscape,
    type CmLandscapeResidue,
} from './conformationalMappingSemantics.js';
import type { ResidueRef } from '../../structureViewer/contracts/structureIdentity.js';
import type { MetricLayer, MetricProvenance, MetricValue } from '../../structureViewer/metrics/metricContracts.js';

const CLASS_COLORS: Readonly<Record<string, string>> = {
    high: '#dc2626',
    neutral: '#f59e0b',
    minimal: '#0ea5e9',
};
const MAX_LANDSCAPE_ROWS = 200_000;
const PAGE_SIZE = 500;

type ResidueMetric = Extract<MetricLayer, { descriptor: { dimension: 'residue-scalar' } }>;

export interface FrustraMpnnViewerMetricInput {
    readonly requestId: string;
    readonly candidateId: string;
    readonly residues: readonly CmLandscapeResidue[];
    readonly structureMap: FrustraMpnnMetricStructureMap;
}

export interface FrustraMpnnStructureMapRow {
    readonly entity_instance_id: string;
    readonly source_entity_id: string | null;
    readonly label_asym_id: string | null;
    readonly auth_asym_id: string;
    readonly label_seq_id: number | null;
    readonly auth_seq_id: number;
    readonly insertion_code: string;
    readonly sequence_index: number;
    readonly pdb_chain_id: string;
    readonly pdb_residue_id: number;
    readonly pdb_insertion_code: string;
    readonly model_position: number;
    readonly residue_name: string;
    readonly wt: string | null;
    readonly selected_model: number;
    readonly selected_altloc: string;
    readonly backbone_complete: boolean;
    readonly backbone_atoms: { readonly N: string | null; readonly CA: string | null; readonly C: string | null; readonly O: string | null };
    readonly status: 'mapped' | 'missing_backbone' | 'nonstandard_residue' | 'excluded';
    readonly reason: string | null;
}

export interface FrustraMpnnStructureMap {
    readonly schema_name: 'frustrampnn_structure_map';
    readonly schema_version: 1;
    readonly target_id: string;
    readonly parent_job_id: string;
    readonly candidate_id: string;
    readonly source_format: 'pdb' | 'mmcif';
    readonly source_sha256: string;
    readonly source_bytes: number;
    readonly identity_authority: 'pdb_self_identity_v1' | 'mmcif_atom_site_v1' | 'producer_manifest_v1' | 'cm_complex_snapshot_v1';
    readonly identity_domain: 'candidate_local' | 'source_authoritative';
    readonly authority_artifact_sha256: string;
    readonly normalized_pdb_sha256: string;
    readonly selected_source_model: number;
    readonly altloc_policy: string;
    readonly normalizer_version: 'frustrampnn_structure_normalizer_v1';
    readonly model_ready_sequence: string;
    readonly model_ready_sequence_sha256: string;
    readonly excluded_records: readonly {
        readonly source_identity: string;
        readonly reason_code: 'non_protein_entity' | 'not_selected' | 'missing_backbone' | 'nonstandard_residue' | 'unsupported_record';
        readonly reason: string;
    }[];
    readonly rows: readonly FrustraMpnnStructureMapRow[];
}

type FrustraMpnnMetricStructureMapRow = Pick<
    FrustraMpnnStructureMapRow,
    | 'entity_instance_id'
    | 'source_entity_id'
    | 'label_asym_id'
    | 'auth_asym_id'
    | 'label_seq_id'
    | 'auth_seq_id'
    | 'insertion_code'
    | 'sequence_index'
> & {
    readonly status: string;
    readonly wt?: string | null;
};

interface FrustraMpnnMetricStructureMap {
    readonly candidate_id: string;
    readonly rows: readonly FrustraMpnnMetricStructureMapRow[];
}

export interface FrustraMpnnViewerMetricBundle {
    readonly layers: readonly [ResidueMetric, ResidueMetric, ResidueMetric];
    readonly residueByIdentityKey: ReadonlyMap<string, CmLandscapeResidue>;
    readonly residueProfiles: readonly { readonly identity: ResidueRef; readonly residue: CmLandscapeResidue }[];
}

const authorIdentityKey = (authAsymId: string, authSeqId: string | number, insertionCode: string): string => (
    `${authAsymId}\u0000${String(authSeqId)}\u0000${insertionCode}`
);

const authorIdentityKeyForResidue = (residue: CmLandscapeResidue): string => (
    authorIdentityKey(residue.auth_asym_id, residue.auth_seq_id, residue.insertion_code)
);

const finiteOk = (slot: CmLandscapeRow): slot is CmLandscapeRow & { score: number } => (
    slot.status === 'ok' && slot.scoreable && typeof slot.score === 'number' && Number.isFinite(slot.score)
);

const provenanceString = (row: CmLandscapeRow, key: string): string | undefined => {
    const value = row.provenance[key];
    return typeof value === 'string' && value.trim() ? value : undefined;
};

const sharedProvenance = (
    requestId: string,
    candidateId: string,
    residues: readonly CmLandscapeResidue[],
): MetricProvenance => {
    const first = residues[0]?.slots[0];
    if (!first) throw new Error('Canonical FrustraMPNN landscape is empty');
    const rawCsvSha256 = provenanceString(first, 'raw_csv_sha256');
    const checkpointSha256 = provenanceString(first, 'checkpoint_sha256');
    const toolSha256 = provenanceString(first, 'tool_sha256');
    const containerSha256 = provenanceString(first, 'container_sha256');
    const thresholdPolicySha256 = provenanceString(first, 'threshold_policy_sha256');
    const expected = { raw_csv_sha256: rawCsvSha256, checkpoint_sha256: checkpointSha256, tool_sha256: toolSha256, container_sha256: containerSha256, threshold_policy_sha256: thresholdPolicySha256 };
    for (const residue of residues) {
        for (const slot of residue.slots) {
            for (const [key, value] of Object.entries(expected)) {
                if (provenanceString(slot, key) !== value) throw new Error(`FrustraMPNN provenance mismatch for ${key}`);
            }
        }
    }
    return {
        source: 'Canonical persisted FrustraMPNN exact-20 landscape',
        sourceVersion: 'cm_frustration_landscape_v1',
        workflowId: requestId,
        artifactId: candidateId,
        artifactSha256: rawCsvSha256,
        parameters: {
            checkpoint_sha256: checkpointSha256 ?? null,
            tool_sha256: toolSha256 ?? null,
            container_sha256: containerSha256 ?? null,
            threshold_policy_sha256: thresholdPolicySha256 ?? null,
        },
    };
};

const authorIdentityKeyForMapRow = (row: FrustraMpnnMetricStructureMapRow): string => (
    authorIdentityKey(row.auth_asym_id, row.auth_seq_id, row.insertion_code)
);

const mappedRows = (structureMap: FrustraMpnnMetricStructureMap): ReadonlyMap<string, FrustraMpnnMetricStructureMapRow> => {
    const rows = new Map<string, FrustraMpnnMetricStructureMapRow>();
    const sourceIdentities = new Set<string>();
    for (const row of structureMap.rows) {
        if (row.status !== 'mapped') continue;
        const sourceKey = `${row.entity_instance_id}\u0000${row.sequence_index}`;
        if (sourceIdentities.has(sourceKey)) {
            throw new Error(`Conflicting mapped structure source identity: ${row.entity_instance_id}:${row.sequence_index}`);
        }
        const authorKey = authorIdentityKeyForMapRow(row);
        if (rows.has(authorKey)) {
            throw new Error(`Ambiguous mapped author residue identity: ${row.auth_asym_id}:${row.auth_seq_id}${row.insertion_code}`);
        }
        sourceIdentities.add(sourceKey);
        rows.set(authorKey, row);
    }
    return rows;
};

const exactIdentity = (residue: CmLandscapeResidue, row: FrustraMpnnMetricStructureMapRow | undefined): ResidueRef => {
    if (!row) throw new Error(`FrustraMPNN identity mismatch: residue has no exact mapped author identity: ${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code}`);
    const authSeqId = Number(residue.auth_seq_id);
    if (!Number.isInteger(authSeqId)
        || row.auth_asym_id !== residue.auth_asym_id
        || row.auth_seq_id !== authSeqId
        || row.insertion_code !== residue.insertion_code
        || row.entity_instance_id !== residue.entity_instance_id
        || row.sequence_index !== residue.sequence_index
        || (row.wt != null && row.wt !== residue.wt)) {
        throw new Error(`FrustraMPNN/structure-map identity mismatch: ${residue.entity_instance_id}:${residue.sequence_index}`);
    }
    return {
        documentId: 'primary',
        entityId: row.source_entity_id ?? undefined,
        labelAsymId: row.label_asym_id ?? undefined,
        authAsymId: row.auth_asym_id,
        labelSeqId: row.label_seq_id ?? undefined,
        authSeqId: row.auth_seq_id,
        insertionCode: row.insertion_code || undefined,
        sourceInstanceId: row.entity_instance_id,
    };
};

const unavailable = (identity: ResidueRef): MetricValue<ResidueRef> => ({ identity, value: null, missingness: 'unavailable' });

const validateExactResidueSlots = (
    candidateId: string,
    residue: CmLandscapeResidue,
): void => {
    for (const slot of residue.slots) {
        if (slot.candidate_id !== candidateId
            || slot.entity_instance_id !== residue.entity_instance_id
            || slot.auth_asym_id !== residue.auth_asym_id
            || slot.auth_seq_id !== residue.auth_seq_id
            || slot.insertion_code !== residue.insertion_code
            || slot.sequence_index !== residue.sequence_index
            || slot.wt !== residue.wt) {
            throw new Error(`FrustraMPNN exact-20 candidate, author identity, or wild-type conflict: ${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code}`);
        }
    }
};

export const createFrustraMpnnViewerMetrics = (input: FrustraMpnnViewerMetricInput): FrustraMpnnViewerMetricBundle => {
    if (input.structureMap.candidate_id !== input.candidateId) throw new Error('FrustraMPNN/structure-map candidate identity mismatch');
    if (input.residues.length === 0) throw new Error('Canonical FrustraMPNN landscape is empty');
    const mapRows = mappedRows(input.structureMap);
    const provenance = sharedProvenance(input.requestId, input.candidateId, input.residues);
    const nativeValues: MetricValue<ResidueRef>[] = [];
    const highFractionValues: MetricValue<ResidueRef>[] = [];
    const maximumDeltaValues: MetricValue<ResidueRef>[] = [];
    const residueByIdentityKey = new Map<string, CmLandscapeResidue>();
    const residueProfiles: Array<{ identity: ResidueRef; residue: CmLandscapeResidue }> = [];

    for (const residue of input.residues) {
        validateExactResidueSlots(input.candidateId, residue);
        const exactAuthorKey = authorIdentityKeyForResidue(residue);
        if (residueByIdentityKey.has(exactAuthorKey)) {
            throw new Error(`Duplicate FrustraMPNN exact author residue identity: ${residue.auth_asym_id}:${residue.auth_seq_id}${residue.insertion_code}`);
        }
        const identity = exactIdentity(residue, mapRows.get(authorIdentityKeyForResidue(residue)));
        residueByIdentityKey.set(exactAuthorKey, residue);
        residueProfiles.push({ identity, residue });
        const native = residue.slots.find((slot) => slot.mutation_aa === residue.wt);
        if (!native) throw new Error(`FrustraMPNN residue lacks its native exact-20 slot: ${residue.key}`);
        const nonNative = residue.slots.filter((slot) => slot.mutation_aa !== residue.wt && finiteOk(slot));

        nativeValues.push(finiteOk(native)
            ? { identity, value: native.score, displayColor: CLASS_COLORS[native.class ?? ''] ?? '#64748b' }
            : unavailable(identity));
        highFractionValues.push(nonNative.length > 0
            ? { identity, value: nonNative.filter((slot) => slot.class === 'high').length / nonNative.length }
            : unavailable(identity));
        maximumDeltaValues.push(finiteOk(native) && nonNative.length > 0
            ? { identity, value: Math.max(...nonNative.map((slot) => slot.score as number)) - (native.score as number) }
            : unavailable(identity));
    }

    const base = {
        dimension: 'residue-scalar' as const,
        units: null,
        projectionPolicy: 'direct' as const,
        normalization: 'none' as const,
        provenance,
    };
    const layers: FrustraMpnnViewerMetricBundle['layers'] = [
        {
            descriptor: {
                ...base,
                id: 'frustrampnn-native-index',
                label: 'FrustraMPNN native score',
                direction: 'neutral',
                description: 'Native-amino-acid backbone-context model score for each canonically mapped residue.',
                semantics: 'FrustraMPNN backbone-context model score; not a physical free energy or functional-effect measurement.',
                formula: 'score(slot where mutation_aa = wild type)',
                valueRange: [-3, 3],
                categories: {
                    high: { label: 'Highly frustrated (canonical policy)', color: CLASS_COLORS.high },
                    neutral: { label: 'Neutral (canonical policy)', color: CLASS_COLORS.neutral },
                    minimal: { label: 'Minimally frustrated (canonical policy)', color: CLASS_COLORS.minimal },
                },
                palette: { colors: [CLASS_COLORS.high, CLASS_COLORS.neutral, CLASS_COLORS.minimal], domain: [-3, 3], missingColor: '#475569' },
            },
            values: nativeValues,
        },
        {
            descriptor: {
                ...base,
                id: 'frustrampnn-high-substitution-fraction',
                label: 'Highly frustrated substitution fraction',
                direction: 'lower_is_better',
                description: 'Fraction of scoreable non-native substitutions classified as highly frustrated at each residue.',
                semantics: 'Derived from exact-20 FrustraMPNN classes. The native slot and unscoreable substitutions are excluded.',
                formula: 'count(non-native class = high) / count(scoreable non-native substitutions)',
                valueRange: [0, 1],
                palette: { colors: ['#0ea5e9', '#f59e0b', '#dc2626'], domain: [0, 1], missingColor: '#475569' },
            },
            values: highFractionValues,
        },
        {
            descriptor: {
                ...base,
                id: 'frustrampnn-maximum-substitution-delta',
                label: 'Maximum substitution score delta',
                direction: 'neutral',
                description: 'Largest model-score change among scoreable non-native substitutions relative to the native score.',
                semantics: 'Derived model-score contrast only; it is not a predicted beneficial mutation, ΔΔG, or functional effect.',
                formula: 'max(scoreable non-native score) - native score',
                valueRange: [-6, 6],
                palette: { colors: ['#7c3aed', '#f8fafc', '#0891b2'], domain: [-6, 6], missingColor: '#475569' },
            },
            values: maximumDeltaValues,
        },
    ];
    return { layers, residueByIdentityKey, residueProfiles };
};

const sameDefined = <T,>(selected: T | undefined, expected: T | undefined): boolean => (
    selected === undefined || (expected !== undefined && selected === expected)
);

export const resolveFrustraMpnnResidueProfile = (
    bundle: FrustraMpnnViewerMetricBundle,
    selected: ResidueRef,
): CmLandscapeResidue | undefined => {
    if (!selected.authAsymId || !Number.isInteger(selected.authSeqId)) return undefined;
    return bundle.residueProfiles.find(({ identity }) => (
        identity.authAsymId === selected.authAsymId
        && identity.authSeqId === selected.authSeqId
        && (identity.insertionCode ?? '') === (selected.insertionCode ?? '')
        && sameDefined(selected.documentId, identity.documentId)
        && sameDefined(selected.labelAsymId, identity.labelAsymId)
        && sameDefined(selected.labelSeqId, identity.labelSeqId)
    ))?.residue;
};

export type FrustraMpnnLandscapePageLoader = (offset: number, limit: number) => Promise<CmLandscapePage>;

export const collectCompleteFrustraMpnnLandscape = async (
    loadPage: FrustraMpnnLandscapePageLoader,
    maxRows = MAX_LANDSCAPE_ROWS,
): Promise<CmLandscapeRow[]> => {
    const rows: CmLandscapeRow[] = [];
    const visited = new Set<number>();
    let offset = 0;
    let candidateId: string | null | undefined;
    while (true) {
        if (visited.has(offset)) throw new Error('FrustraMPNN landscape pagination is not monotonic');
        visited.add(offset);
        const page = await loadPage(offset, PAGE_SIZE);
        if (page.offset !== offset) throw new Error('FrustraMPNN landscape page offset mismatch');
        candidateId ??= page.candidate_id;
        if (!candidateId || page.candidate_id !== candidateId || page.rows.some((row) => row.candidate_id !== candidateId)) {
            throw new Error('FrustraMPNN landscape candidate identity mismatch');
        }
        if (rows.length + page.rows.length > maxRows) throw new Error('FrustraMPNN landscape exceeds bounded viewer capacity');
        rows.push(...page.rows);
        if (page.next_offset == null) break;
        if (!Number.isInteger(page.next_offset) || page.next_offset <= offset) throw new Error('FrustraMPNN landscape pagination is not monotonic');
        offset = page.next_offset;
    }
    groupExact20Landscape(rows);
    return rows;
};
