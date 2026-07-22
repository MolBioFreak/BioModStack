import type { ViewerSnapshot, StructureCollectionKind, StructureSceneProvenance, StructureSceneState } from './sceneState.js';
import type { CollectionBrowserStateV1 } from './structureCollections.js';
import type { StructureComparisonStateV1 } from './structureComparison.js';
import { viewerOk, viewerUnsupported, type ViewerResult } from './viewerResults.js';
import type { VolumePresentationStateV1 } from './spatialVolumes.js';

const SHA256 = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}T/;
const EXPORT_MIME: Readonly<Record<ExportKindV1, string>> = Object.freeze({
    snapshot_json: 'application/json', figure_png: 'image/png', table_csv: 'text/csv', table_json: 'application/json',
    selection_mmcif: 'chemical/x-mmcif', trajectory_webm: 'video/webm;codecs=vp9', morph_webm: 'video/webm;codecs=vp9',
});

export type ViewerSnapshotBindingKind = 'document' | 'trajectory' | 'frame_map' | 'volume' | 'metric' | 'mapping' | 'alignment' | 'segmentation' | 'analysis';

export interface ViewerSnapshotBindingV2 {
    readonly kind: ViewerSnapshotBindingKind;
    readonly resourceId: string;
    readonly sha256: string;
    readonly required: boolean;
    readonly capabilityId?: string;
}

export interface ViewerSnapshotV2 {
    readonly schema: 'bms.viewer.snapshot.v2';
    readonly schemaVersion: 2;
    readonly snapshotId: string;
    readonly capturedAt: string;
    readonly engine: {
        readonly package: 'molstar';
        readonly engineVersion: '4.5.0';
        readonly adapterId: 'bms-direct';
        readonly adapterVersion: string;
    };
    readonly requiredCapabilities: readonly string[];
    readonly bindings: readonly ViewerSnapshotBindingV2[];
    readonly scene: StructureSceneState;
    readonly collectionState: CollectionBrowserStateV1 | null;
    readonly comparisonState: StructureComparisonStateV1 | null;
    readonly volumeStates: readonly VolumePresentationStateV1[];
    readonly uiComposition: 'standard' | 'compact';
    readonly provenance: StructureSceneProvenance;
}

export interface CreateViewerSnapshotV2Metadata {
    readonly snapshotId: string;
    readonly capturedAt: string;
    readonly adapterVersion: string;
    readonly bindings: readonly ViewerSnapshotBindingV2[];
    readonly requiredCapabilities?: readonly string[];
    readonly collectionState?: CollectionBrowserStateV1 | null;
    readonly comparisonState?: StructureComparisonStateV1 | null;
    readonly volumeStates?: readonly VolumePresentationStateV1[];
    readonly uiComposition?: 'standard' | 'compact';
    readonly provenance?: StructureSceneProvenance;
}

const assertValidUnicode = (value: string): string => {
    for (let index = 0; index < value.length; index += 1) {
        const unit = value.charCodeAt(index);
        if (unit >= 0xD800 && unit <= 0xDBFF) {
            const next = value.charCodeAt(index + 1);
            if (!(next >= 0xDC00 && next <= 0xDFFF)) throw new Error('Canonical JSON rejects lone UTF-16 surrogates');
            index += 1;
        } else if (unit >= 0xDC00 && unit <= 0xDFFF) {
            throw new Error('Canonical JSON rejects lone UTF-16 surrogates');
        }
    }
    return value;
};

const canonicalize = (value: unknown): unknown => {
    if (value === null || typeof value === 'boolean') return value;
    if (typeof value === 'string') return assertValidUnicode(value);
    if (typeof value === 'number') {
        if (!Number.isFinite(value)) throw new Error('Canonical JSON rejects non-finite numbers');
        return Object.is(value, -0) ? 0 : value;
    }
    if (Array.isArray(value)) return value.map(canonicalize);
    if (typeof value === 'object') {
        const object = value as Record<string, unknown>;
        const output: Record<string, unknown> = {};
        for (const key of Object.keys(object).sort()) {
            assertValidUnicode(key);
            if (object[key] === undefined) throw new Error(`Canonical JSON rejects undefined at ${key}`);
            output[key] = canonicalize(object[key]);
        }
        return output;
    }
    throw new Error(`Canonical JSON does not support ${typeof value}`);
};

/** RFC 8785/JCS for JSON-safe BMS data, using ECMAScript number serialization and UTF-16 key ordering. */
export const canonicalJson = (value: unknown): string => JSON.stringify(canonicalize(value));

export const sha256Hex = async (value: string | Uint8Array): Promise<string> => {
    if (!globalThis.crypto?.subtle) throw new Error('Web Crypto SHA-256 is unavailable');
    const source = typeof value === 'string' ? new TextEncoder().encode(value) : value;
    const buffer = new ArrayBuffer(source.byteLength);
    new Uint8Array(buffer).set(source);
    const digest = await crypto.subtle.digest('SHA-256', buffer);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
};

const bindingKey = (binding: ViewerSnapshotBindingV2): string => `${binding.kind}:${binding.resourceId}`;

