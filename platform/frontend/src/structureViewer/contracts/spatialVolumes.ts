import { viewerOk, viewerUnsupported, type ViewerResult } from './viewerResults.js';
import type { ViewerSnapshotBindingV2 } from './m6Reproducibility.js';

const SHA256 = /^[0-9a-f]{64}$/;
const MATRIX_LENGTH = 16;
export const VOLUME_HARD_LIMITS = Object.freeze({
    maxAxis: 4096,
    maxVoxels: 536_870_912,
    maxEncodedBytes: 2 * 1024 * 1024 * 1024,
    maxResidentDescriptors: 32,
    maxVisibleVolumes: 4,
});

export type SpatialVolumeSemanticKind = 'density' | 'electrostatic_potential' | 'segmentation' | 'other_scalar';
export type SpatialVolumeUnits = 'e/Å³' | 'V' | 'kT/e' | 'dimensionless' | 'arbitrary';

export interface SpatialVolumeDescriptorV1 {
    readonly schemaVersion: 1;
    readonly volumeId: string;
    readonly semanticKind: SpatialVolumeSemanticKind;
    readonly artifactId: string;
    readonly artifactSha256: string;
    readonly format: 'ccp4';
    readonly byteLength: number;
    readonly dimensions: readonly [number, number, number];
    readonly axisOrder: readonly [number, number, number];
    readonly gridToWorldRowMajor4x4: readonly number[];
    readonly coordinateUnits: 'Å';
    readonly valueUnits?: SpatialVolumeUnits;
    readonly statistics?: { readonly min: number; readonly max: number; readonly mean?: number; readonly sigma?: number };
    readonly channelCount: number;
    readonly recommendedDisplay?: {
        readonly channel?: number;
        readonly contourAbsolute?: number;
        readonly contourSigma?: number;
        readonly opacity?: number;
    };
    readonly registrationRef?: string;
    readonly provenanceRef: string;
}

export interface VolumePresentationStateV1 {
    readonly schema: 'bms.viewer.volume-presentation.v1';
    readonly volumeId: string;
    readonly channel: number;
    readonly visible: boolean;
    readonly opacity: number;
    readonly contour: { readonly mode: 'absolute' | 'sigma'; readonly value: number };
    readonly color: number;
    readonly representation: 'isosurface' | 'slice';
    readonly slice: { readonly axis: 0 | 1 | 2; readonly index: number } | null;
    readonly crop: {
        readonly namespace: 'grid' | 'world';
        readonly min: readonly [number, number, number];
        readonly max: readonly [number, number, number];
    } | null;
    readonly visibleSegmentIds: readonly number[];
    readonly registrationRef: string | null;
}

/** Supplied registration analysis payload; the browser applies but never produces this transform. */
export interface VolumeRegistrationV1 {
    readonly schema: 'bms.viewer.volume-registration.v1';
    readonly registrationId: string;
    readonly artifactSha256: string;
    readonly structureDocumentId: string;
    readonly structureSha256: string;
    readonly volumeId: string;
    readonly volumeSha256: string;
    readonly transformRowMajor4x4: readonly number[];
    readonly method: 'supplied_transform_v1';
    readonly provenanceRef: string;
}

export interface VolumeSegmentationLabelV1 {
    readonly segmentId: number;
    readonly parentSegmentId: number | null;
    readonly label: string | null;
    readonly recommendedColor: number | null;
}

export interface VolumeSegmentationV1 {
    readonly schema: 'bms.viewer.volume-segmentation.v1';
    readonly segmentationId: string;
    readonly volumeId: string;
    readonly artifactId: string;
    readonly artifactSha256: string;
    readonly labels: readonly VolumeSegmentationLabelV1[];
    readonly provenanceRef: string;
}

const validMatrix = (matrix: readonly number[]): boolean => matrix.length === MATRIX_LENGTH && matrix.every(Number.isFinite);
const unique = <T>(values: readonly T[]): boolean => new Set(values).size === values.length;
const validColor = (color: number | null): boolean => color === null || Number.isInteger(color) && color >= 0 && color <= 0xFFFFFF;

