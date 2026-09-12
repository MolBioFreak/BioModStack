import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { parseSdf } from 'molstar/lib/commonjs/mol-io/reader/sdf/parser.js';

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

test('the installed SDF decoder reads native ligand atoms instead of an empty PDB', async () => {
    const sdf = 'complex-b\nBMS test fixture\n\n  1  0  0  0  0  0            999 V2000\n    1.2500    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n$$$$\n';
    const result = await parseSdf(sdf).run();
    assert.equal(result.isError, false);
    assert.equal(result.result.compounds.length, 1);
    const ligand = result.result.compounds[0].molFile;
    assert.equal(ligand.title, 'complex-b');
    assert.equal(ligand.atoms.count, 1);
    assert.equal(ligand.atoms.type_symbol.value(0), 'C');
    assert.equal(ligand.atoms.x.value(0), 1.25);
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

test('direct bridge delegates MD loading and frame selection by bounded display index', () => {
    const source = readFileSync('src/structureViewer/runtime/MolstarDirectSceneEngineAdapter.ts', 'utf8');

    assert.match(source, /this\.adapter\.loadMolecularDynamics\(state\.molecularDynamics\)/);
    assert.match(source, /this\.adapter\.selectMolecularDynamicsDisplayFrame\(frame\.displayFrame\)/);
    assert.doesNotMatch(source, /selectMolecularDynamicsDisplayFrame\(frame\.sourceFrame\)/);
});

test('MD-only scene reconciliation loads an unselected governed trajectory before frame selection', () => {
    const source = readFileSync('src/structureViewer/runtime/MolstarDirectSceneEngineAdapter.ts', 'utf8');

    assert.match(source, /reconciliation\.molecularDynamicsChanged[\s\S]*?await this\.adapter\.loadMolecularDynamics\(next\.molecularDynamics\)/);
    assert.match(source, /loadMolecularDynamics\(next\.molecularDynamics\)[\s\S]*?selectedFrame/);
});

test('direct adapter uses Molstar 4.5 GRO/XTC state transforms and bounded display indices', () => {
    const source = readFileSync('src/structureViewer/adapters/MolstarDirectAdapter.ts', 'utf8');

    assert.match(source, /parseTrajectory\(topologyData, 'gro'\)/);
    assert.match(source, /StateTransforms\.Model\.CoordinatesFromXtc/);
    assert.match(source, /StateTransforms\.Model\.TrajectoryFromModelAndCoordinates/);
    assert.match(source, /modelIndex:\s*displayFrame/);
    assert.doesNotMatch(source, /modelIndex:\s*.*sourceFrame/);
});

test('direct presentation forwards representations and applies analytical layer visibility and opacity', () => {
    const source = readFileSync('src/structureViewer/runtime/MolstarDirectSceneEngineAdapter.ts', 'utf8');
    assert.match(source, /representations:\s*state\.presentation\?\.representations/);
    assert.match(source, /activeLayer\?\.visible/);
    assert.match(source, /opacity:\s*activeLayer\.opacity/);
    assert.match(source, /this\.adapter\.capturePresentation\(\)/);
});
