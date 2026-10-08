import {
    MolstarDirectAdapter,
    MolstarDirectAdapterCancelledError,
    type MolstarDirectPresentation,
    type MolstarDirectQuery,
} from '../adapters/MolstarDirectAdapter';
import type { StructureComponentType, StructurePresentationQuery } from '../contracts/scenePresentation.js';
import type { StructureSceneState } from '../contracts/sceneState.js';
import type { MDPlaybackState, MDSourceFrameRef } from '../contracts/mdTrajectory.js';
import type { SpatialVolumeDescriptorV1, VolumePresentationStateV1, VolumeRegistrationV1, VolumeSegmentationV1 } from '../contracts/spatialVolumes.js';
import {
    viewerCancelled,
    viewerError,
    viewerOk,
    viewerUnsupported,
    type ViewerResult,
} from '../contracts/viewerResults.js';
import type { EngineResidueClick, MolstarEngineAdapter, MolstarEngineDiagnostics } from './MolstarEngineAdapter.js';
import { reconcileSceneState } from './sceneReconciler.js';
import { documentsForDirectMolstar } from './directSceneDocuments.js';

const toDirectQuery = (query: StructurePresentationQuery): MolstarDirectQuery => ({
    document_id: query.documentId,
    entity_id: query.entityId,
    struct_asym_id: query.labelAsymId,
    auth_asym_id: query.authAsymId,
    start_residue_number: query.startLabelSeqId,
    end_residue_number: query.endLabelSeqId,
    start_auth_residue_number: query.startAuthSeqId,
    end_auth_residue_number: query.endAuthSeqId,
    auth_ins_code_id: query.insertionCode ?? undefined,
    atoms: query.labelAtomIds,
    auth_atoms: query.authAtomIds,
    alt_loc_id: query.altLoc,
    component_types: query.componentTypes,
    color: query.color ?? undefined,
    focus: query.focus,
    tooltip: query.tooltip,
    opacity: query.opacity,
});

const STRUCTURE_COMPONENT_TYPES: readonly StructureComponentType[] = ['protein', 'dna', 'rna', 'ligand', 'glycan', 'ion', 'water', 'unknown'];

const toDirectPresentation = (state: StructureSceneState): MolstarDirectPresentation => {
    const visibleTypes = state.presentation?.filters?.entityTypes;
    const visible = new Set(visibleTypes ?? STRUCTURE_COMPONENT_TYPES);
    const hiddenTypes = visibleTypes === undefined
        ? []
        : STRUCTURE_COMPONENT_TYPES.filter((componentType) => !visible.has(componentType));
    return {
        colorSelections: state.presentation?.colorQueries?.map(toDirectQuery) ?? [],
        tooltipSelections: state.presentation?.tooltipQueries?.map(toDirectQuery) ?? [],
        hiddenSelections: [
            ...(state.presentation?.hiddenQueries?.map(toDirectQuery) ?? []),
            ...(hiddenTypes.length > 0 ? [{ component_types: hiddenTypes }] : []),
        ],
        nonSelectedColor: state.presentation?.nonSelectedColor,
    };
};

export class MolstarDirectSceneEngineAdapter implements MolstarEngineAdapter {
    private readonly adapter: MolstarDirectAdapter;

    constructor(adapter: MolstarDirectAdapter) {
        this.adapter = adapter;
    }

    subscribeResidueClicks(handler: (click: EngineResidueClick) => void): () => void {
        let active = true;
        this.adapter.setResidueClickHandler((click) => {
            if (!active) return;
            handler({
                engineGeneration: click.sceneGeneration,
                residue: {
                    documentId: click.documentId,
                    labelAsymId: click.labelAsymId,
                    authAsymId: click.authAsymId,
                    labelSeqId: click.labelSeqId,
                    authSeqId: click.authSeqId,
                    insertionCode: click.insertionCode,
                },
            });
        });
        return () => {
            active = false;
            this.adapter.setResidueClickHandler(undefined);
        };
    }

    diagnostics(): MolstarEngineDiagnostics {
        const value = this.adapter.diagnostics;
        return {
            engineName: 'molstar', engineVersion: '4.5.0', wrapper: 'bms-direct',
            disposed: value.disposed, structureCount: value.structureCount,
            completedSceneGeneration: value.completedSceneGeneration,
            measurementCount: value.measurementCount, hasCanvas3d: value.hasCanvas3d,
        };
    }

