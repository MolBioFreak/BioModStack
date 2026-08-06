export interface FrustraMpnnDimension {
    id: string;
    kind: 'number' | 'fraction' | 'count' | 'boolean' | 'identifier' | 'category';
    unit?: string;
    formula?: string;
    description?: string;
}

export interface FrustraMpnnMultidimensionalPoint {
    point_id: string;
    dataset_id: string;
    workflow_family: string;
    job_id: string;
    design_id: string | null;
    candidate_id: string;
    invocation_id: string;
    source_artifact_sha256: string;
    checkpoint_sha256: string | null;
    threshold_policy_id: string | null;
    metrics: Record<string, number | boolean | null>;
    [key: string]: unknown;
}

export interface FrustraMpnnMultidimensionalPage {
    schema_version: 'frustrampnn_multidimensional_v1';
    level: 'result' | 'residue' | 'mutation';
    dimensions: FrustraMpnnDimension[];
    total: number;
    limit: number;
    offset: number;
    next_offset: number | null;
    items: FrustraMpnnMultidimensionalPoint[];
}

const isRecord = (value: unknown): value is Record<string, unknown> => value != null && typeof value === 'object' && !Array.isArray(value);
const requiredIdentity = ['point_id', 'dataset_id', 'workflow_family', 'job_id', 'candidate_id', 'invocation_id', 'source_artifact_sha256'] as const;

export function parseFrustraMpnnMultidimensionalPage(value: unknown): FrustraMpnnMultidimensionalPage {
    if (!isRecord(value) || value.schema_version !== 'frustrampnn_multidimensional_v1'
        || !['result', 'residue', 'mutation'].includes(String(value.level))
        || !Array.isArray(value.dimensions) || !Array.isArray(value.items)
        || !Number.isInteger(value.total) || Number(value.total) < 0
        || !Number.isInteger(value.limit) || Number(value.limit) < 1 || Number(value.limit) > 5000
        || !Number.isInteger(value.offset) || Number(value.offset) < 0) {
        throw new Error('FrustraMPNN multidimensional envelope is invalid');
    }
    const dimensionIds = new Set<string>();
    for (const rawDimension of value.dimensions) {
        if (!isRecord(rawDimension) || typeof rawDimension.id !== 'string' || !rawDimension.id
            || typeof rawDimension.kind !== 'string' || dimensionIds.has(rawDimension.id)) {
            throw new Error('FrustraMPNN multidimensional dimension contract is invalid');
        }
        dimensionIds.add(rawDimension.id);
    }
    for (const rawItem of value.items) {
        if (!isRecord(rawItem) || requiredIdentity.some((key) => typeof rawItem[key] !== 'string' || !(rawItem[key] as string))) {
            throw new Error('FrustraMPNN multidimensional point identity is invalid');
        }
        if (rawItem.design_id != null && (typeof rawItem.design_id !== 'string' || !rawItem.design_id)) {
            throw new Error('FrustraMPNN multidimensional point identity is invalid');
        }
        if (!isRecord(rawItem.metrics)) throw new Error('FrustraMPNN multidimensional metrics are invalid');
        for (const metricValue of Object.values(rawItem.metrics)) {
            if (typeof metricValue === 'number' && !Number.isFinite(metricValue)) {
                throw new Error('FrustraMPNN multidimensional metrics must be finite');
            }
            if (metricValue != null && typeof metricValue !== 'number' && typeof metricValue !== 'boolean') {
                throw new Error('FrustraMPNN multidimensional metric type is invalid');
            }
        }
    }
    return value as unknown as FrustraMpnnMultidimensionalPage;
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
        const values = [point.metrics[xMetric], point.metrics[yMetric], point.metrics[zMetric], point.metrics[colorMetric]];
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
