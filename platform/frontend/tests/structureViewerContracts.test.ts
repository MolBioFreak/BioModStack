import assert from 'node:assert/strict';
import test from 'node:test';

import {
    assessResidueRef,
    canonicalResidueRefKey,
    type ResidueRef,
} from '../src/structureViewer/contracts/structureIdentity.js';
import {
    createStructureSceneState,
    createViewerSnapshot,
    restoreViewerSnapshot,
} from '../src/structureViewer/contracts/sceneState.js';
import { createViewerEvent } from '../src/structureViewer/contracts/viewerEvents.js';

test('canonical residue identity retains advanced identity fields without broadening', () => {
    const residue: ResidueRef = {
        documentId: 'doc-1',
        modelId: 'model-2',
        entityId: 'entity-1',
        labelAsymId: 'AA',
        authAsymId: 'H',
        labelSeqId: 42,
        authSeqId: -7,
        insertionCode: 'B',
        componentId: 'TYR',
        altLoc: 'A',
        assemblyId: '1',
        operatorInstanceId: '1_555',
        sourceInstanceId: 'heavy-copy-2',
    };

    const result = assessResidueRef(residue);
    assert.equal(result.status, 'ok');
    if (result.status !== 'ok') return;
    assert.deepEqual(result.value, residue);
    assert.match(canonicalResidueRefKey(residue), /label_asym=AA/);
    assert.match(canonicalResidueRefKey(residue), /auth_seq=-7/);
    assert.match(canonicalResidueRefKey(residue), /operator=1_555/);
    assert.match(canonicalResidueRefKey(residue), /altloc=A/);
});

test('incomplete mixed residue namespaces fail closed', () => {
    const result = assessResidueRef({
        documentId: 'doc-1',
        labelAsymId: 'A',
        authSeqId: 42,
    });
    assert.equal(result.status, 'ambiguous');
    if (result.status === 'ambiguous') {
        assert.match(result.reason, /complete label or author namespace/i);
    }
});

test('scene snapshots round-trip and reject stale source hashes', () => {
    const scene = createStructureSceneState({
        ref: { viewerId: 'viewer-1', sceneId: 'scene-1', generation: 3 },
        documents: [{
            documentId: 'doc-1',
            sourceKind: 'pdb',
            contentSha256: 'a'.repeat(64),
            sourceUrl: '/api/designs/1/pdb',
            provenanceRef: 'prov-1',
        }],
        collection: { kind: 'independent_hypotheses', orderedDocumentIds: ['doc-1'] },
        activeDocumentId: 'doc-1',
        provenance: { createdBy: 'test', createdAt: '2026-07-18T00:00:00.000Z' },
    });
    assert.equal(scene.status, 'ok');
    if (scene.status !== 'ok') return;

    const snapshot = createViewerSnapshot(scene.value, {
        adapterVersion: 'direct-molstar-4.5.0',
        capturedAt: '2026-07-18T00:01:00.000Z',
    });
    const decoded = JSON.parse(JSON.stringify(snapshot));
    assert.deepEqual(decoded, snapshot);

    const restored = restoreViewerSnapshot(decoded, [{
        documentId: 'doc-1',
        contentSha256: 'a'.repeat(64),
    }]);
    assert.equal(restored.status, 'ok');

    const stale = restoreViewerSnapshot(decoded, [{
        documentId: 'doc-1',
        contentSha256: 'b'.repeat(64),
    }]);
    assert.equal(stale.status, 'unsupported');
    if (stale.status === 'unsupported') assert.match(stale.reason, /hash mismatch/i);
});

test('viewer events are fully scoped and retain origin provenance', () => {
    const event = createViewerEvent({
        type: 'selection-changed',
        scene: { viewerId: 'viewer-a', sceneId: 'scene-a', generation: 9 },
        documentId: 'doc-a',
        origin: 'canvas',
        payload: { selectionId: 'sel-1' },
        emittedAt: '2026-07-18T00:02:00.000Z',
    });
    assert.deepEqual(event, {
        type: 'selection-changed',
        viewerId: 'viewer-a',
        sceneId: 'scene-a',
        generation: 9,
        documentId: 'doc-a',
        resourceId: null,
        origin: 'canvas',
        payload: { selectionId: 'sel-1' },
        emittedAt: '2026-07-18T00:02:00.000Z',
    });
});

test('multi-document scenes require explicit collection semantics and ordering', () => {
    const result = createStructureSceneState({
        ref: { viewerId: 'viewer-1', sceneId: 'scene-1', generation: 1 },
        documents: [
            { documentId: 'a', sourceKind: 'pdb' },
            { documentId: 'b', sourceKind: 'pdb' },
        ],
        activeDocumentId: 'a',
        provenance: { createdBy: 'test', createdAt: '2026-07-18T00:00:00.000Z' },
    });
    assert.equal(result.status, 'unsupported');
    if (result.status === 'unsupported') assert.match(result.reason, /collection kind/i);
});