export const validateSnapshotBindings = (bindings: readonly ViewerSnapshotBindingV2[]): ViewerResult<readonly ViewerSnapshotBindingV2[]> => {
    const seen = new Set<string>();
    for (const binding of bindings) {
        if (!binding.resourceId.trim() || !SHA256.test(binding.sha256) || typeof binding.required !== 'boolean') {
            return viewerUnsupported('Snapshot bindings require identity, required state, and lowercase SHA-256', 'snapshot-v2');
        }
        const key = bindingKey(binding);
        if (seen.has(key)) return viewerUnsupported(`Duplicate snapshot binding ${key}`, 'snapshot-v2');
        seen.add(key);
    }
    return viewerOk(bindings);
};

const durableScene = (scene: StructureSceneState): StructureSceneState => ({
    ...scene,
    documents: scene.documents.map(({ sourceUrl: _transport, ...document }) => document),
});

const requireBinding = (bindings: readonly ViewerSnapshotBindingV2[], kind: ViewerSnapshotBindingKind, resourceId: string): void => {
    const binding = bindings.find((entry) => entry.kind === kind && entry.resourceId === resourceId);
    if (!binding) throw new Error(`Snapshot resource ${kind}:${resourceId} requires exactly one binding`);
};

export const createViewerSnapshotV2 = (scene: StructureSceneState, metadata: CreateViewerSnapshotV2Metadata): ViewerSnapshotV2 => {
    if (!UUID.test(metadata.snapshotId) || !ISO_DATE.test(metadata.capturedAt) || !metadata.adapterVersion.trim()) {
        throw new Error('Snapshot identity, timestamp, and adapter version are required');
    }
    const validated = validateSnapshotBindings(metadata.bindings);
    if (validated.status !== 'ok') throw new Error(validated.status === 'error' ? validated.error.message : validated.reason);
    for (const document of scene.documents) {
        requireBinding(validated.value, 'document', document.documentId);
        const binding = validated.value.find((entry) => entry.kind === 'document' && entry.resourceId === document.documentId)!;
        if (!binding.required || document.contentSha256?.toLowerCase() !== binding.sha256) throw new Error(`Snapshot document ${document.documentId} binding is missing or hash-mismatched`);
    }
    for (const volume of metadata.volumeStates ?? []) requireBinding(validated.value, 'volume', volume.volumeId);
    if (metadata.comparisonState) requireBinding(validated.value, 'alignment', metadata.comparisonState.alignmentId);
    const requiredCapabilities = [...new Set(['snapshot-v2', ...(metadata.requiredCapabilities ?? [])])].sort();
    return JSON.parse(canonicalJson({
        schema: 'bms.viewer.snapshot.v2', schemaVersion: 2,
        snapshotId: metadata.snapshotId, capturedAt: metadata.capturedAt,
        engine: { package: 'molstar', engineVersion: '4.5.0', adapterId: 'bms-direct', adapterVersion: metadata.adapterVersion },
        requiredCapabilities, bindings: validated.value, scene: durableScene(scene),
        collectionState: metadata.collectionState ?? null,
        comparisonState: metadata.comparisonState ?? null,
        volumeStates: metadata.volumeStates ?? [],
        uiComposition: metadata.uiComposition ?? 'standard',
        provenance: metadata.provenance ?? scene.provenance,
    })) as ViewerSnapshotV2;
};

export const migrateViewerSnapshotV1 = (snapshot: ViewerSnapshot, snapshotId: string): ViewerSnapshotV2 => createViewerSnapshotV2(snapshot.scene, {
    snapshotId, capturedAt: snapshot.capturedAt, adapterVersion: snapshot.adapterVersion,
    bindings: snapshot.scene.documents.map((document) => {
        const sha256 = snapshot.documentHashes[document.documentId] ?? document.contentSha256;
        if (!sha256) throw new Error(`Legacy snapshot document ${document.documentId} has no SHA-256 and cannot migrate`);
        return { kind: 'document' as const, resourceId: document.documentId, sha256: sha256.toLowerCase(), required: true };
    }),
    collectionState: null, comparisonState: null, volumeStates: [], uiComposition: 'standard', provenance: snapshot.scene.provenance,
});

export const restoreViewerSnapshotV2 = (
    snapshot: ViewerSnapshotV2,
    availableBindings: readonly ViewerSnapshotBindingV2[],
): ViewerResult<StructureSceneState> => {
    if (snapshot.schema !== 'bms.viewer.snapshot.v2' || snapshot.schemaVersion !== 2
        || snapshot.engine.package !== 'molstar' || snapshot.engine.engineVersion !== '4.5.0' || snapshot.engine.adapterId !== 'bms-direct') {
        return viewerUnsupported('Unsupported viewer snapshot schema or engine adapter', 'snapshot-v2');
    }
    const validated = validateSnapshotBindings(snapshot.bindings);
    if (validated.status !== 'ok') return validated as ViewerResult<never>;
    const available = new Map(availableBindings.map((binding) => [bindingKey(binding), binding]));
    const failures: string[] = [];
    for (const binding of snapshot.bindings) {
        const current = available.get(bindingKey(binding));
        if (!current && binding.required) failures.push(`${bindingKey(binding)} unavailable`);
        else if (current && current.sha256 !== binding.sha256) failures.push(`${bindingKey(binding)} hash mismatch`);
    }
    if (failures.length) return viewerUnsupported(`Snapshot restore refused: ${failures.join('; ')}`, 'snapshot-v2');
    return viewerOk(JSON.parse(canonicalJson(snapshot.scene)) as StructureSceneState);
};