export const validateSpatialVolumeDescriptor = (descriptor: SpatialVolumeDescriptorV1): ViewerResult<SpatialVolumeDescriptorV1> => {
    if (descriptor.schemaVersion !== 1 || descriptor.format !== 'ccp4' || descriptor.semanticKind === 'other_scalar') {
        return viewerUnsupported('Only governed scalar-volume v1 CCP4 density, electrostatic, and supplied segmentation descriptors are supported', 'volume-ccp4-v1');
    }
    if (!descriptor.volumeId.trim() || !descriptor.artifactId.trim() || !SHA256.test(descriptor.artifactSha256) || !descriptor.provenanceRef.trim()) {
        return viewerUnsupported('Volume identity, provenance, and lowercase SHA-256 are required', 'volume-ccp4-v1');
    }
    if (!unique(descriptor.axisOrder) || [...descriptor.axisOrder].sort().join(',') !== '0,1,2') {
        return viewerUnsupported('Volume axisOrder must be a permutation of 0, 1, 2', 'volume-ccp4-v1');
    }
    if (descriptor.dimensions.some((value) => !Number.isInteger(value) || value < 1 || value > VOLUME_HARD_LIMITS.maxAxis)) {
        return viewerUnsupported('Volume dimensions exceed the v1 per-axis admission limit', 'volume-ccp4-v1');
    }
    const voxels = descriptor.dimensions.reduce((product, value) => product * value, 1);
    if (voxels > VOLUME_HARD_LIMITS.maxVoxels || !Number.isInteger(descriptor.byteLength) || descriptor.byteLength < 1 || descriptor.byteLength > VOLUME_HARD_LIMITS.maxEncodedBytes) {
        return viewerUnsupported('Volume exceeds the v1 voxel or encoded-byte admission limit', 'volume-ccp4-v1');
    }
    if (!validMatrix(descriptor.gridToWorldRowMajor4x4) || descriptor.coordinateUnits !== 'Å'
        || !Number.isInteger(descriptor.channelCount) || descriptor.channelCount < 1) {
        return viewerUnsupported('Volume transform, coordinate units, or channel count are invalid', 'volume-ccp4-v1');
    }
    if ((descriptor.semanticKind === 'density' || descriptor.semanticKind === 'electrostatic_potential') && descriptor.valueUnits === undefined) {
        return viewerUnsupported('Density and electrostatic volumes require explicit value units', 'volume-ccp4-v1');
    }
    if (descriptor.valueUnits && !['e/Å³', 'V', 'kT/e', 'dimensionless', 'arbitrary'].includes(descriptor.valueUnits)) {
        return viewerUnsupported('Volume value units are unsupported', 'volume-ccp4-v1');
    }
    if (descriptor.statistics) {
        const { min, max, mean, sigma } = descriptor.statistics;
        if (![min, max, ...(mean === undefined ? [] : [mean]), ...(sigma === undefined ? [] : [sigma])].every(Number.isFinite)
            || min > max || sigma !== undefined && sigma <= 0) {
            return viewerUnsupported('Volume statistics are invalid', 'volume-ccp4-v1');
        }
    }
    const channel = descriptor.recommendedDisplay?.channel;
    if (channel !== undefined && (!Number.isInteger(channel) || channel < 0 || channel >= descriptor.channelCount)) {
        return viewerUnsupported('Recommended volume channel is out of bounds', 'volume-ccp4-v1');
    }
    return viewerOk(descriptor);
};

export const validateVolumePresentationState = (state: VolumePresentationStateV1, descriptor: SpatialVolumeDescriptorV1): ViewerResult<VolumePresentationStateV1> => {
    const valid = validateSpatialVolumeDescriptor(descriptor);
    if (valid.status !== 'ok') return valid as ViewerResult<never>;
    if (state.schema !== 'bms.viewer.volume-presentation.v1' || state.volumeId !== descriptor.volumeId
        || !Number.isInteger(state.channel) || state.channel < 0 || state.channel >= descriptor.channelCount) {
        return viewerUnsupported('Volume presentation identity or channel is invalid', 'volume-ccp4-v1');
    }
    if (!Number.isFinite(state.opacity) || state.opacity < 0 || state.opacity > 1
        || !Number.isFinite(state.contour.value) || !validColor(state.color)) {
        return viewerUnsupported('Volume opacity, contour, or packed RGB color is invalid', 'volume-ccp4-v1');
    }
    if (state.contour.mode === 'sigma' && (descriptor.statistics?.mean === undefined || descriptor.statistics.sigma === undefined)) {
        return viewerUnsupported('Sigma contour requires authoritative mean and sigma', 'volume-ccp4-v1');
    }
    if (state.representation === 'slice') {
        if (!state.slice || !Number.isInteger(state.slice.index) || state.slice.index < 0 || state.slice.index >= descriptor.dimensions[state.slice.axis]!) {
            return viewerUnsupported('Slice representation requires an in-bounds integer axis index', 'volume-slice-v1');
        }
    } else if (state.slice !== null) {
        return viewerUnsupported('Isosurface presentation must not carry slice state', 'volume-ccp4-v1');
    }
    if (state.crop && (!state.crop.min.every(Number.isFinite) || !state.crop.max.every(Number.isFinite)
        || state.crop.min.some((value, index) => value > state.crop!.max[index]!))) {
        return viewerUnsupported('Volume crop bounds are invalid', 'volume-ccp4-v1');
    }
    if (descriptor.semanticKind !== 'segmentation' && state.visibleSegmentIds.length > 0) {
        return viewerUnsupported('Visible segment IDs are valid only for supplied segmentation volumes', 'volume-segmentation-v1');
    }
    if (!unique(state.visibleSegmentIds) || state.visibleSegmentIds.some((id) => !Number.isInteger(id) || id < 0)) {
        return viewerUnsupported('Visible segment IDs must be unique nonnegative integers', 'volume-segmentation-v1');
    }
    if ((descriptor.registrationRef ?? null) !== state.registrationRef) {
        return viewerUnsupported('Volume registration reference does not match its descriptor', 'volume-registration-v1');
    }
    return viewerOk(state);
};

