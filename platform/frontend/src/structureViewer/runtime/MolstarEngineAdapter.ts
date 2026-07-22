import type { ResidueRef } from '../contracts/structureIdentity.js';
import type { StructureSceneState } from '../contracts/sceneState.js';
import type { MDPlaybackState, MDSourceFrameRef } from '../contracts/mdTrajectory.js';
import type { ViewerResult } from '../contracts/viewerResults.js';
import type { SpatialVolumeDescriptorV1, VolumePresentationStateV1, VolumeRegistrationV1, VolumeSegmentationV1 } from '../contracts/spatialVolumes.js';

export interface EngineResidueClick {
    readonly residue: ResidueRef;
    readonly engineGeneration: number;
}

export interface MolstarEngineDiagnostics {
    readonly engineName: 'molstar';
    readonly engineVersion: string;
    readonly wrapper: 'bms-direct';
    readonly disposed: boolean;
    readonly structureCount: number;
    readonly completedSceneGeneration: number;
    readonly measurementCount: number;
    readonly hasCanvas3d: boolean;
}

/** The sole public boundary between BMS scene orchestration and the Mol* runtime. */
export interface MolstarEngineAdapter {
    loadScene(state: StructureSceneState, signal: AbortSignal): Promise<ViewerResult<void>>;
    reconcileScene(previous: StructureSceneState | undefined, next: StructureSceneState, signal: AbortSignal): Promise<ViewerResult<void>>;
    subscribeResidueClicks(handler: (click: EngineResidueClick) => void): () => void;
    diagnostics(): MolstarEngineDiagnostics;
    selectMDSourceFrame?(frame: MDSourceFrameRef, signal: AbortSignal): Promise<ViewerResult<void>>;
    setMDPlayback?(playback: MDPlaybackState, signal: AbortSignal): Promise<ViewerResult<void>>;
    loadVolume?(descriptor: SpatialVolumeDescriptorV1, signal: AbortSignal): Promise<ViewerResult<void>>;
    setVolumePresentation?(state: VolumePresentationStateV1, signal: AbortSignal): Promise<ViewerResult<void>>;
    removeVolume?(volumeId: string, signal: AbortSignal): Promise<ViewerResult<void>>;
    applyVolumeRegistration?(registration: VolumeRegistrationV1, signal: AbortSignal): Promise<ViewerResult<void>>;
    applyVolumeSegmentation?(segmentation: VolumeSegmentationV1, signal: AbortSignal): Promise<ViewerResult<void>>;
    capturePng?(signal: AbortSignal): Promise<ViewerResult<Blob>>;
    exportSelectionMmcif?(signal: AbortSignal): Promise<ViewerResult<Blob>>;
    getCanvasElement?(): ViewerResult<HTMLCanvasElement>;
    dispose(): Promise<void>;
}
