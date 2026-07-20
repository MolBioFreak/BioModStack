import type { ResidueRef } from '../contracts/structureIdentity.js';
import type { StructureSceneState } from '../contracts/sceneState.js';
import type { MDPlaybackState, MDSourceFrameRef } from '../contracts/mdTrajectory.js';
import type { ViewerResult } from '../contracts/viewerResults.js';

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
    dispose(): Promise<void>;
}
