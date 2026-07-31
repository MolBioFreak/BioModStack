import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const read = (relativePath: string) => readFileSync(path.resolve(process.cwd(), relativePath), 'utf8');

test('generic MolstarViewer is a typed lazy facade over the shared host', () => {
    const facade = read('src/components/MolstarViewer.tsx');
    const implementation = read('src/components/MolstarViewerImpl.tsx');
    const boundary = read('src/structureViewer/StructureViewerErrorBoundary.tsx');
    const host = read('src/structureViewer/StructureViewerHost.tsx');

    assert.match(facade, /lazy\(\(\) => import\('\.\.\/structureViewer\/StructureViewerHost'\)\)/);
    assert.match(facade, /<Suspense/);
    assert.match(facade, /<StructureViewerErrorBoundary/);
    assert.match(boundary, /data-bms-molstar-status="error-boundary"/);
    assert.match(boundary, /role="alert"/);
    assert.match(host, /const hostHeight = typeof viewerProps\.height === 'number'/);
    assert.match(host, /style=\{hostHeight \? \{ height: hostHeight \} : undefined\}/);
    assert.match(host, /hostHeight \? '' : 'h-full'/);
    assert.doesNotMatch(facade, /molstar\/lib|molstar\/build/);
    assert.match(implementation, /new MolstarDirectAdapter/);
});

test('epitope viewer is lazy and composes the shared direct-Molstar workbench', () => {
    const facade = read('src/components/EpitopeMolstarViewer.tsx');
    const implementation = read('src/components/EpitopeMolstarViewerImpl.tsx');

    assert.match(facade, /lazy\(\(\) => import\('\.\/EpitopeMolstarViewerImpl'\)\)/);
    assert.match(facade, /<Suspense/);
    assert.doesNotMatch(facade, /molstar-loader|pdbe-molstar/);
    assert.match(implementation, /<StructureWorkbench/);
    assert.doesNotMatch(implementation, /ensureMolstarLoaded|pdbe-molstar/);
});
