import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
    MOLSTAR_STABLE_33_CAPABILITIES,
    MOLSTAR_STABLE_33_CAPABILITY_IDS,
    assessMolstarStable33Identity,
} from '../src/structureViewer/runtime/molstarStable33Capabilities.js';
import { MOLSTAR_DIRECT_45_CAPABILITIES } from '../src/structureViewer/runtime/molstarDirect45Capabilities.js';
import type {
    ViewerCapabilityId,
    ViewerRuntimeCapabilities,
} from '../src/structureViewer/contracts/viewerCapabilities.js';

const frontendRoot = process.cwd();

const requiredCapabilityIds = [
    'load-completion',
    'load-errors',
    'disconnect-disposal',
    'label-chain-identity',
    'author-chain-identity',
    'label-residue-identity',
    'author-residue-identity',
    'insertion-code-identity',
    'model-identity',
    'alternate-location-identity',
    'operator-instance-identity',
    'repeated-entity-instance-identity',
    'selection',
    'coloring',
    'overlays',
    'overlay-removal',
    'measurements',
    'trajectories',
    'assemblies',
    'symmetry',
    'volumes',
    'snapshots',
    'event-provenance',
] as const satisfies readonly ViewerCapabilityId[];

const compileTimeMatrix: ViewerRuntimeCapabilities = MOLSTAR_STABLE_33_CAPABILITIES;
void compileTimeMatrix;

test('pinned 3.3 capability matrix is complete and version-bound', () => {
    assert.equal(MOLSTAR_STABLE_33_CAPABILITIES.runtime.packageName, 'pdbe-molstar');
    assert.equal(MOLSTAR_STABLE_33_CAPABILITIES.runtime.packageVersion, '3.3.0');
    assert.equal(MOLSTAR_STABLE_33_CAPABILITIES.runtime.engineName, 'molstar');
    assert.equal(MOLSTAR_STABLE_33_CAPABILITIES.runtime.engineVersion, '4.5.0');
    assert.deepEqual(MOLSTAR_STABLE_33_CAPABILITY_IDS, requiredCapabilityIds);
    assert.deepEqual(
        Object.keys(MOLSTAR_STABLE_33_CAPABILITIES.capabilities),
        requiredCapabilityIds,
    );
});

test('direct 4.5 adapter manifest is complete, version-bound, and explicit about containment', () => {
    assert.deepEqual(
        Object.keys(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities),
        requiredCapabilityIds,
    );
    assert.deepEqual(MOLSTAR_DIRECT_45_CAPABILITIES.adapter, {
        id: 'bms-molstar-direct',
        version: '1',
        enginePackage: 'molstar',
        engineVersion: '4.5.0',
        compatibilityReferencePackage: 'pdbe-molstar',
        compatibilityReferenceVersion: '3.3.0',
        wrapperRuntimeDependency: false,
        governedSurface: 'MolstarViewer',
    });
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities['disconnect-disposal'].status, 'supported');
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities['disconnect-disposal'].boundary, 'bms-engine-owner');
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities.overlays.status, 'supported');
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities.symmetry.status, 'unsupported');
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.privateApiInventory.length, 1);
    assert.equal(
        MOLSTAR_DIRECT_45_CAPABILITIES.privateApiInventory
            .find((entry) => entry.classification === 'private-diagnostics-only')
            ?.productionBehaviorDependsOnIt,
        false,
    );
});

