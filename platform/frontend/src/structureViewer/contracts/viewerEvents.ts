import type { StructureSceneRef } from './structureIdentity.js';

export type ViewerEventOrigin = 'canvas' | 'sequence' | 'matrix' | 'table' | 'controller' | 'restore' | 'runtime';
export type ViewerEventType =
    | 'runtime-ready'
    | 'scene-loading'
    | 'scene-ready'
    | 'scene-error'
    | 'selection-changed'
    | 'hover-changed'
    | 'focus-changed'
    | 'frame-changed'
    | 'candidate-changed'
    | 'camera-changed'
    | 'measurement-created'
    | 'measurement-removed'
    | 'layer-changed'
    | 'filter-changed'
    | 'capability-unsupported'
    | 'disposed';

export interface ViewerEvent<TPayload = unknown> {
    readonly type: ViewerEventType;
    readonly viewerId: string;
    readonly sceneId: string;
    readonly generation: number;
    readonly documentId: string;
    readonly origin: ViewerEventOrigin;
    readonly payload: TPayload;
    readonly emittedAt: string;
}

export interface ViewerEventInput<TPayload> {
    readonly type: ViewerEventType;
    readonly scene: StructureSceneRef;
    readonly documentId: string;
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
    origin: input.origin,
    payload: input.payload,
    emittedAt: input.emittedAt,
});
