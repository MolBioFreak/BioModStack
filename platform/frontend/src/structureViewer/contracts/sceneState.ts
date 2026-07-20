import type { StructureDocumentRef, StructureSceneRef } from './structureIdentity.js';
import type { StructureScenePresentation } from './scenePresentation.js';
import { validateMDSceneState, type MDSceneState } from './mdTrajectory.js';
import { viewerOk, viewerUnsupported, type ViewerResult } from './viewerResults.js';

export type StructureCollectionKind =
    | 'independent_hypotheses'
    | 'experimental_ensemble'
    | 'coordinate_trajectory'
    | 'interpolated_morph'
    | 'matched_state_series'
    | 'static_complex_components';

export interface StructureCollectionRef {
    readonly kind: StructureCollectionKind;
    readonly orderedDocumentIds: readonly string[];
}

export interface StructureSceneProvenance {
    readonly createdBy: string;
    readonly createdAt: string;
    readonly sourceRevision?: string;
    readonly workflowId?: string;
    readonly jobId?: string;
}

export interface StructureSceneState {
    readonly schemaVersion: 1;
    readonly ref: StructureSceneRef;
    readonly documents: readonly StructureDocumentRef[];
    readonly collection?: StructureCollectionRef;
    readonly activeDocumentId: string;
    readonly provenance: StructureSceneProvenance;
    readonly presentation?: StructureScenePresentation;
    readonly molecularDynamics?: MDSceneState;
}

export type StructureSceneStateInput = Omit<StructureSceneState, 'schemaVersion'>;

const SHA256 = /^[0-9a-f]{64}$/i;
const unique = (values: readonly string[]): boolean => new Set(values).size === values.length;

export const createStructureSceneState = (input: StructureSceneStateInput): ViewerResult<StructureSceneState> => {
    if (!input.ref.viewerId.trim() || !input.ref.sceneId.trim() || !Number.isInteger(input.ref.generation) || input.ref.generation < 0) {
        return viewerUnsupported('Scene identity requires viewerId, sceneId, and a non-negative integer generation', 'scene-identity');
    }
    if (input.documents.length === 0) return viewerUnsupported('A scene requires at least one document', 'scene-documents');
    const documentIds = input.documents.map((document) => document.documentId);
    if (documentIds.some((id) => !id.trim()) || !unique(documentIds)) {
        return viewerUnsupported('Scene document IDs must be non-empty and unique', 'document-identity');
    }
    if (input.documents.some((document) => document.contentSha256 !== undefined && !SHA256.test(document.contentSha256))) {
        return viewerUnsupported('Document contentSha256 must be a 64-character hexadecimal SHA-256', 'document-provenance');
    }
    if (!documentIds.includes(input.activeDocumentId)) {
        return viewerUnsupported('activeDocumentId must identify a document in the scene', 'scene-documents');
    }
    if (input.documents.length > 1 && !input.collection) {
        return viewerUnsupported('A multi-document scene requires an explicit collection kind and ordering', 'collection-semantics');
    }
    if (input.collection) {
        const ordered = input.collection.orderedDocumentIds;
        if (!unique(ordered)
            || ordered.length !== documentIds.length
            || ordered.some((id) => !documentIds.includes(id))) {
            return viewerUnsupported('Collection ordering must contain every scene document exactly once', 'collection-semantics');
        }
    }
    if (!input.provenance.createdBy.trim() || !input.provenance.createdAt.trim()) {
        return viewerUnsupported('Scene provenance requires createdBy and createdAt', 'provenance');
    }
    if (input.molecularDynamics) {
        const md = validateMDSceneState(input.molecularDynamics);
        if (md.status !== 'ok') {
            return viewerUnsupported(
                md.status === 'error' ? md.error.message : md.reason,
                md.status === 'unsupported' ? md.capability : 'trajectories',
            );
        }
    }
    return viewerOk({ schemaVersion: 1, ...input });
};

export interface ViewerSnapshot {
    readonly schemaVersion: 1;
    readonly scene: StructureSceneState;
    readonly adapterVersion: string;
    readonly capturedAt: string;
    readonly documentHashes: Readonly<Record<string, string | null>>;
}

export interface ViewerSnapshotMetadata {
    readonly adapterVersion: string;
    readonly capturedAt: string;
}

export const createViewerSnapshot = (
    scene: StructureSceneState,
    metadata: ViewerSnapshotMetadata,
): ViewerSnapshot => ({
    schemaVersion: 1,
    scene: JSON.parse(JSON.stringify(scene)) as StructureSceneState,
    adapterVersion: metadata.adapterVersion,
    capturedAt: metadata.capturedAt,
    documentHashes: Object.fromEntries(scene.documents.map((document) => [
        document.documentId,
        document.contentSha256 ?? null,
    ])),
});

export interface CurrentDocumentIdentity {
    readonly documentId: string;
    readonly contentSha256?: string;
}

export const restoreViewerSnapshot = (
    snapshot: ViewerSnapshot,
    currentDocuments: readonly CurrentDocumentIdentity[],
): ViewerResult<StructureSceneState> => {
    if (snapshot.schemaVersion !== 1 || snapshot.scene.schemaVersion !== 1) {
        return viewerUnsupported('Unsupported viewer snapshot schema version', 'snapshots');
    }
    const currentById = new Map(currentDocuments.map((document) => [document.documentId, document]));
    for (const document of snapshot.scene.documents) {
        const current = currentById.get(document.documentId);
        if (!current) return viewerUnsupported(`Snapshot document ${document.documentId} is unavailable`, 'snapshots');
        const expectedHash = snapshot.documentHashes[document.documentId];
        if (expectedHash && current.contentSha256 !== expectedHash) {
            return viewerUnsupported(`Snapshot document ${document.documentId} hash mismatch`, 'snapshots');
        }
    }
    return createStructureSceneState({
        ref: snapshot.scene.ref,
        documents: snapshot.scene.documents,
        collection: snapshot.scene.collection,
        activeDocumentId: snapshot.scene.activeDocumentId,
        provenance: snapshot.scene.provenance,
        presentation: snapshot.scene.presentation,
        molecularDynamics: snapshot.scene.molecularDynamics,
    });
};
