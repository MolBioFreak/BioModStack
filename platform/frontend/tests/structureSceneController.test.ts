import assert from 'node:assert/strict';
import test from 'node:test';

import type { StructureSceneState } from '../src/structureViewer/contracts/sceneState.js';
import type { StructureScenePresentation } from '../src/structureViewer/contracts/scenePresentation.js';
import { viewerOk, type ViewerResult } from '../src/structureViewer/contracts/viewerResults.js';
import {
    StructureSceneController,
    type StructureSceneEngineAdapter,
} from '../src/structureViewer/runtime/StructureSceneController.js';

const deferred = <T>() => {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => { resolve = done; });
    return { promise, resolve };
};

const scene = (sceneId: string, generation: number): StructureSceneState => ({
    schemaVersion: 1,
    ref: { viewerId: 'viewer-1', sceneId, generation },
    documents: [{ documentId: `${sceneId}-doc`, sourceKind: 'pdb' }],
    activeDocumentId: `${sceneId}-doc`,
    provenance: { createdBy: 'test', createdAt: '2026-07-18T00:00:00.000Z' },
});

const diagnostics = () => ({
    engineName: 'molstar' as const,
    engineVersion: '4.5.0',
    wrapper: 'bms-direct' as const,
    disposed: false,
    structureCount: 0,
    completedSceneGeneration: 0,
    measurementCount: 0,
    hasCanvas3d: false,
});

const clickSubscription = () => () => undefined;

test('stale scene completion cannot commit over a newer generation', async () => {
    const first = deferred<ViewerResult<void>>();
    const second = deferred<ViewerResult<void>>();
    let calls = 0;
    const reconcileScene = async () => (++calls === 1 ? first.promise : second.promise);
    const adapter: StructureSceneEngineAdapter = {
        loadScene: reconcileScene,
        reconcileScene,
        subscribeResidueClicks: clickSubscription,
        diagnostics,
        dispose: async () => undefined,
    };
    const controller = new StructureSceneController(adapter);
    const events: string[] = [];
    controller.subscribe((event) => events.push(`${event.type}:${event.sceneId}:${event.generation}`));

    const a = controller.loadScene(scene('A', 1));
    const b = controller.loadScene(scene('B', 2));
    second.resolve(viewerOk(undefined));
    assert.equal((await b).status, 'ok');
    first.resolve(viewerOk(undefined));
    assert.equal((await a).status, 'cancelled');
    assert.equal(controller.currentScene?.ref.sceneId, 'B');
    assert.deepEqual(events, [
        'scene-loading:A:1',
        'scene-loading:B:2',
        'scene-ready:B:2',
    ]);
});

test('dispose cancels in-flight work and emits one scoped terminal event', async () => {
    const pending = deferred<ViewerResult<void>>();
    let disposeCalls = 0;
    const reconcileScene = async () => pending.promise;
    const adapter: StructureSceneEngineAdapter = {
        loadScene: reconcileScene,
        reconcileScene,
        subscribeResidueClicks: clickSubscription,
        diagnostics,
        dispose: async () => { disposeCalls += 1; },
    };
    const controller = new StructureSceneController(adapter);
    const events: Array<{ type: string; sceneId: string; documentId: string }> = [];
    controller.subscribe((event) => events.push({
        type: event.type,
        sceneId: event.sceneId,
        documentId: event.documentId,
    }));

    const loading = controller.loadScene(scene('A', 4));
    await controller.dispose();
    await controller.dispose();
    pending.resolve(viewerOk(undefined));

    assert.equal((await loading).status, 'cancelled');
    assert.equal(disposeCalls, 1);
    assert.deepEqual(events.at(-1), { type: 'disposed', sceneId: 'A', documentId: 'A-doc' });
});

test('presentation capture combines live engine state with controller-owned analytical layers', async () => {
    const live: StructureScenePresentation = {
        camera: { mode: 'orthographic', target: [1, 2, 3], position: [4, 5, 6], up: [0, 1, 0], radius: 9 },
        representations: [{
            representationId: 'A-doc:polymer:cartoon:0', documentId: 'A-doc', kind: 'cartoon', visible: false, opacity: 0.4,
        }],
    };
    const initial = {
        ...scene('A', 1),
        presentation: {
            layers: [{ layerId: 'metric:native', metricId: 'native', visible: true, opacity: 0.7, order: 0 }],
        },
    } satisfies StructureSceneState;
    const reconcileScene = async () => viewerOk(undefined);
    const adapter: StructureSceneEngineAdapter = {
        loadScene: reconcileScene,
        reconcileScene,
        subscribeResidueClicks: clickSubscription,
        diagnostics,
        capturePresentation: () => viewerOk(live),
        dispose: async () => undefined,
    };
    const controller = new StructureSceneController(adapter);
    assert.equal((await controller.loadScene(initial)).status, 'ok');

    assert.deepEqual(controller.capturePresentation(), {
        status: 'ok',
        value: { ...live, layers: initial.presentation.layers },
    });
});