export const validateVolumeRegistration = (
    registration: VolumeRegistrationV1,
    volume: SpatialVolumeDescriptorV1,
    availableBindings: readonly ViewerSnapshotBindingV2[],
): ViewerResult<VolumeRegistrationV1> => {
    if (registration.schema !== 'bms.viewer.volume-registration.v1' || registration.method !== 'supplied_transform_v1'
        || !registration.registrationId.trim() || !SHA256.test(registration.artifactSha256) || !registration.provenanceRef.trim()) {
        return viewerUnsupported('Only provenance-bound supplied volume registration v1 is supported', 'volume-registration-v1');
    }
    if (registration.volumeId !== volume.volumeId || registration.volumeSha256 !== volume.artifactSha256 || !validMatrix(registration.transformRowMajor4x4)) {
        return viewerUnsupported('Volume registration identity, hash, or transform mismatch', 'volume-registration-v1');
    }
    const structure = availableBindings.find((binding) => binding.kind === 'document' && binding.resourceId === registration.structureDocumentId);
    if (!structure || structure.sha256 !== registration.structureSha256) {
        return viewerUnsupported('Volume registration structure binding is unavailable or hash-mismatched', 'volume-registration-v1');
    }
    return viewerOk(registration);
};

export const validateVolumeSegmentation = (
    segmentation: VolumeSegmentationV1,
    volume: SpatialVolumeDescriptorV1,
): ViewerResult<VolumeSegmentationV1> => {
    if (segmentation.schema !== 'bms.viewer.volume-segmentation.v1' || !segmentation.segmentationId.trim()
        || segmentation.volumeId !== volume.volumeId || !segmentation.artifactId.trim()
        || !SHA256.test(segmentation.artifactSha256) || !segmentation.provenanceRef.trim()) {
        return viewerUnsupported('Segmentation identity, artifact binding, and provenance are invalid', 'volume-segmentation-v1');
    }
    const ids = segmentation.labels.map((entry) => entry.segmentId);
    if (!unique(ids) || ids.some((id) => !Number.isInteger(id) || id < 0)) {
        return viewerUnsupported('Segment IDs must be unique nonnegative integers', 'volume-segmentation-v1');
    }
    const byId = new Map(segmentation.labels.map((entry) => [entry.segmentId, entry]));
    for (const entry of segmentation.labels) {
        if (!validColor(entry.recommendedColor) || entry.parentSegmentId === entry.segmentId
            || entry.parentSegmentId !== null && !byId.has(entry.parentSegmentId)) {
            return viewerUnsupported('Segmentation hierarchy or recommended color is invalid', 'volume-segmentation-v1');
        }
        const visited = new Set<number>([entry.segmentId]);
        let parent = entry.parentSegmentId;
        while (parent !== null) {
            if (visited.has(parent)) return viewerUnsupported('Segmentation hierarchy must be acyclic', 'volume-segmentation-v1');
            visited.add(parent);
            parent = byId.get(parent)?.parentSegmentId ?? null;
        }
    }
    return viewerOk(segmentation);
};

export const absoluteContourValue = (state: VolumePresentationStateV1, descriptor: SpatialVolumeDescriptorV1): number => (
    state.contour.mode === 'absolute'
        ? state.contour.value
        : descriptor.statistics!.mean! + state.contour.value * descriptor.statistics!.sigma!
);
