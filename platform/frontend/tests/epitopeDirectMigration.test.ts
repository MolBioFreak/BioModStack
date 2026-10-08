import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const read = (relativePath: string) => readFileSync(path.resolve(process.cwd(), relativePath), 'utf8');

test('epitope viewer uses the direct owned runtime rather than PDBe globals', () => {
    const source = read('src/components/EpitopeMolstarViewerImpl.tsx');
    assert.match(source, /from ['"]\.\/MolstarViewer['"]/u);
    assert.match(source, /<MolstarViewer/u);
    assert.match(source, /onResidueClick=/u);
    assert.doesNotMatch(source, /ensureMolstarLoaded|pdbe-molstar|viewerInstance|PDB\.molstar\.click/u);
});

test('bundle no longer declares or aliases the retired PDBe runtime', () => {
    const packageSource = read('package.json');
    const viteSource = read('vite.config.ts');
    assert.doesNotMatch(packageSource, /pdbe-molstar/u);
    assert.doesNotMatch(viteSource, /pdbe-molstar/u);
});
