import assert from 'node:assert/strict';
import test from 'node:test';

import type { StructureSceneState } from '../src/structureViewer/contracts/sceneState.js';
import { documentsForDirectMolstar } from '../src/structureViewer/runtime/directSceneDocuments.js';

const state = (sourceKind: StructureSceneState['documents'][number]['sourceKind'], sourceUrl = '/structure'): StructureSceneState => ({
    schemaVersion: 1,
    ref: { viewerId: 'v', sceneId: 's', generation: 1 },
    documents: [{ documentId: 'doc', sourceKind, sourceUrl }],
    activeDocumentId: 'doc',
    provenance: { createdBy: 'test', createdAt: '2026-07-18T00:00:00.000Z' },
});

test('direct scene bridge maps governed coordinate formats exactly', () => {
    assert.deepEqual(documentsForDirectMolstar(state('pdb')), {
        status: 'ok', value: [{ id: 'doc', url: '/structure', format: 'pdb' }],
    });
    assert.deepEqual(documentsForDirectMolstar(state('mmcif')), {
        status: 'ok', value: [{ id: 'doc', url: '/structure', format: 'mmcif' }],
    });
    assert.deepEqual(documentsForDirectMolstar(state('bcif')), {
        status: 'ok', value: [{ id: 'doc', url: '/structure', format: 'mmcif', isBinary: true }],
    });
    assert.deepEqual(documentsForDirectMolstar(state('sdf')), {
        status: 'ok', value: [{ id: 'doc', url: '/structure', format: 'sdf' }],
    });
});

test('trajectory and volume documents fail closed until extensions are integrated', () => {
    for (const kind of ['trajectory', 'volume', 'mol2'] as const) {
        const result = documentsForDirectMolstar(state(kind));
        assert.equal(result.status, 'unsupported');
        if (result.status === 'unsupported') assert.match(result.reason, new RegExp(kind));
    }
});

test('transport URL is required but never substituted for document identity', () => {
    const input = state('pdb', '');
    const result = documentsForDirectMolstar(input);
    assert.equal(result.status, 'unsupported');
    assert.equal(input.documents[0].documentId, 'doc');
});
