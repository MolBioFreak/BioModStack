import type { StructureSceneRef } from './structureIdentity.js';

export type ViewerEventOrigin = 'canvas' | 'sequence' | 'matrix' | 'table' | 'controller' | 'restore' | 'runtime'
    | 'ensemble' | 'comparison' | 'trajectory' | 'volume' | 'snapshot' | 'export';
export type ViewerEventType =
    | 'runtime-ready' | 'scene-loading' | 'scene-ready' | 'scene-error'
    | 'selection-changed' | 'hover-changed' | 'focus-changed'
    | 'frame-changed' | 'candidate-changed' | 'camera-changed'
    | 'measurement-created' | 'measurement-removed' | 'layer-changed' | 'filter-changed'
    | 'capability-unsupported' | 'volume-loaded' | 'volume-presentation-changed' | 'volume-removed'
    | 'segment-selection-changed' | 'snapshot-restore-state-changed' | 'export-state-changed'
    | 'snapshot-restored' | 'export-completed' | 'disposed';

export interface ViewerEvent<TPayload = unknown> {
    readonly type: ViewerEventType;
    readonly viewerId: string;
    readonly sceneId: string;
    readonly generation: number;
    readonly documentId: string | null;
    readonly resourceId: string | null;
    readonly origin: ViewerEventOrigin;
    readonly payload: TPayload;
    readonly emittedAt: string;
}

export interface ViewerEventInput<TPayload> {
    readonly type: ViewerEventType;
    readonly scene: StructureSceneRef;
    readonly documentId: string | null;
    readonly resourceId?: string | null;
    readonly origin: ViewerEventOrigin;
    readonly payload: TPayload;
    readonly emittedAt: string;
}

export const createViewerEvent = <TPayload>(input: ViewerEventInput<TPayload>): ViewerEvent<TPayload> => ({
    type: input.type,
    viewerId: input.scene.viewerId,
    sceneId: input.scene.sceneId,
    generation: input.scene.generation,
    documentId: input.documentId,
    resourceId: input.resourceId ?? null,
    origin: input.origin,
    payload: input.payload,
    emittedAt: input.emittedAt,
});
