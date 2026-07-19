import type { StructureSceneState } from '../contracts/sceneState.js';
import { createViewerEvent, type ViewerEvent } from '../contracts/viewerEvents.js';
import {
    viewerCancelled,
    viewerError,
    type ViewerResult,
} from '../contracts/viewerResults.js';

export interface StructureSceneEngineAdapter {
    loadScene(state: StructureSceneState, signal: AbortSignal): Promise<ViewerResult<void>>;
    dispose(): Promise<void>;
}

export class StructureSceneController {
    private readonly listeners = new Set<(event: ViewerEvent) => void>();
    private operationToken = 0;
    private abortController: AbortController | undefined;
    private scene: StructureSceneState | undefined;
    private pendingScene: StructureSceneState | undefined;
    private disposed = false;
    private readonly adapter: StructureSceneEngineAdapter;

    constructor(adapter: StructureSceneEngineAdapter) {
        this.adapter = adapter;
    }

    get currentScene(): StructureSceneState | undefined {
        return this.scene;
    }

    subscribe(handler: (event: ViewerEvent) => void): () => void {
        if (this.disposed) return () => undefined;
        this.listeners.add(handler);
        return () => this.listeners.delete(handler);
    }

    async loadScene(state: StructureSceneState): Promise<ViewerResult<void>> {
        if (this.disposed) return viewerCancelled('Structure scene controller is disposed');
        const token = ++this.operationToken;
        this.abortController?.abort();
        const abortController = new AbortController();
        this.abortController = abortController;
        this.pendingScene = state;
        this.emit('scene-loading', state, {});

        let result: ViewerResult<void>;
        try {
            result = await this.adapter.loadScene(state, abortController.signal);
        } catch (error) {
            result = viewerError(error);
        }
        if (this.disposed || token !== this.operationToken || abortController.signal.aborted) {
            return viewerCancelled('Scene load was superseded or disposed');
        }
        this.pendingScene = undefined;
        if (result.status === 'ok') {
            this.scene = state;
            this.emit('scene-ready', state, {});
        } else if (result.status !== 'cancelled') {
            this.emit('scene-error', state, {
                status: result.status,
                reason: result.status === 'error' ? result.error.message : result.reason,
            });
        }
        return result;
    }

    async dispose(): Promise<void> {
        if (this.disposed) return;
        this.disposed = true;
        this.operationToken += 1;
        this.abortController?.abort();
        this.abortController = undefined;
        const terminalScene = this.pendingScene ?? this.scene;
        this.pendingScene = undefined;
        try {
            await this.adapter.dispose();
        } finally {
            if (terminalScene) this.emit('disposed', terminalScene, {});
            this.listeners.clear();
            this.scene = undefined;
        }
    }

    private emit(type: ViewerEvent['type'], state: StructureSceneState, payload: unknown): void {
        const event = createViewerEvent({
            type,
            scene: state.ref,
            documentId: state.activeDocumentId,
            origin: 'controller',
            payload,
            emittedAt: new Date().toISOString(),
        });
        for (const listener of this.listeners) listener(event);
    }
}
