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
import {
    VOLUME_HARD_LIMITS,
    validateSpatialVolumeDescriptor,
    validateVolumeRegistration,
    validateVolumeSegmentation,
    validateVolumePresentationState,
    type SpatialVolumeDescriptorV1,
    type VolumePresentationStateV1,
    type VolumeRegistrationV1,
    type VolumeSegmentationV1,
} from '../contracts/spatialVolumes.js';
import {
    restoreViewerSnapshotV2,
    type ViewerSnapshotBindingV2,
    type ViewerSnapshotV2,
} from '../contracts/m6Reproducibility.js';
import {
    encodeGovernedWebM,
    type AuthoritativeFrameStepper,
    type GovernedWebMResult,
    type MovieExportRequestV1,
} from './browserMovieExport.js';

const sceneResourceKey = (state: StructureSceneState): string => JSON.stringify({
    documents: state.documents,
    activeDocumentId: state.activeDocumentId,
    collection: state.collection ?? null,
});
const volumeEstimateBytes = (descriptor: SpatialVolumeDescriptorV1): number => descriptor.dimensions.reduce((product, value) => product * value, 1) * 8;
const volumeRuntimeBudget = (): { residentBytes: number; visible: number } => {
    const memory = (globalThis.navigator as (Navigator & { deviceMemory?: number }) | undefined)?.deviceMemory;
    return {
        residentBytes: memory ? Math.min(1024 * 1024 * 1024, Math.max(128 * 1024 * 1024, memory * 128 * 1024 * 1024)) : 512 * 1024 * 1024,
        visible: memory !== undefined && memory <= 4 ? 1 : 2,
    };
};

/** @deprecated Import MolstarEngineAdapter from ./MolstarEngineAdapter.js. */
export type StructureSceneEngineAdapter = MolstarEngineAdapter;

export class StructureSceneController {
    private readonly listeners = new Set<(event: ViewerEvent) => void>();
    private operationToken = 0;
    private abortController: AbortController | undefined;
    private exportToken = 0;
    private exportAbortController: AbortController | undefined;
    private scene: StructureSceneState | undefined;
    private pendingScene: StructureSceneState | undefined;
    private disposed = false;
    private readonly adapter: MolstarEngineAdapter;
    private readonly unsubscribeResidueClicks: () => void;
    private readonly loadedVolumes = new Map<string, SpatialVolumeDescriptorV1>();
    private readonly volumePresentations = new Map<string, VolumePresentationStateV1>();
    private readonly appliedRegistrations = new Map<string, VolumeRegistrationV1>();
    private readonly appliedSegmentations = new Map<string, VolumeSegmentationV1>();

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

    cancelCurrentOperation(): void {
        this.operationToken += 1;
        this.abortController?.abort();
        this.abortController = undefined;
    }

    cancelCurrentExport(): void {
        this.exportToken += 1;
        this.exportAbortController?.abort();
        this.exportAbortController = undefined;
    }

    publish(type: ViewerEvent['type'], origin: ViewerEventOrigin, payload: unknown): void {
        if (this.disposed || !this.scene) return;
        this.emit(type, this.scene, payload, origin);
    }

