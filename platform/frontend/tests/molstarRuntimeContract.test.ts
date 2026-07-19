import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { MOLSTAR_DIRECT_45_CAPABILITIES } from '../src/structureViewer/runtime/molstarDirect45Capabilities.js';
import type { ViewerCapabilityId } from '../src/structureViewer/contracts/viewerCapabilities.js';

const frontendRoot = process.cwd();
const requiredCapabilityIds = [
    'load-completion', 'load-errors', 'disconnect-disposal', 'label-chain-identity',
    'author-chain-identity', 'label-residue-identity', 'author-residue-identity',
    'insertion-code-identity', 'model-identity', 'alternate-location-identity',
    'operator-instance-identity', 'repeated-entity-instance-identity', 'selection',
    'coloring', 'overlays', 'overlay-removal', 'measurements', 'trajectories',
    'assemblies', 'symmetry', 'volumes', 'snapshots', 'event-provenance',
] as const satisfies readonly ViewerCapabilityId[];

test('direct 4.5 adapter manifest is complete and version-bound', () => {
    assert.deepEqual(Object.keys(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities), requiredCapabilityIds);
    assert.deepEqual(MOLSTAR_DIRECT_45_CAPABILITIES.adapter, {
        id: 'bms-molstar-direct',
        version: '2',
        enginePackage: 'molstar',
        engineVersion: '4.5.0',
        wrapperRuntimeDependency: false,
        governedSurface: 'StructureViewerHost',
    });
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities['disconnect-disposal'].status, 'supported');
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities.measurements.status, 'supported');
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities.snapshots.status, 'supported');
    assert.equal(MOLSTAR_DIRECT_45_CAPABILITIES.capabilities['event-provenance'].status, 'supported');
});

test('unsupported advanced identities remain explicit and fail-closed', () => {
    for (const id of ['model-identity', 'operator-instance-identity', 'repeated-entity-instance-identity'] as const) {
        const capability = MOLSTAR_DIRECT_45_CAPABILITIES.capabilities[id];
        assert.equal(capability.status, 'unsupported');
        assert.equal(capability.failClosed, true);
    }
});

test('retired PDBe runtime is absent from production dependency and resolution surfaces', () => {
    const packageSource = readFileSync(path.resolve(frontendRoot, 'package.json'), 'utf8');
    const viteConfig = readFileSync(path.resolve(frontendRoot, 'vite.config.ts'), 'utf8');
    const rootLock = readFileSync(path.resolve(frontendRoot, '../../pnpm-lock.yaml'), 'utf8');
    const facade = readFileSync(path.resolve(frontendRoot, 'src/components/MolstarViewer.tsx'), 'utf8');
    assert.doesNotMatch(packageSource, /pdbe-molstar/);
    assert.doesNotMatch(viteConfig, /pdbe-molstar/);
    assert.doesNotMatch(rootLock, /pdbe-molstar/);
    assert.doesNotMatch(facade, /viewerInstance|pdbe-molstar/);
    assert.match(facade, /StructureViewerHost/);
});
