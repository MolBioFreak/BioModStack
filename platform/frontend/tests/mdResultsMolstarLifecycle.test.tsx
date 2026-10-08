import assert from 'node:assert/strict';
import test from 'node:test';
import { viewerOk } from '../src/structureViewer/contracts/viewerResults.js';
import { StructureSceneController } from '../src/structureViewer/runtime/StructureSceneController.js';

const scene = (generation: number, replica: number) => ({
    schemaVersion: 1 as const,
    ref: { viewerId: 'md-owner', sceneId: 'md-results', generation },
    documents: [{ documentId: `replica-${replica}`, sourceKind: 'pdb' as const }],
    activeDocumentId: `replica-${replica}`,
    provenance: { createdBy: 'md-results-lifecycle-test', createdAt: '2026-07-21T00:00:00Z' },
    molecularDynamics: {
        activeReplica: replica,
        replicas: [{
            replica,
            topologyArtifactId: `top-${replica}`,
            trajectoryArtifactId: `traj-${replica}`,
            atomOrderIdentity: `order-${replica}`,
            topologySha256: 'a'.repeat(64),
            trajectorySha256: 'b'.repeat(64),
            trajectoryFormat: 'xtc' as const,
        }],
        playbackCapability: { supported: false as const, reason: 'binary playback is not qualified' },
        playback: { state: 'unsupported' as const, framesPerSecond: 0 },
    },
});

test('MD replica switching and StrictMode-style remount retain one disposed owner chain', async () => {
    let liveOwners = 0;
    let maximumLiveOwners = 0;
    let reconciliations = 0;
    const makeController = () => {
        liveOwners += 1;
        maximumLiveOwners = Math.max(maximumLiveOwners, liveOwners);
        let disposed = false;
        return new StructureSceneController({
            loadScene: async () => viewerOk(undefined),
            reconcileScene: async () => { reconciliations += 1; return viewerOk(undefined); },
            subscribeResidueClicks: () => () => undefined,
            diagnostics: () => ({
                engineName: 'molstar', engineVersion: '4.5.0', wrapper: 'bms-direct', disposed,
                structureCount: 1, completedSceneGeneration: reconciliations, measurementCount: 0, hasCanvas3d: true,
            }),
            selectMDSourceFrame: async () => viewerOk(undefined),
            dispose: async () => { if (!disposed) { disposed = true; liveOwners -= 1; } },
        });
    };

    const first = makeController();
    assert.equal((await first.loadScene(scene(1, 0))).status, 'ok');
    assert.equal((await first.reconcileScene(scene(2, 1))).status, 'ok');
    await first.dispose();
    assert.equal(liveOwners, 0);

    const remount = makeController();
    assert.equal((await remount.loadScene(scene(3, 0))).status, 'ok');
    await remount.dispose();
    await remount.dispose();

    assert.equal(maximumLiveOwners, 1);
    assert.equal(liveOwners, 0);
    assert.equal(reconciliations, 3);
});