    async loadScene(state: StructureSceneState): Promise<ViewerResult<void>> {
        if (this.disposed) return viewerCancelled('Structure scene controller is disposed');
        const replacingResources = !this.scene || sceneResourceKey(this.scene) !== sceneResourceKey(state);
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
            if (replacingResources) {
                this.loadedVolumes.clear();
                this.volumePresentations.clear();
                this.appliedRegistrations.clear();
                this.appliedSegmentations.clear();
            }
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

    bindDocumentHashes(hashes: Readonly<Record<string, string>>): ViewerResult<StructureSceneState> {
        if (!this.scene) return viewerUnsupported('No ready scene is available for artifact binding', 'snapshot-v2');
        const missing = this.scene.documents.filter((document) => !/^[0-9a-f]{64}$/.test(hashes[document.documentId]?.toLowerCase() ?? ''));
        if (missing.length) {
            return viewerUnsupported(`Verified SHA-256 is missing for documents: ${missing.map((document) => document.documentId).join(', ')}`, 'snapshot-v2');
        }
        const documents = this.scene.documents.map((document) => ({
            ...document,
            contentSha256: hashes[document.documentId]!.toLowerCase(),
        }));
        this.scene = { ...this.scene, documents };
        this.emit('scene-ready', this.scene, { documentHashesBound: documents.length }, 'controller');
        return viewerOk(this.scene);
    }

    async restoreSnapshotV2(
        snapshot: ViewerSnapshotV2,
        availableBindings: readonly ViewerSnapshotBindingV2[],
        resources?: {
            readonly volumes: readonly SpatialVolumeDescriptorV1[];
            readonly segmentations: readonly VolumeSegmentationV1[];
            readonly registrations: readonly VolumeRegistrationV1[];
        },
    ): Promise<ViewerResult<void>> {
        const preflight = restoreViewerSnapshotV2(snapshot, availableBindings);
        if (preflight.status !== 'ok') return preflight as ViewerResult<never>;
        const previous = this.scene;
        if (!previous) return viewerUnsupported('No current scene is available as the transactional restore base', 'snapshot-v2');
        const volumeById = new Map((resources?.volumes ?? []).map((value) => [value.volumeId, value]));
        const segmentationByVolume = new Map((resources?.segmentations ?? []).map((value) => [value.volumeId, value]));
        const registrationById = new Map((resources?.registrations ?? []).map((value) => [value.registrationId, value]));
        for (const state of snapshot.volumeStates) {
            const descriptor = volumeById.get(state.volumeId);
            if (!descriptor) return viewerUnsupported(`Snapshot volume ${state.volumeId} has no governed descriptor`, 'snapshot-v2');
            const descriptorResult = validateSpatialVolumeDescriptor(descriptor);
            const stateResult = validateVolumePresentationState(state, descriptor);
            if (descriptorResult.status !== 'ok' || stateResult.status !== 'ok') return viewerUnsupported(`Snapshot volume ${state.volumeId} failed admission`, 'snapshot-v2');
            if (state.registrationRef && !registrationById.has(state.registrationRef)) {
                return viewerUnsupported(`Snapshot registration ${state.registrationRef} is unavailable`, 'snapshot-v2');
            }
            if (descriptor.semanticKind === 'segmentation' && !segmentationByVolume.has(state.volumeId)) {
                return viewerUnsupported(`Snapshot segmentation metadata for ${state.volumeId} is unavailable`, 'snapshot-v2');
            }
        }
        const previousVolumes = [...this.loadedVolumes.values()];
        const previousPresentations = [...this.volumePresentations.values()];
        const previousRegistrations = [...this.appliedRegistrations.values()];
        const previousSegmentations = [...this.appliedSegmentations.values()];
        const currentDocuments = new Map(previous.documents.map((document) => [document.documentId, document]));
        const unresolved = preflight.value.documents.filter((document) => !currentDocuments.get(document.documentId)?.sourceUrl);
        if (unresolved.length) {
            return viewerUnsupported(`Snapshot restore transport is unavailable for: ${unresolved.map((document) => document.documentId).join(', ')}`, 'snapshot-v2');
        }
        const next: StructureSceneState = {
            ...preflight.value,
            documents: preflight.value.documents.map((document) => ({
                ...document,
                sourceUrl: currentDocuments.get(document.documentId)!.sourceUrl,
            })),
        };
        const applyResources = async (
            volumes: readonly SpatialVolumeDescriptorV1[], presentations: readonly VolumePresentationStateV1[],
            registrations: readonly VolumeRegistrationV1[], segmentations: readonly VolumeSegmentationV1[],
        ): Promise<ViewerResult<void>> => {
            const descriptorMap = new Map(volumes.map((value) => [value.volumeId, value]));
            const registrationMap = new Map(registrations.map((value) => [value.registrationId, value]));
            const segmentationMap = new Map(segmentations.map((value) => [value.volumeId, value]));
            for (const presentation of presentations) {
                const descriptor = descriptorMap.get(presentation.volumeId);
                if (!descriptor) return viewerUnsupported(`Restore descriptor ${presentation.volumeId} is unavailable`, 'snapshot-v2');
                const segmentation = segmentationMap.get(presentation.volumeId);
                const initial = segmentation ? { ...presentation, visible: false, visibleSegmentIds: [] } : presentation;
                const loaded = await this.loadVolume(descriptor, initial);
                if (loaded.status !== 'ok') return loaded;
                const registration = presentation.registrationRef ? registrationMap.get(presentation.registrationRef) : undefined;
                if (registration) {
                    const applied = await this.applyVolumeRegistration(registration);
                    if (applied.status !== 'ok') return applied;
                }
                if (segmentation) {
                    const applied = await this.applyVolumeSegmentation(segmentation);
                    if (applied.status !== 'ok') return applied;
                }
                if (registration || segmentation) {
                    const represented = await this.setVolumePresentation(presentation);
                    if (represented.status !== 'ok') return represented;
                }
            }
            return viewerOk(undefined);
        };
        const restored = await this.loadScene(next);
        const resourcesRestored = restored.status === 'ok'
            ? await applyResources(resources?.volumes ?? [], snapshot.volumeStates, resources?.registrations ?? [], resources?.segmentations ?? [])
            : restored;
        if (resourcesRestored.status !== 'ok') {
            const sceneRollback = await this.loadScene(previous);
            const resourceRollback = sceneRollback.status === 'ok'
                ? await applyResources(previousVolumes, previousPresentations, previousRegistrations, previousSegmentations)
                : sceneRollback;
            if (resourceRollback.status !== 'ok') return viewerError(new Error('Snapshot restore and transactional rollback both failed'));
            return resourcesRestored;
        }
        this.emit('snapshot-restored', next, { snapshotId: snapshot.snapshotId }, 'snapshot');
        return viewerOk(undefined);
    }

    async loadVolume(
        descriptor: SpatialVolumeDescriptorV1,
        presentation: VolumePresentationStateV1,
    ): Promise<ViewerResult<void>> {
        const descriptorResult = validateSpatialVolumeDescriptor(descriptor);
        if (descriptorResult.status !== 'ok') return descriptorResult as ViewerResult<never>;
        const presentationResult = validateVolumePresentationState(presentation, descriptor);
        if (presentationResult.status !== 'ok') return presentationResult as ViewerResult<never>;
        if (!this.scene) return viewerUnsupported('No ready scene is available for volume loading', 'volume-ccp4-v1');
        if (!this.adapter.loadVolume || !this.adapter.setVolumePresentation || !this.adapter.removeVolume) {
            return viewerUnsupported('The active runtime does not support governed volumes', 'volume-ccp4-v1');
        }
        const budget = volumeRuntimeBudget();
        const resident = [...this.loadedVolumes.values()]
            .filter((value) => value.volumeId !== descriptor.volumeId)
            .reduce((sum, value) => sum + volumeEstimateBytes(value), 0);
        const otherVisible = [...this.volumePresentations.values()].filter((value) => value.visible && value.volumeId !== descriptor.volumeId).length;
        if (this.loadedVolumes.size >= VOLUME_HARD_LIMITS.maxResidentDescriptors && !this.loadedVolumes.has(descriptor.volumeId)
            || resident + volumeEstimateBytes(descriptor) > budget.residentBytes) {
            return viewerUnsupported('Volume exceeds the capability-derived resident memory budget', 'volume-ccp4-v1');
        }
        if (presentation.visible && otherVisible >= Math.min(budget.visible, VOLUME_HARD_LIMITS.maxVisibleVolumes)) {
            return viewerUnsupported('Volume exceeds the capability-derived simultaneous visibility budget', 'volume-ccp4-v1');
        }
        const token = ++this.operationToken;
        this.abortController?.abort();
        const abortController = new AbortController();
        this.abortController = abortController;
        let loaded: ViewerResult<void>;
        try {
            loaded = await this.adapter.loadVolume(descriptor, abortController.signal);
        } catch (error) {
            loaded = viewerError(error);
        }
        if (loaded.status !== 'ok') return loaded;
        if (this.disposed || token !== this.operationToken || abortController.signal.aborted) {
            await this.adapter.removeVolume(descriptor.volumeId, new AbortController().signal);
            return viewerCancelled('Volume load was superseded or disposed');
        }
        let presented: ViewerResult<void>;
        try {
            presented = await this.adapter.setVolumePresentation(presentation, abortController.signal);
        } catch (error) {
            presented = viewerError(error);
        }
        if (presented.status !== 'ok') {
            await this.adapter.removeVolume(descriptor.volumeId, new AbortController().signal);
            return presented;
        }
        this.loadedVolumes.set(descriptor.volumeId, descriptor);
        this.volumePresentations.set(descriptor.volumeId, presentation);
        this.emit('volume-loaded', this.scene, { volumeId: descriptor.volumeId }, 'volume');
        return viewerOk(undefined);
    }

    async setVolumePresentation(state: VolumePresentationStateV1): Promise<ViewerResult<void>> {
        if (!this.scene || !this.adapter.setVolumePresentation) {
            return viewerUnsupported('Governed volume presentation is unavailable', 'volume-ccp4-v1');
        }
        const descriptor = this.loadedVolumes.get(state.volumeId);
        if (!descriptor) return viewerUnsupported(`Volume ${state.volumeId} is not loaded`, 'volume-ccp4-v1');
        const validated = validateVolumePresentationState(state, descriptor);
        if (validated.status !== 'ok') return validated as ViewerResult<never>;
        const budget = volumeRuntimeBudget();
        const otherVisible = [...this.volumePresentations.values()].filter((value) => value.visible && value.volumeId !== state.volumeId).length;
        if (state.visible && otherVisible >= Math.min(budget.visible, VOLUME_HARD_LIMITS.maxVisibleVolumes)) {
            return viewerUnsupported('Volume exceeds the capability-derived simultaneous visibility budget', 'volume-ccp4-v1');
        }
        const abortController = new AbortController();
        this.abortController?.abort();
        this.abortController = abortController;
        try {
            const result = await this.adapter.setVolumePresentation(state, abortController.signal);
            if (result.status === 'ok') {
                this.volumePresentations.set(state.volumeId, state);
                this.emit('volume-presentation-changed', this.scene, state, 'volume');
            }
            return result;
        } catch (error) {
            return viewerError(error);
        }
    }

    async removeVolume(volumeId: string): Promise<ViewerResult<void>> {
        if (!this.scene || !this.adapter.removeVolume) {
            return viewerUnsupported('Governed volume removal is unavailable', 'volume-ccp4-v1');
        }
        const abortController = new AbortController();
        this.abortController?.abort();
        this.abortController = abortController;
        try {
            const result = await this.adapter.removeVolume(volumeId, abortController.signal);
            if (result.status === 'ok') {
                this.loadedVolumes.delete(volumeId);
                this.volumePresentations.delete(volumeId);
                this.appliedRegistrations.delete(volumeId);
                this.appliedSegmentations.delete(volumeId);
                this.emit('volume-removed', this.scene, { volumeId }, 'volume');
            }
            return result;
        } catch (error) {
            return viewerError(error);
        }
    }

    async applyVolumeRegistration(registration: VolumeRegistrationV1): Promise<ViewerResult<void>> {
        const volume = this.loadedVolumes.get(registration.volumeId);
        if (!this.scene || !volume || !this.adapter.applyVolumeRegistration) {
            return viewerUnsupported('Governed supplied volume registration is unavailable', 'volume-segmentation-v1');
        }
        const bindings: ViewerSnapshotBindingV2[] = this.scene.documents.flatMap((document) => document.contentSha256 ? [{
            kind: 'document' as const,
            resourceId: document.documentId,
            sha256: document.contentSha256.toLowerCase(),
            required: true,
        }] : []);
        const validated = validateVolumeRegistration(registration, volume, bindings);
        if (validated.status !== 'ok') return validated as ViewerResult<never>;
        const abortController = new AbortController();
        this.abortController?.abort();
        this.abortController = abortController;
        try {
            const result = await this.adapter.applyVolumeRegistration(registration, abortController.signal);
            if (result.status === 'ok') this.appliedRegistrations.set(registration.volumeId, registration);
            return result;
        } catch (error) {
            return viewerError(error);
        }
    }

    async applyVolumeSegmentation(segmentation: VolumeSegmentationV1): Promise<ViewerResult<void>> {
        const volume = this.loadedVolumes.get(segmentation.volumeId);
        if (!this.scene || !volume || !this.adapter.applyVolumeSegmentation) {
            return viewerUnsupported('Governed supplied segmentation is unavailable', 'volume-segmentation-v1');
        }
        const validated = validateVolumeSegmentation(segmentation, volume);
        if (validated.status !== 'ok') return validated as ViewerResult<never>;
        const abortController = new AbortController();
        this.abortController?.abort();
        this.abortController = abortController;
        try {
            const result = await this.adapter.applyVolumeSegmentation(segmentation, abortController.signal);
            if (result.status === 'ok') this.appliedSegmentations.set(segmentation.volumeId, segmentation);
            return result;
        } catch (error) {
            return viewerError(error);
        }
    }

    async capturePng(): Promise<ViewerResult<Blob>> {
        if (!this.scene || !this.adapter.capturePng) {
            return viewerUnsupported('Governed PNG capture is unavailable', 'export-png-v1');
        }
        const abortController = new AbortController();
        this.abortController?.abort();
        this.abortController = abortController;
        try {
            return await this.adapter.capturePng(abortController.signal);
        } catch (error) {
            return viewerError(error);
        }
    }

    async exportSelectionMmcif(): Promise<ViewerResult<Blob>> {
        if (!this.scene || !this.adapter.exportSelectionMmcif) {
            return viewerUnsupported('Governed selected-structure mmCIF export is unavailable', 'export-mmcif-v1');
        }
        const abortController = new AbortController();
        this.abortController?.abort();
        this.abortController = abortController;
        try {
            return await this.adapter.exportSelectionMmcif(abortController.signal);
        } catch (error) {
            return viewerError(error);
        }
    }

    async exportWebM(
        stepper: AuthoritativeFrameStepper,
        request: MovieExportRequestV1,
        onProgress?: (completedFrames: number) => void,
    ): Promise<ViewerResult<GovernedWebMResult>> {
        if (!this.scene || !this.adapter.getCanvasElement) {
            return viewerUnsupported('Governed WebM export is unavailable', 'export-webm-v1');
        }
        const canvas = this.adapter.getCanvasElement();
        if (canvas.status !== 'ok') return canvas as ViewerResult<never>;
        const token = ++this.exportToken;
        this.exportAbortController?.abort();
        const abortController = new AbortController();
        this.exportAbortController = abortController;
        const result = await encodeGovernedWebM(canvas.value, stepper, request, abortController.signal, onProgress);
        return this.disposed || token !== this.exportToken || abortController.signal.aborted
            ? viewerCancelled('WebM export was superseded, cancelled, or disposed')
            : result;
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
        this.exportToken += 1;
        this.exportAbortController?.abort();
        this.exportAbortController = undefined;
        this.loadedVolumes.clear();
        this.volumePresentations.clear();
        this.appliedRegistrations.clear();
        this.appliedSegmentations.clear();
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

    private emit(type: ViewerEvent['type'], state: StructureSceneState, payload: unknown, origin: ViewerEventOrigin = 'controller', documentId: string | null = state.activeDocumentId, resourceId: string | null = null): void {
        const event = createViewerEvent({
            type,
            scene: state.ref,
            documentId,
            resourceId,
            origin,
            payload,
            emittedAt: new Date().toISOString(),
        });
        for (const listener of this.listeners) listener(event);
    }
}
