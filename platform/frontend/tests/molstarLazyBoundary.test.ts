import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const read = (relativePath: string) => readFileSync(path.resolve(process.cwd(), relativePath), 'utf8');

test('generic MolstarViewer is a typed lazy facade over the direct implementation', () => {
    const facade = read('src/components/MolstarViewer.tsx');
    const implementation = read('src/components/MolstarViewerImpl.tsx');

    assert.match(facade, /lazy\(\(\) => import\('\.\/MolstarViewerImpl'\)\)/);
    assert.match(facade, /<Suspense/);
    assert.doesNotMatch(facade, /molstar\/lib|molstar\/build/);
    assert.match(implementation, /new MolstarDirectAdapter/);
});

test('epitope viewer is lazy so the legacy PDBe runtime loads only when mounted', () => {
    const facade = read('src/components/EpitopeMolstarViewer.tsx');
    const implementation = read('src/components/EpitopeMolstarViewerImpl.tsx');

    assert.match(facade, /lazy\(\(\) => import\('\.\/EpitopeMolstarViewerImpl'\)\)/);
    assert.match(facade, /<Suspense/);
    assert.doesNotMatch(facade, /molstar-loader|pdbe-molstar/);
    assert.match(implementation, /<MolstarViewer/);
    assert.doesNotMatch(implementation, /ensureMolstarLoaded|pdbe-molstar/);
});