test('known 3.3 limitations are explicit and never upgraded by declaration alone', () => {
    const capabilities = MOLSTAR_STABLE_33_CAPABILITIES.capabilities;

    assert.equal(capabilities['load-completion'].status, 'partial');
    assert.equal(capabilities['load-errors'].status, 'partial');
    assert.equal(capabilities['disconnect-disposal'].status, 'unsupported');
    assert.equal(capabilities['insertion-code-identity'].status, 'unsupported');
    assert.equal(capabilities['model-identity'].status, 'unsupported');
    assert.equal(capabilities['alternate-location-identity'].status, 'unsupported');
    assert.equal(capabilities['operator-instance-identity'].status, 'unsupported');
    assert.equal(capabilities['repeated-entity-instance-identity'].status, 'unsupported');
    assert.equal(capabilities.selection.status, 'partial');
    assert.equal(capabilities.coloring.status, 'partial');
    assert.equal(capabilities.overlays.status, 'partial');
    assert.equal(capabilities['overlay-removal'].status, 'partial');
    assert.equal(capabilities.measurements.status, 'unsupported');
    assert.equal(capabilities.trajectories.status, 'unsupported');
    assert.equal(capabilities.assemblies.status, 'partial');
    assert.equal(capabilities.symmetry.status, 'unsupported');
    assert.equal(capabilities.volumes.status, 'partial');
    assert.equal(capabilities.snapshots.status, 'unsupported');
    assert.equal(capabilities['event-provenance'].status, 'partial');
});

test('unrepresentable or namespace-ambiguous residue identity fails closed', () => {
    assert.deepEqual(
        assessMolstarStable33Identity({ labelAsymId: 'A', labelSeqId: 42 }),
        { status: 'supported', unsupportedFields: [], reasons: [] },
    );
    assert.deepEqual(
        assessMolstarStable33Identity({ authorAsymId: 'A', authorSeqId: 42 }),
        { status: 'supported', unsupportedFields: [], reasons: [] },
    );
    assert.deepEqual(
        assessMolstarStable33Identity({
            labelAsymId: 'A',
            labelSeqId: 42,
            insertionCode: 'B',
            modelId: '2',
            alternateLocation: 'A',
            operatorInstanceId: '1_555',
            repeatedEntityInstanceId: 'copy-2',
        }),
        {
            status: 'unsupported',
            unsupportedFields: [
                'insertionCode',
                'modelId',
                'alternateLocation',
                'operatorInstanceId',
                'repeatedEntityInstanceId',
            ],
            reasons: ['identityFieldUnsupported'],
        },
    );
    assert.deepEqual(
        assessMolstarStable33Identity({}),
        { status: 'ambiguous', unsupportedFields: [], reasons: ['missingChainOrEntity'] },
    );
    assert.deepEqual(
        assessMolstarStable33Identity({ entityId: '1', labelSeqId: 42 }),
        { status: 'ambiguous', unsupportedFields: [], reasons: ['residueWithoutChain'] },
    );
    assert.deepEqual(
        assessMolstarStable33Identity({ labelAsymId: 'A', authorSeqId: 42 }),
        { status: 'unsupported', unsupportedFields: [], reasons: ['mixedChainResidueNamespace'] },
    );
    assert.deepEqual(
        assessMolstarStable33Identity({ authorAsymId: 'A', labelSeqId: 42 }),
        { status: 'unsupported', unsupportedFields: [], reasons: ['mixedChainResidueNamespace'] },
    );
    assert.deepEqual(
        assessMolstarStable33Identity({ labelAsymId: 'A', authorAsymId: 'A' }),
        { status: 'unsupported', unsupportedFields: [], reasons: ['dualChainNamespace'] },
    );
    assert.deepEqual(
        assessMolstarStable33Identity({ labelAsymId: 'A', labelSeqId: 42, authorSeqId: 42 }),
        { status: 'unsupported', unsupportedFields: [], reasons: ['dualResidueNamespace'] },
    );
});

test('retired PDBe runtime is absent from production dependency and resolution surfaces', () => {
    const packageSource = readFileSync(path.resolve(frontendRoot, 'package.json'), 'utf8');
    const viteConfig = readFileSync(path.resolve(frontendRoot, 'vite.config.ts'), 'utf8');
    const rootLock = readFileSync(path.resolve(frontendRoot, '../../pnpm-lock.yaml'), 'utf8');

    assert.doesNotMatch(packageSource, /pdbe-molstar/);
    assert.doesNotMatch(viteConfig, /pdbe-molstar/);
    assert.doesNotMatch(rootLock, /pdbe-molstar/);
});