    async loadScene(state: StructureSceneState, signal: AbortSignal): Promise<ViewerResult<void>> {
        if (signal.aborted) return viewerCancelled('Scene load was cancelled before translation');
        const documents = documentsForDirectMolstar(state);
        if (documents.status !== 'ok') return documents;
        try {
            await this.adapter.loadScene(documents.value);
            if (signal.aborted) return viewerCancelled('Scene load was cancelled after engine reconciliation');
            return viewerOk(undefined);
        } catch (error) {
            if (signal.aborted || error instanceof MolstarDirectAdapterCancelledError) {
                return viewerCancelled('Scene load was superseded or cancelled');
            }
            return viewerError(error);
        }
    }

    async reconcileScene(
        previous: StructureSceneState | undefined,
        next: StructureSceneState,
        signal: AbortSignal,
    ): Promise<ViewerResult<void>> {
        const reconciliation = reconcileSceneState(previous, next);
        const documentsReloaded = reconciliation.documentsChanged || reconciliation.activeDocumentChanged || reconciliation.collectionChanged;
        if (documentsReloaded) {
            const loaded = await this.loadScene(next, signal);
            if (loaded.status !== 'ok') return loaded;
        }
        if (signal.aborted) return viewerCancelled('Scene reconciliation was cancelled');
        if (documentsReloaded || reconciliation.presentationChanged || reconciliation.layerChanged
            || reconciliation.selectionChanged || reconciliation.filterChanged) {
            try {
                await this.adapter.applyPresentation(toDirectPresentation(next));
            } catch (error) {
                if (signal.aborted || error instanceof MolstarDirectAdapterCancelledError) {
                    return viewerCancelled('Presentation reconciliation was superseded or cancelled');
                }
                return viewerError(error);
            }
        }
        if (signal.aborted) return viewerCancelled('Scene reconciliation was cancelled after presentation');
        if (reconciliation.cameraChanged && next.presentation?.camera) {
            const camera = this.adapter.applyCamera(next.presentation.camera);
            if (camera.status !== 'ok') return camera;
        }
        if (documentsReloaded || reconciliation.measurementChanged) {
            const measurements = await this.adapter.setMeasurements(next.presentation?.measurements ?? []);
            if (measurements.status !== 'ok') return measurements;
        }
        return viewerOk(undefined);
    }

    async dispose(): Promise<void> {
        this.adapter.dispose();
    }

    async loadVolume(descriptor: SpatialVolumeDescriptorV1, signal: AbortSignal): Promise<ViewerResult<void>> {
        return this.adapter.loadVolume(descriptor, signal);
    }

    async setVolumePresentation(state: VolumePresentationStateV1, signal: AbortSignal): Promise<ViewerResult<void>> {
        return this.adapter.setVolumePresentation(state, signal);
    }

    async removeVolume(volumeId: string, signal: AbortSignal): Promise<ViewerResult<void>> {
        return this.adapter.removeVolume(volumeId, signal);
    }

    async applyVolumeRegistration(registration: VolumeRegistrationV1, signal: AbortSignal): Promise<ViewerResult<void>> {
        return this.adapter.applyVolumeRegistration(registration, signal);
    }

    async applyVolumeSegmentation(segmentation: VolumeSegmentationV1, signal: AbortSignal): Promise<ViewerResult<void>> {
        return this.adapter.applyVolumeSegmentation(segmentation, signal);
    }

    async capturePng(signal: AbortSignal): Promise<ViewerResult<Blob>> {
        return this.adapter.capturePng(signal);
    }

    async exportSelectionMmcif(signal: AbortSignal): Promise<ViewerResult<Blob>> {
        return this.adapter.exportSelectionMmcif(signal);
    }

    getCanvasElement(): ViewerResult<HTMLCanvasElement> {
        return this.adapter.getCanvasElement();
    }

    async selectMDSourceFrame(_frame: MDSourceFrameRef, signal: AbortSignal): Promise<ViewerResult<void>> {
        if (signal.aborted) return viewerCancelled('MD frame selection was cancelled');
        return viewerUnsupported('Molstar 4.5 XTC/DCD playback is not enabled without exercised format proof', 'trajectories');
    }

    async setMDPlayback(_playback: MDPlaybackState, signal: AbortSignal): Promise<ViewerResult<void>> {
        if (signal.aborted) return viewerCancelled('MD playback update was cancelled');
        return viewerUnsupported('Molstar 4.5 XTC/DCD playback is not enabled without exercised format proof', 'trajectories');
    }
}
