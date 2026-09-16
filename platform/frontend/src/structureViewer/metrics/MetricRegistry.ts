import {
    assessResidueRef,
    canonicalResidueRefKey,
    canonicalSpatialRefKey,
    type AtomRef,
    type ResidueRef,
} from '../contracts/structureIdentity.js';
import { viewerOk, viewerUnsupported, type ViewerResult } from '../contracts/viewerResults.js';
import type { ChainPairIdentity, GeometryAnnotationIdentity, MetricDescriptor, MetricLayer, MetricValue, ResiduePairIdentity } from './metricContracts.js';

const nonEmpty = (value: string): boolean => Boolean(value.trim());
const SHA256 = /^[0-9a-f]{64}$/i;

export const validateMetricDescriptor = (descriptor: MetricDescriptor): ViewerResult<MetricDescriptor> => {
    if (!nonEmpty(descriptor.id) || !nonEmpty(descriptor.label) || !nonEmpty(descriptor.provenance.source)) {
        return viewerUnsupported('Metric descriptors require id, label, and provenance source', 'metric-registry');
    }
    if (descriptor.valueRange && (!Number.isFinite(descriptor.valueRange[0])
        || !Number.isFinite(descriptor.valueRange[1])
        || descriptor.valueRange[0] >= descriptor.valueRange[1])) {
        return viewerUnsupported('Metric valueRange must be finite and increasing', 'metric-registry');
    }
    if (descriptor.projectionPolicy === 'direct' && !['residue-scalar', 'atom-scalar'].includes(descriptor.dimension)) {
        return viewerUnsupported('Direct projection is limited to residue- and atom-scalar metrics', 'metric-projection');
    }
    if (descriptor.palette && (descriptor.palette.colors.length === 0
        || descriptor.palette.colors.some((color) => !/^#[0-9a-f]{6}$/i.test(color)))) {
        return viewerUnsupported('Metric palettes require at least one #RRGGBB color', 'metric-palette');
    }
    const hash = descriptor.provenance.artifactSha256;
    if (hash !== undefined && !SHA256.test(hash)) {
        return viewerUnsupported('Metric artifactSha256 must be a hexadecimal SHA-256', 'metric-provenance');
    }
    return viewerOk(descriptor);
};

const validateValue = (point: MetricValue<unknown>): ViewerResult<void> => {
    if ((point.value === null || (typeof point.value === 'number' && !Number.isFinite(point.value))) && point.missingness === undefined) {
        return viewerUnsupported('Null and non-finite metric values require an explicit missingness reason', 'metric-missingness');
    }
    return viewerOk(undefined);
};

const validateResidue = (residue: ResidueRef): ViewerResult<void> => {
    const assessed = assessResidueRef(residue);
    return assessed.status === 'ok' ? viewerOk(undefined) : assessed;
};

const validateResidueValues = (values: readonly MetricValue<ResidueRef>[]): ViewerResult<void> => {
    const seen = new Set<string>();
    for (const point of values) {
        const value = validateValue(point);
        if (value.status !== 'ok') return value;
        const identity = validateResidue(point.identity);
        if (identity.status !== 'ok') return identity;
        const key = canonicalResidueRefKey(point.identity);
        if (seen.has(key)) return viewerUnsupported(`Duplicate residue metric identity: ${key}`, 'metric-identity');
        seen.add(key);
    }
    return viewerOk(undefined);
};

const validateAtomValues = (values: readonly MetricValue<AtomRef>[]): ViewerResult<void> => {
    const seen = new Set<string>();
    for (const point of values) {
        const value = validateValue(point);
        if (value.status !== 'ok') return value;
        const identity = validateResidue(point.identity);
        if (identity.status !== 'ok') return identity;
        const atom = point.identity.labelAtomId ?? point.identity.authAtomId;
        if (!atom?.trim()) return viewerUnsupported('Atom metric identity requires labelAtomId or authAtomId', 'metric-identity');
        const key = canonicalSpatialRefKey(point.identity);
        if (seen.has(key)) return viewerUnsupported(`Duplicate atom metric identity: ${key}`, 'metric-identity');
        seen.add(key);
    }
    return viewerOk(undefined);
};

const validatePairValues = (values: readonly MetricValue<ResiduePairIdentity>[]): ViewerResult<void> => {
    const seen = new Set<string>();
    for (const point of values) {
        const value = validateValue(point);
        if (value.status !== 'ok') return value;
        const first = validateResidue(point.identity.first);
        if (first.status !== 'ok') return first;
        const second = validateResidue(point.identity.second);
        if (second.status !== 'ok') return second;
        const key = `${canonicalSpatialRefKey(point.identity.first)}::${canonicalSpatialRefKey(point.identity.second)}`;
        if (seen.has(key)) return viewerUnsupported(`Duplicate residue-pair metric identity: ${key}`, 'metric-identity');
        seen.add(key);
    }
    return viewerOk(undefined);
};

const validateChainPairValues = (values: readonly MetricValue<ChainPairIdentity>[]): ViewerResult<void> => {
    const seen = new Set<string>();
    for (const point of values) {
        const value = validateValue(point);
        if (value.status !== 'ok') return value;
        const identity = point.identity;
        if (!nonEmpty(identity.documentId) || !nonEmpty(identity.firstChainId) || !nonEmpty(identity.secondChainId)) {
            return viewerUnsupported('Chain-pair metrics require documentId and two chain IDs', 'metric-identity');
        }
        if (identity.firstChainId === identity.secondChainId
            && (!identity.firstInstanceId || !identity.secondInstanceId || identity.firstInstanceId === identity.secondInstanceId)) {
            return viewerUnsupported('Same-chain interface metrics require two distinct repeated-instance IDs', 'metric-identity');
        }
        const key = [identity.documentId, identity.firstChainId, identity.firstInstanceId ?? '', identity.secondChainId, identity.secondInstanceId ?? ''].join('|');
        if (seen.has(key)) return viewerUnsupported(`Duplicate chain-pair metric identity: ${key}`, 'metric-identity');
        seen.add(key);
    }
    return viewerOk(undefined);
};

const validateGeometryValues = (values: readonly MetricValue<GeometryAnnotationIdentity>[]): ViewerResult<void> => {
    const seen = new Set<string>();
    for (const point of values) {
        const value = validateValue(point);
        if (value.status !== 'ok') return value;
        const identity = point.identity;
        if (!nonEmpty(identity.annotationId) || !nonEmpty(identity.documentId)) {
            return viewerUnsupported('Geometry annotations require annotationId and documentId', 'metric-identity');
        }
        if (!identity.residues?.length && !identity.atoms?.length) {
            return viewerUnsupported('Geometry annotations require exact residue or atom endpoints', 'metric-identity');
        }
        for (const residue of [...(identity.residues ?? []), ...(identity.atoms ?? [])]) {
            const assessed = validateResidue(residue);
            if (assessed.status !== 'ok') return assessed;
            if (residue.documentId !== identity.documentId) {
                return viewerUnsupported('Geometry endpoint document identity must match its annotation', 'metric-identity');
            }
        }
        const key = `${identity.documentId}|${identity.annotationId}`;
        if (seen.has(key)) return viewerUnsupported(`Duplicate geometry annotation identity: ${key}`, 'metric-identity');
        seen.add(key);
    }
    return viewerOk(undefined);
};

export const validateMetricLayer = (layer: MetricLayer): ViewerResult<MetricLayer> => {
    const descriptor = validateMetricDescriptor(layer.descriptor);
    if (descriptor.status !== 'ok') return descriptor;
    const limits: Record<MetricDescriptor['dimension'], number> = {
        'residue-scalar': 200_000, 'atom-scalar': 1_000_000, 'structure-scalar': 10_000,
        'chain-pair-scalar': 100_000, 'residue-pair-matrix': 262_144,
        'geometry-annotation': 50_000, 'volume-descriptor': 10_000,
    };
    if (layer.values.length > limits[layer.descriptor.dimension]) {
        return viewerUnsupported(`Metric layer exceeds the ${limits[layer.descriptor.dimension].toLocaleString()}-value ${layer.descriptor.dimension} admission limit`, 'metric-admission');
    }
    if ('dataset' in layer && layer.dataset?.matrix) {
        const d = layer.dataset, matrix = d.matrix!;
        const rows = d.rowAxis ?? [], columns = d.columnAxis ?? [];
        if (d.matrixDirection !== 'directed' || layer.values.length || !rows.length || !columns.length
            || rows.length > 1024 || columns.length > 1024 || d.shape?.[0] !== rows.length || d.shape?.[1] !== columns.length
            || d.documentIds.length !== 1 || d.descriptorId !== layer.descriptor.id
            || matrix.length !== rows.length || matrix.some(row => row.length !== columns.length || row.some(v => typeof v !== 'number' || !Number.isFinite(v) || v < 0))) {
            return viewerUnsupported('Invalid dense directed matrix dimensions or values', 'metric-identity');
        }
        for (const axis of [rows, columns]) {
            if (axis.some(ref => ref.documentId !== d.documentIds[0] || assessResidueRef(ref).status !== 'ok')
                || new Set(axis.map(canonicalSpatialRefKey)).size !== axis.length) return viewerUnsupported('Invalid dense matrix axis identity', 'metric-identity');
        }
    }
    const estimatedBytes = JSON.stringify('dataset' in layer && layer.dataset?.matrix ? layer.dataset : layer.values).length * 2;
    if (estimatedBytes > 64 * 1024 * 1024) {
        return viewerUnsupported('Metric layer exceeds the 64 MiB serialized admission budget', 'metric-admission');
    }
    let values: ViewerResult<void> = viewerOk(undefined);
    if (layer.descriptor.dimension === 'residue-scalar') {
        values = validateResidueValues(layer.values as readonly MetricValue<ResidueRef>[]);
    } else if (layer.descriptor.dimension === 'atom-scalar') {
        values = validateAtomValues(layer.values as readonly MetricValue<AtomRef>[]);
    } else if (layer.descriptor.dimension === 'residue-pair-matrix') {
        values = validatePairValues(layer.values as readonly MetricValue<ResiduePairIdentity>[]);
    } else if (layer.descriptor.dimension === 'chain-pair-scalar') {
        values = validateChainPairValues(layer.values as readonly MetricValue<ChainPairIdentity>[]);
    } else if (layer.descriptor.dimension === 'geometry-annotation') {
        values = validateGeometryValues(layer.values as readonly MetricValue<GeometryAnnotationIdentity>[]);
    } else {
        for (const point of layer.values) {
            values = validateValue(point as MetricValue<unknown>);
            if (values.status !== 'ok') break;
        }
    }
    return values.status === 'ok' ? viewerOk(layer) : values;
};

export class MetricRegistry {
    private readonly layers = new Map<string, MetricLayer>();

    register(layer: MetricLayer): ViewerResult<MetricLayer> {
        const valid = validateMetricLayer(layer);
        if (valid.status !== 'ok') return valid;
        const existing = this.layers.get(layer.descriptor.id);
        if (existing && existing.descriptor.dimension !== layer.descriptor.dimension) {
            return viewerUnsupported(`Metric ${layer.descriptor.id} cannot change dimension`, 'metric-registry');
        }
        this.layers.set(layer.descriptor.id, layer);
        return viewerOk(layer);
    }

    unregister(metricId: string): boolean { return this.layers.delete(metricId); }
    get(metricId: string): MetricLayer | undefined { return this.layers.get(metricId); }
    list(): readonly MetricLayer[] { return [...this.layers.values()].sort((a, b) => a.descriptor.label.localeCompare(b.descriptor.label)); }
    clear(): void { this.layers.clear(); }
}
