import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { validateMDSceneState } from '../src/structureViewer/contracts/mdTrajectory.js';
import { StructureSceneController } from '../src/structureViewer/runtime/StructureSceneController.js';
import { viewerOk } from '../src/structureViewer/contracts/viewerResults.js';

test('MD results route before Design-centric fetching and charts', () => {
    const source = readFileSync(path.resolve(process.cwd(), 'src/components/ResultsViewer.tsx'), 'utf8');
    assert.match(source, /activeJob\.model_id === 'molecular_dynamics' \? \(/);
    assert.match(source, /activeJob\.model_id !== 'molecular_dynamics'/);
    assert.match(source, /<MDResultsPane jobId=\{activeJob\.id\}/);
});

test('governed MD metadata remains fail-closed without binary playback proof', () => {
    const result = validateMDSceneState({
        activeReplica: 0,
        replicas: [{
            replica: 0,
            topologyArtifactId: 'r0.final_coordinates',
            trajectoryArtifactId: 'r0.trajectory',
            atomOrderIdentity: 'atom-order-v1',
            topologySha256: 'a'.repeat(64),
            trajectorySha256: 'b'.repeat(64),
            trajectoryFormat: 'dcd',
        }],
        playbackCapability: { supported: false, reason: 'not exercised' },
        playback: { state: 'unsupported', framesPerSecond: 0 },
    });
    assert.equal(result.status, 'ok');
});

test('stale cross-replica source-frame identity is rejected', () => {
    const result = validateMDSceneState({
        activeReplica: 0,
        replicas: [{ replica: 0, topologyArtifactId: 'top', trajectoryArtifactId: 'traj', atomOrderIdentity: 'order', topologySha256: 'a'.repeat(64), trajectorySha256: 'b'.repeat(64), trajectoryFormat: 'xtc' }],
        playbackCapability: { supported: false, reason: 'not exercised' },
        playback: { state: 'unsupported', framesPerSecond: 0, selectedFrame: { replica: 1, sourceFrame: 4, timePs: 8 } },
    });
    assert.equal(result.status, 'unsupported');
});

test('controller keeps unsupported playback fail-closed without invoking the adapter', async () => {
    let frameCalls = 0;
    const controller = new StructureSceneController({
        loadScene: async () => viewerOk(undefined),
        reconcileScene: async () => viewerOk(undefined),
        subscribeResidueClicks: () => () => undefined,
        diagnostics: () => ({
            engineName: 'molstar', engineVersion: '4.5.0', wrapper: 'bms-direct', disposed: false,
            structureCount: 1, completedSceneGeneration: 1, measurementCount: 0, hasCanvas3d: true,
        }),
        selectMDSourceFrame: async () => { frameCalls += 1; return viewerOk(undefined); },
        dispose: async () => undefined,
    });
    const loaded = await controller.loadScene({
        schemaVersion: 1,
        ref: { viewerId: 'owner', sceneId: 'md', generation: 1 },
        documents: [{ documentId: 'representative', sourceKind: 'pdb' }],
        activeDocumentId: 'representative',
        provenance: { createdBy: 'test', createdAt: '2026-07-19T00:00:00Z' },
        molecularDynamics: {
            activeReplica: 0,
            replicas: [{ replica: 0, topologyArtifactId: 'top', trajectoryArtifactId: 'traj', atomOrderIdentity: 'order', topologySha256: 'a'.repeat(64), trajectorySha256: 'b'.repeat(64), trajectoryFormat: 'dcd' }],
            playbackCapability: { supported: false, reason: 'not exercised' },
            playback: { state: 'unsupported', framesPerSecond: 0 },
        },
    });
    assert.equal(loaded.status, 'ok');
    const selected = await controller.selectMDSourceFrame({ replica: 0, sourceFrame: 2, timePs: 4 });
    assert.equal(selected.status, 'unsupported');
    assert.equal(frameCalls, 0);
    await controller.dispose();
});

test('MD pane consumes server-produced point identity and advertises no recomputation', () => {
    const source = readFileSync(path.resolve(process.cwd(), 'src/components/MDResultsPane.tsx'), 'utf8');
    assert.match(source, /source_frame/);
    assert.match(source, /Server-produced bounded points/);
    assert.doesNotMatch(source, /from ['"]MDAnalysis|superposition|calculateRmsd/i);
    assert.match(source, /playback unavailable/i);
});
