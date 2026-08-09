export interface FrustraMpnnDimension {
    id: string;
    kind: 'number' | 'fraction' | 'count' | 'boolean' | 'identifier' | 'category';
    description: string | null;
    unit: string | null;
    formula: string | null;
}

interface FrustraMpnnPointBase {
    point_id: string;
    dataset_id: string;
    workflow_family: string;
    job_id: string;
    design_id: string | null;
    candidate_id: string;
    invocation_id: string;
    source_artifact_sha256: string;
    checkpoint_sha256: string | null;
    configuration_id: string | null;
    configuration_sha256: string | null;
    threshold_policy_id: string | null;
}

export interface FrustraMpnnResultPoint extends FrustraMpnnPointBase {
    metrics: {
        mean_score: number | null;
        native_score: number | null;
        high_fraction: number | null;
        minimal_fraction: number | null;
        scoreable_fraction: number | null;
        slot_count: number;
        residue_count: number;
    };
}

interface FrustraMpnnResidueIdentity {
    target_id: string;
    entity_instance_id: string;
    auth_asym_id: string;
    auth_seq_id: string;
    insertion_code: string;
    sequence_index: number;
    wt: string;
}

export interface FrustraMpnnResiduePoint extends FrustraMpnnPointBase, FrustraMpnnResidueIdentity {
    metrics: {
        native_score: number | null;
        alternative_mean_score: number | null;
        best_alternative_delta: number | null;
        worst_alternative_delta: number | null;
        high_alternative_fraction: number | null;
        minimal_alternative_fraction: number | null;
        alternative_count: number;
    };
}

export interface FrustraMpnnMutationPoint extends FrustraMpnnPointBase, FrustraMpnnResidueIdentity {
    mutation_aa: string;
    score_class: 'high' | 'neutral' | 'minimal' | null;
    status: string;
    reason: string | null;
    metrics: {
        score: number | null;
        scoreable: boolean;
    };
}

export type FrustraMpnnMultidimensionalPoint = FrustraMpnnResultPoint | FrustraMpnnResiduePoint | FrustraMpnnMutationPoint;

interface FrustraMpnnPageBase {
    schema_version: 'frustrampnn_multidimensional_v1';
    dimensions: FrustraMpnnDimension[];
    total: number;
    limit: number;
    offset: number;
    next_offset: number | null;
}

export interface FrustraMpnnResultPage extends FrustraMpnnPageBase {
    level: 'result';
    items: FrustraMpnnResultPoint[];
}

export interface FrustraMpnnResiduePage extends FrustraMpnnPageBase {
    level: 'residue';
    items: FrustraMpnnResiduePoint[];
}

export interface FrustraMpnnMutationPage extends FrustraMpnnPageBase {
    level: 'mutation';
    items: FrustraMpnnMutationPoint[];
}

export type FrustraMpnnMultidimensionalPage = FrustraMpnnResultPage | FrustraMpnnResiduePage | FrustraMpnnMutationPage;

const isRecord = (value: unknown): value is Record<string, unknown> => value != null && typeof value === 'object' && !Array.isArray(value);

const exactKeys = (value: Record<string, unknown>, expected: readonly string[], label: string): void => {
    const actual = Object.keys(value).sort();
    const sortedExpected = [...expected].sort();
    if (actual.length !== sortedExpected.length || actual.some((key, index) => key !== sortedExpected[index])) {
        throw new Error(`${label} has unknown or missing keys`);
    }
};

const stringValue = (value: unknown, label: string, allowBlank = false): string => {
    if (typeof value !== 'string' || (!allowBlank && value.length === 0)) throw new Error(`${label} must be a string`);
    return value;
};

