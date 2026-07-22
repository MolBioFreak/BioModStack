import { createViewerSnapshot, type StructureSceneState, type ViewerSnapshot } from '../contracts/sceneState.js';
import type { StructureCameraState, StructureScenePresentation, StructureSelectionSet } from '../contracts/scenePresentation.js';
import type { ResidueRef } from '../contracts/structureIdentity.js';
import type { MDPlaybackState, MDSourceFrameRef } from '../contracts/mdTrajectory.js';
import { createViewerEvent, type ViewerEvent, type ViewerEventOrigin } from '../contracts/viewerEvents.js';
import {
    viewerCancelled,
    viewerError,
    viewerOk,
    viewerUnsupported,
    type ViewerResult,
} from '../contracts/viewerResults.js';
import type { MolstarEngineAdapter, MolstarEngineDiagnostics } from './MolstarEngineAdapter.js';
/** @deprecated Import MolstarEngineAdapter from ./MolstarEngineAdapter.js. */
export type StructureSceneEngineAdapter = MolstarEngineAdapter;

export class StructureSceneController {
    private readonly listeners = new Set<(event: ViewerEvent) => void>();
    private operationToken = 0;
    private abortController: AbortController | undefined;
    private scene: StructureSceneState | undefined;
    private pendingScene: StructureSceneState | undefined;
    private disposed = false;
    private readonly adapter: MolstarEngineAdapter;
    private readonly unsubscribeResidueClicks: () => void;

    constructor(adapter: MolstarEngineAdapter) {
        this.adapter = adapter;
        this.unsubscribeResidueClicks = adapter.subscribeResidueClicks((click) => {
            const scene = this.scene;
            if (!scene || click.residue.documentId !== scene.activeDocumentId && !scene.documents.some((entry) => entry.documentId === click.residue.documentId)) return;
            this.emit('selection-changed', scene, click, 'canvas', click.residue.documentId);
        });
    }

    get currentScene(): StructureSceneState | undefined {
        return this.scene;
    }

    diagnostics(): MolstarEngineDiagnostics { return this.adapter.diagnostics(); }

    subscribe(handler: (event: ViewerEvent) => void): () => void {
        if (this.disposed) return () => undefined;
        this.listeners.add(handler);
        return () => this.listeners.delete(handler);
    }

    publish(type: ViewerEvent['type'], origin: ViewerEventOrigin, payload: unknown): void {
        if (this.disposed || !this.scene) return;
        this.emit(type, this.scene, payload, origin);
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
            result = await this.adapter.reconcileScene(this.scene, state, abortController.signal);
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

    reconcileScene(state: StructureSceneState): Promise<ViewerResult<void>> { return this.loadScene(state); }

    async setSelection(selection: readonly StructureSelectionSet[]): Promise<ViewerResult<void>> {
        const result = await this.updatePresentation({ selection });
        if (result.status === 'ok' && this.scene) this.emit('selection-changed', this.scene, selection);
        return result;
    }

    async setHover(hover: ResidueRef | undefined): Promise<ViewerResult<void>> {
        const result = await this.updatePresentation({ hover });
        if (result.status === 'ok' && this.scene) this.emit('hover-changed', this.scene, hover ?? null);
        return result;
    }

    async focus(residue: ResidueRef): Promise<ViewerResult<void>> {
        const scene = this.scene;
        if (!scene) return viewerUnsupported('No scene is ready to focus', 'focus');
        const focusQuery = {
            documentId: residue.documentId, entityId: residue.entityId,
            labelAsymId: residue.labelAsymId, authAsymId: residue.authAsymId,
            startLabelSeqId: residue.labelSeqId, endLabelSeqId: residue.labelSeqId,
            startAuthSeqId: residue.authSeqId, endAuthSeqId: residue.authSeqId,
            insertionCode: residue.insertionCode, focus: true,
        };
        const result = await this.updatePresentation({
            colorQueries: [...(scene.presentation?.colorQueries ?? []), focusQuery],
        });
        if (result.status === 'ok' && this.scene) this.emit('focus-changed', this.scene, residue);
        return result;
    }

    async setCamera(camera: StructureCameraState): Promise<ViewerResult<void>> {
        const result = await this.updatePresentation({ camera });
        if (result.status === 'ok' && this.scene) this.emit('camera-changed', this.scene, camera);
        return result;
    }

    captureSnapshot(): ViewerResult<ViewerSnapshot> {
        if (!this.scene) return viewerUnsupported('No ready scene is available to snapshot', 'snapshots');
        const diagnostics = this.adapter.diagnostics();
        return viewerOk(createViewerSnapshot(this.scene, {
            adapterVersion: `${diagnostics.wrapper}:${diagnostics.engineVersion}`,
            capturedAt: new Date().toISOString(),
        }));
    }

    async selectMDSourceFrame(frame: MDSourceFrameRef): Promise<ViewerResult<void>> {
        const state = this.scene?.molecularDynamics;
        if (!state?.playbackCapability.supported || !this.adapter.selectMDSourceFrame) {
            return viewerUnsupported(state?.playbackCapability.reason ?? 'Governed MD playback is unavailable', 'trajectories');
        }
        if (frame.replica !== state.activeReplica) {
            return viewerUnsupported('Source frame does not belong to the active MD replica', 'trajectories');
        }
        const token = ++this.operationToken;
        this.abortController?.abort();
        const abortController = new AbortController();
        this.abortController = abortController;
        const result = await this.adapter.selectMDSourceFrame(frame, abortController.signal);
        return this.disposed || token !== this.operationToken || abortController.signal.aborted
            ? viewerCancelled('MD frame selection was superseded or disposed') : result;
    }

    async setMDPlayback(playback: MDPlaybackState): Promise<ViewerResult<void>> {
        const state = this.scene?.molecularDynamics;
        if (!state?.playbackCapability.supported || !this.adapter.setMDPlayback) {
            return viewerUnsupported(state?.playbackCapability.reason ?? 'Governed MD playback is unavailable', 'trajectories');
        }
        const token = ++this.operationToken;
        this.abortController?.abort();
        const abortController = new AbortController();
        this.abortController = abortController;
        const result = await this.adapter.setMDPlayback(playback, abortController.signal);
        return this.disposed || token !== this.operationToken || abortController.signal.aborted
            ? viewerCancelled('MD playback update was superseded or disposed') : result;
    }

    async dispose(): Promise<void> {
        if (this.disposed) return;
        this.disposed = true;
        this.operationToken += 1;
        this.abortController?.abort();
        this.abortController = undefined;
        this.unsubscribeResidueClicks();
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

    private updatePresentation(patch: Partial<StructureScenePresentation>): Promise<ViewerResult<void>> {
        if (!this.scene) return Promise.resolve(viewerUnsupported('No scene is ready for presentation reconciliation', 'scene-presentation'));
        const next: StructureSceneState = {
            ...this.scene,
            ref: { ...this.scene.ref, generation: this.scene.ref.generation + 1 },
            presentation: { ...this.scene.presentation, ...patch },
        };
        return this.loadScene(next);
    }

    private emit(type: ViewerEvent['type'], state: StructureSceneState, payload: unknown, origin: ViewerEventOrigin = 'controller', documentId = state.activeDocumentId): void {
        const event = createViewerEvent({
            type,
            scene: state.ref,
            documentId,
            origin,
            payload,
            emittedAt: new Date().toISOString(),
        });
        for (const listener of this.listeners) listener(event);
    }
}