export type ExportKindV1 = 'snapshot_json' | 'figure_png' | 'table_csv' | 'table_json' | 'selection_mmcif' | 'trajectory_webm' | 'morph_webm';

export interface ExportManifestV1 {
    readonly schema: 'bms.viewer.export-manifest.v1';
    readonly exportId: string;
    readonly kind: ExportKindV1;
    readonly createdAt: string;
    readonly jobId: string;
    readonly workflowContext: Readonly<Record<string, string | number | boolean | null>>;
    readonly actorId?: string;
    readonly snapshotId: string;
    readonly collectionKind: StructureCollectionKind;
    readonly sourceBindings: readonly ViewerSnapshotBindingV2[];
    readonly engine: ViewerSnapshotV2['engine'];
    readonly sceneStateSha256: string;
    readonly exportParameters: Readonly<Record<string, string | number | boolean | null>>;
    readonly outputFileName: string;
    readonly outputMimeType: string;
    readonly outputByteLength: number;
    readonly outputSha256: string;
    readonly semanticWarnings: readonly string[];
}

export interface CreateExportManifestInput {
    readonly exportId: string;
    readonly kind: ExportKindV1;
    readonly createdAt: string;
    readonly jobId: string;
    readonly workflowContext?: Readonly<Record<string, string | number | boolean | null>>;
    readonly actorId?: string;
    readonly snapshot: ViewerSnapshotV2;
    readonly exportParameters?: Readonly<Record<string, string | number | boolean | null>>;
    readonly outputFileName: string;
    readonly output: Uint8Array;
    readonly semanticWarnings?: readonly string[];
}

export const createExportManifest = async (input: CreateExportManifestInput): Promise<ExportManifestV1> => {
    if (!UUID.test(input.exportId) || !input.jobId.trim() || !/^[^/\\\u0000-\u001f]+$/.test(input.outputFileName)) throw new Error('Export identity, job, or output basename is invalid');
    const warnings = [...new Set(input.semanticWarnings ?? [])];
    const morphWarning = 'visual_interpolation_not_physical_trajectory';
    if (input.kind === 'morph_webm' && !warnings.includes(morphWarning)) warnings.push(morphWarning);
    if (input.kind === 'trajectory_webm' && warnings.includes(morphWarning)) throw new Error('Trajectory export cannot carry the morph semantic warning');
    const sceneStateSha256 = await sha256Hex(canonicalJson(input.snapshot.scene));
    return {
        schema: 'bms.viewer.export-manifest.v1', exportId: input.exportId, kind: input.kind, createdAt: input.createdAt,
        jobId: input.jobId, workflowContext: input.workflowContext ?? {}, ...(input.actorId ? { actorId: input.actorId } : {}),
        snapshotId: input.snapshot.snapshotId, collectionKind: input.snapshot.scene.collection?.kind ?? 'static_complex_components',
        sourceBindings: input.snapshot.bindings, engine: input.snapshot.engine, sceneStateSha256,
        exportParameters: input.exportParameters ?? {}, outputFileName: input.outputFileName,
        outputMimeType: EXPORT_MIME[input.kind], outputByteLength: input.output.byteLength,
        outputSha256: await sha256Hex(input.output), semanticWarnings: warnings,
    };
};

export const validateExportManifest = (manifest: ExportManifestV1): ViewerResult<ExportManifestV1> => {
    if (!UUID.test(manifest.exportId) || !UUID.test(manifest.snapshotId) || !SHA256.test(manifest.sceneStateSha256)
        || !SHA256.test(manifest.outputSha256) || !manifest.jobId.trim() || manifest.outputMimeType !== EXPORT_MIME[manifest.kind]
        || !Number.isInteger(manifest.outputByteLength) || manifest.outputByteLength < 0
        || !/^[^/\\\u0000-\u001f]+$/.test(manifest.outputFileName)) {
        return viewerUnsupported('Export manifest identity, output, MIME, or hashes are invalid', 'governed-export');
    }
    const bindings = validateSnapshotBindings(manifest.sourceBindings);
    return bindings.status === 'ok' ? viewerOk(manifest) : bindings as ViewerResult<never>;
};

const csvCell = (value: unknown): string => {
    if (value === null || value === undefined) return '';
    const text = typeof value === 'object' ? canonicalJson(value) : String(value);
    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
};

export const rowsToCsv = (rows: readonly Readonly<Record<string, unknown>>[], columns: readonly string[]): string => (
    `${columns.map(csvCell).join(',')}\r\n${rows.map((row) => columns.map((column) => csvCell(row[column])).join(',')).join('\r\n')}${rows.length ? '\r\n' : ''}`
);