const nullableString = (value: unknown, label: string): string | null => value === null ? null : stringValue(value, label);
const integerValue = (value: unknown, label: string, minimum?: number): number => {
    if (!Number.isInteger(value) || (minimum !== undefined && Number(value) < minimum)) throw new Error(`${label} must be an integer`);
    return Number(value);
};
const finiteValue = (value: unknown, label: string): number => {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label} must be finite`);
    return value;
};
const nullableFinite = (value: unknown, label: string): number | null => value === null ? null : finiteValue(value, label);
const nullableFraction = (value: unknown, label: string): number | null => {
    const parsed = nullableFinite(value, label);
    if (parsed !== null && (parsed < 0 || parsed > 1)) throw new Error(`${label} must be a fraction`);
    return parsed;
};
const sha256 = (value: unknown, label: string): string => {
    const parsed = stringValue(value, label);
    if (!/^[0-9a-f]{64}$/.test(parsed)) throw new Error(`${label} must be a lowercase SHA-256`);
    return parsed;
};
const nullableSha256 = (value: unknown, label: string): string | null => value === null ? null : sha256(value, label);
const aminoAcid = (value: unknown, label: string): string => {
    const parsed = stringValue(value, label);
    if (!/^[ACDEFGHIKLMNPQRSTVWY]$/.test(parsed)) throw new Error(`${label} is invalid`);
    return parsed;
};

const identityDimensions = [
    ['dataset_id', 'identifier'], ['workflow_family', 'category'], ['job_id', 'identifier'],
    ['design_id', 'identifier'], ['invocation_id', 'identifier'], ['configuration_id', 'identifier'],
    ['configuration_sha256', 'identifier'], ['threshold_policy_id', 'identifier'],
] as const;
const residueIdentityDimensions = [
    ['target_id', 'identifier'], ['entity_instance_id', 'identifier'], ['auth_asym_id', 'identifier'],
    ['auth_seq_id', 'identifier'], ['insertion_code', 'identifier'], ['sequence_index', 'identifier'], ['wt', 'identifier'],
] as const;
const dimensionsByLevel = {
    result: [...identityDimensions,
        ['mean_score', 'number'], ['native_score', 'number'], ['high_fraction', 'fraction'],
        ['minimal_fraction', 'fraction'], ['scoreable_fraction', 'fraction'], ['slot_count', 'count'], ['residue_count', 'count']],
    residue: [...identityDimensions, ...residueIdentityDimensions,
        ['native_score', 'number'], ['alternative_mean_score', 'number'], ['best_alternative_delta', 'number'],
        ['worst_alternative_delta', 'number'], ['high_alternative_fraction', 'fraction'],
        ['minimal_alternative_fraction', 'fraction'], ['alternative_count', 'count']],
    mutation: [...identityDimensions, ...residueIdentityDimensions,
        ['mutation_aa', 'identifier'], ['score_class', 'identifier'], ['status', 'identifier'],
        ['score', 'number'], ['scoreable', 'boolean']],
} as const;

const parseDimensions = (value: unknown, level: keyof typeof dimensionsByLevel): FrustraMpnnDimension[] => {
    if (!Array.isArray(value)) throw new Error('FrustraMPNN multidimensional dimensions must be an array');
    const expected = dimensionsByLevel[level];
    if (value.length !== expected.length) throw new Error('FrustraMPNN multidimensional dimension contract is invalid');
    return value.map((item, index) => {
        if (!isRecord(item)) throw new Error(`FrustraMPNN multidimensional dimensions[${index}] must be an object`);
        exactKeys(item, ['id', 'kind', 'description', 'unit', 'formula'], `FrustraMPNN multidimensional dimensions[${index}]`);
        const [expectedId, expectedKind] = expected[index]!;
        if (item.id !== expectedId || item.kind !== expectedKind) throw new Error('FrustraMPNN multidimensional dimension contract is invalid');
        return {
            id: expectedId,
            kind: expectedKind,
            description: item.description === null ? null : stringValue(item.description, `dimensions[${index}].description`, true),
            unit: item.unit === null ? null : stringValue(item.unit, `dimensions[${index}].unit`, true),
            formula: item.formula === null ? null : stringValue(item.formula, `dimensions[${index}].formula`, true),
        };
    });
};

const pointBaseKeys = [
    'point_id', 'dataset_id', 'workflow_family', 'job_id', 'design_id', 'candidate_id', 'invocation_id',
    'source_artifact_sha256', 'checkpoint_sha256', 'configuration_id', 'configuration_sha256',
    'threshold_policy_id', 'metrics',
] as const;
const residueKeys = ['target_id', 'entity_instance_id', 'auth_asym_id', 'auth_seq_id', 'insertion_code', 'sequence_index', 'wt'] as const;

const parsePointBase = (value: Record<string, unknown>, label: string): FrustraMpnnPointBase => ({
    point_id: stringValue(value.point_id, `${label}.point_id`),
    dataset_id: stringValue(value.dataset_id, `${label}.dataset_id`),
    workflow_family: stringValue(value.workflow_family, `${label}.workflow_family`),
    job_id: stringValue(value.job_id, `${label}.job_id`),
    design_id: nullableString(value.design_id, `${label}.design_id`),
    candidate_id: stringValue(value.candidate_id, `${label}.candidate_id`),
    invocation_id: stringValue(value.invocation_id, `${label}.invocation_id`),
    source_artifact_sha256: sha256(value.source_artifact_sha256, `${label}.source_artifact_sha256`),
    checkpoint_sha256: nullableSha256(value.checkpoint_sha256, `${label}.checkpoint_sha256`),
    configuration_id: nullableString(value.configuration_id, `${label}.configuration_id`),
    configuration_sha256: nullableSha256(value.configuration_sha256, `${label}.configuration_sha256`),
    threshold_policy_id: nullableString(value.threshold_policy_id, `${label}.threshold_policy_id`),
});

const parseResidueIdentity = (value: Record<string, unknown>, label: string): FrustraMpnnResidueIdentity => ({
    target_id: stringValue(value.target_id, `${label}.target_id`, true),
    entity_instance_id: stringValue(value.entity_instance_id, `${label}.entity_instance_id`, true),
    auth_asym_id: stringValue(value.auth_asym_id, `${label}.auth_asym_id`, true),
    auth_seq_id: stringValue(value.auth_seq_id, `${label}.auth_seq_id`, true),
    insertion_code: stringValue(value.insertion_code, `${label}.insertion_code`, true),
    sequence_index: integerValue(value.sequence_index, `${label}.sequence_index`),
    wt: aminoAcid(value.wt, `${label}.wt`),
});

const parseResultPoint = (value: unknown, index: number): FrustraMpnnResultPoint => {
    const label = `FrustraMPNN multidimensional items[${index}]`;
    if (!isRecord(value)) throw new Error(`${label} must be an object`);
    exactKeys(value, pointBaseKeys, label);
    if (!isRecord(value.metrics)) throw new Error(`${label}.metrics must be an object`);
    exactKeys(value.metrics, ['mean_score', 'native_score', 'high_fraction', 'minimal_fraction', 'scoreable_fraction', 'slot_count', 'residue_count'], `${label}.metrics`);
    return {
        ...parsePointBase(value, label),
        metrics: {
            mean_score: nullableFinite(value.metrics.mean_score, `${label}.metrics.mean_score`),
            native_score: nullableFinite(value.metrics.native_score, `${label}.metrics.native_score`),
            high_fraction: nullableFraction(value.metrics.high_fraction, `${label}.metrics.high_fraction`),
            minimal_fraction: nullableFraction(value.metrics.minimal_fraction, `${label}.metrics.minimal_fraction`),
            scoreable_fraction: nullableFraction(value.metrics.scoreable_fraction, `${label}.metrics.scoreable_fraction`),
            slot_count: integerValue(value.metrics.slot_count, `${label}.metrics.slot_count`, 0),
            residue_count: integerValue(value.metrics.residue_count, `${label}.metrics.residue_count`, 0),
        },
    };
};

const parseResiduePoint = (value: unknown, index: number): FrustraMpnnResiduePoint => {
    const label = `FrustraMPNN multidimensional items[${index}]`;
    if (!isRecord(value)) throw new Error(`${label} must be an object`);
    exactKeys(value, [...pointBaseKeys, ...residueKeys], label);
    if (!isRecord(value.metrics)) throw new Error(`${label}.metrics must be an object`);
    exactKeys(value.metrics, ['native_score', 'alternative_mean_score', 'best_alternative_delta', 'worst_alternative_delta', 'high_alternative_fraction', 'minimal_alternative_fraction', 'alternative_count'], `${label}.metrics`);
    return {
        ...parsePointBase(value, label),
        ...parseResidueIdentity(value, label),
        metrics: {
            native_score: nullableFinite(value.metrics.native_score, `${label}.metrics.native_score`),
            alternative_mean_score: nullableFinite(value.metrics.alternative_mean_score, `${label}.metrics.alternative_mean_score`),
            best_alternative_delta: nullableFinite(value.metrics.best_alternative_delta, `${label}.metrics.best_alternative_delta`),
            worst_alternative_delta: nullableFinite(value.metrics.worst_alternative_delta, `${label}.metrics.worst_alternative_delta`),
            high_alternative_fraction: nullableFraction(value.metrics.high_alternative_fraction, `${label}.metrics.high_alternative_fraction`),
            minimal_alternative_fraction: nullableFraction(value.metrics.minimal_alternative_fraction, `${label}.metrics.minimal_alternative_fraction`),
            alternative_count: integerValue(value.metrics.alternative_count, `${label}.metrics.alternative_count`, 0),
        },
    };
};

const parseMutationPoint = (value: unknown, index: number): FrustraMpnnMutationPoint => {
    const label = `FrustraMPNN multidimensional items[${index}]`;
    if (!isRecord(value)) throw new Error(`${label} must be an object`);
    exactKeys(value, [...pointBaseKeys, ...residueKeys, 'mutation_aa', 'score_class', 'status', 'reason'], label);
    if (!isRecord(value.metrics)) throw new Error(`${label}.metrics must be an object`);
    exactKeys(value.metrics, ['score', 'scoreable'], `${label}.metrics`);
    if (value.score_class !== null && value.score_class !== 'high' && value.score_class !== 'neutral' && value.score_class !== 'minimal') throw new Error(`${label}.score_class is invalid`);
    if (typeof value.metrics.scoreable !== 'boolean') throw new Error(`${label}.metrics.scoreable must be boolean`);
    return {
        ...parsePointBase(value, label),
        ...parseResidueIdentity(value, label),
        mutation_aa: aminoAcid(value.mutation_aa, `${label}.mutation_aa`),
        score_class: value.score_class as FrustraMpnnMutationPoint['score_class'],
        status: stringValue(value.status, `${label}.status`, true),
        reason: value.reason === null ? null : stringValue(value.reason, `${label}.reason`, true),
        metrics: {
            score: nullableFinite(value.metrics.score, `${label}.metrics.score`),
            scoreable: value.metrics.scoreable,
        },
    };
};

export function parseFrustraMpnnMultidimensionalPage(value: unknown): FrustraMpnnMultidimensionalPage {
    if (!isRecord(value)) throw new Error('FrustraMPNN multidimensional envelope must be an object');
    exactKeys(value, ['schema_version', 'level', 'dimensions', 'total', 'limit', 'offset', 'next_offset', 'items'], 'FrustraMPNN multidimensional envelope');
    if (value.schema_version !== 'frustrampnn_multidimensional_v1' || (value.level !== 'result' && value.level !== 'residue' && value.level !== 'mutation')) {
        throw new Error('FrustraMPNN multidimensional envelope identity is invalid');
    }
    if (!Array.isArray(value.items)) throw new Error('FrustraMPNN multidimensional items must be an array');
    const total = integerValue(value.total, 'FrustraMPNN multidimensional total', 0);
    const limit = integerValue(value.limit, 'FrustraMPNN multidimensional limit', 1);
    const offset = integerValue(value.offset, 'FrustraMPNN multidimensional offset', 0);
    const nextOffset = value.next_offset === null ? null : integerValue(value.next_offset, 'FrustraMPNN multidimensional next_offset', 0);
    if (limit > 5000 || value.items.length > limit || offset + value.items.length > total) throw new Error('FrustraMPNN multidimensional pagination is invalid');
    const expectedNextOffset = offset + value.items.length < total ? offset + value.items.length : null;
    if (nextOffset !== expectedNextOffset) throw new Error('FrustraMPNN multidimensional pagination is inconsistent');
    const common = {
        schema_version: 'frustrampnn_multidimensional_v1' as const,
        dimensions: parseDimensions(value.dimensions, value.level), total, limit, offset, next_offset: nextOffset,
    };
    if (value.level === 'result') return { ...common, level: 'result', items: value.items.map(parseResultPoint) };
    if (value.level === 'residue') return { ...common, level: 'residue', items: value.items.map(parseResiduePoint) };
    return { ...common, level: 'mutation', items: value.items.map(parseMutationPoint) };
}

export interface FrustraMpnn3dModel {
    x: number[];
    y: number[];
    z: number[];
    color: number[];
    pointIds: string[];
    hover: string[];
}

export function buildFrustraMpnn3dModel(
    page: FrustraMpnnMultidimensionalPage,
    xMetric: string,
    yMetric: string,
    zMetric: string,
    colorMetric: string,
): FrustraMpnn3dModel {
    const model: FrustraMpnn3dModel = { x: [], y: [], z: [], color: [], pointIds: [], hover: [] };
    for (const point of page.items) {
        const metrics = point.metrics as Record<string, number | boolean | null>;
        const values = [metrics[xMetric], metrics[yMetric], metrics[zMetric], metrics[colorMetric]];
        if (!values.every((metric) => typeof metric === 'number' && Number.isFinite(metric))) continue;
        model.x.push(values[0] as number);
        model.y.push(values[1] as number);
        model.z.push(values[2] as number);
        model.color.push(values[3] as number);
        model.pointIds.push(point.point_id);
        model.hover.push(`Dataset ${point.dataset_id}\nJob ${point.job_id}\nDesign ${point.design_id ?? 'unlinked'}\nCandidate ${point.candidate_id}\nInvocation ${point.invocation_id}`);
    }
    return model;
}
