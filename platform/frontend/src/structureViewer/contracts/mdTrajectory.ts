import { viewerOk, viewerUnsupported, type ViewerResult } from './viewerResults.js';

export interface MDTrajectoryArtifactRef {
    readonly replica: number;
    readonly topologyArtifactId: string;
    readonly trajectoryArtifactId: string;
    readonly atomOrderIdentity: string;
    readonly topologySha256: string;
    readonly trajectorySha256: string;
    readonly trajectoryFormat: 'xtc' | 'dcd';
}

export interface MDSourceFrameRef {
    readonly replica: number;
    /** Index into the bounded, immutable trajectory-frame map; this is the Mol* XTC model index. */
    readonly displayFrame: number;
    /** Original producer frame; shown to the operator and never used as a local decoder index. */
    readonly sourceFrame: number;
    readonly timePs: number;
    readonly step: number;
}

export interface MDPlaybackState {
    readonly state: 'stopped' | 'playing' | 'paused' | 'unsupported';
    readonly selectedFrame?: MDSourceFrameRef;
    readonly framesPerSecond: number;
}

export interface MDSceneState {
    readonly activeReplica: number;
    readonly replicas: readonly MDTrajectoryArtifactRef[];
    readonly playbackCapability: {
        readonly supported: boolean;
        readonly reason?: string;
    };
    readonly playback: MDPlaybackState;
}

const SHA256 = /^[0-9a-f]{64}$/;

export const validateMDSceneState = (state: MDSceneState): ViewerResult<MDSceneState> => {
    if (state.replicas.length === 0 || new Set(state.replicas.map((item) => item.replica)).size !== state.replicas.length) {
        return viewerUnsupported('MD scene replicas must be non-empty and uniquely identified', 'trajectories');
    }
    if (!state.replicas.some((item) => item.replica === state.activeReplica)) {
        return viewerUnsupported('MD active replica is absent from governed metadata', 'trajectories');
    }
    if (state.replicas.some((item) => !item.atomOrderIdentity || !SHA256.test(item.topologySha256) || !SHA256.test(item.trajectorySha256))) {
        return viewerUnsupported('MD trajectory metadata requires atom-order identity and artifact hashes', 'trajectories');
    }
    if (!state.playbackCapability.supported && state.playback.state !== 'unsupported') {
        return viewerUnsupported('Unsupported MD playback must remain explicitly unsupported', 'trajectories');
    }
    if (state.playback.selectedFrame && state.playback.selectedFrame.replica !== state.activeReplica) {
        return viewerUnsupported('Selected MD source frame must belong to the active replica', 'trajectories');
    }
    return viewerOk(state);
};
