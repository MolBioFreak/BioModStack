import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const source = readFileSync(
    path.resolve(process.cwd(), 'src/structureViewer/runtime/createDirectMolstarEngineOwner.ts'),
    'utf8',
);
const viewerSource = readFileSync(
    path.resolve(process.cwd(), 'src/components/MolstarViewerImpl.tsx'),
    'utf8',
);
const paneSource = readFileSync(
    path.resolve(process.cwd(), 'src/components/StructureViewerPane.tsx'),
    'utf8',
);

test('BMS Molstar UI specs keep the stock behaviors and exclude process-global query-symbol behaviors', () => {
    assert.doesNotMatch(source, /PluginSpec\.Behavior\(MAQualityAssessment/);
    // Stock Mol* behaviors are kept whole (commit 7a87fca restored them); BMS only adds its own.
    assert.match(source, /\.\.\.\(defaults\.behaviors \?\? \[\]\)/);
    assert.doesNotMatch(source, /AccessibleSurfaceArea/);
    assert.match(source, /PluginSpec\.Behavior\(MolViewSpec\)/);
    assert.match(source, /BmsPLDDTQualityAssessment/);
});

test('every viewer keeps the stock Molstar viewport buttons; hideControls no longer removes tools', () => {
    for (const button of ['ShowExpand', 'ShowControls', 'ShowSettings', 'ShowSelectionMode', 'ShowAnimation']) {
        assert.match(source, new RegExp(`\\[PluginConfig\\.Viewport\\.${button},\\s*true\\]`));
    }
    assert.doesNotMatch(source, /ShowSettings,\s*!hideControls/);
    assert.doesNotMatch(source, /left:\s*hideControls\s*\?\s*'none'/);
    assert.doesNotMatch(source, /right:\s*hideControls\s*\?\s*'none'/);
    assert.match(viewerSource, /hideControls\s*=\s*false/);
    assert.match(paneSource, /<StructureWorkbench[\s\S]*?mode="standard"[\s\S]*?height="100%"/);
    // A standard-mode workbench may pass hideControls={false} (the default) but never hides controls.
    assert.doesNotMatch(paneSource, /<StructureWorkbench[\s\S]{0,500}?mode="standard"[\s\S]{0,500}?hideControls=\{(?!false\})/);
});

test('embedded controls and sequence stay responsive inside the viewer bounds once opened', () => {
    assert.match(source, /controlsDisplay:\s*'reactive'/);
    assert.match(source, /regionState:\s*\{ left: 'collapsed', top: 'full', right: 'full', bottom: 'full' \}/);
});

test('Molstar viewers open view-only; the controls wrench stays available but is not pre-activated', () => {
    // layoutShowControls=false: tools panel closed on load.
    assert.match(source, /showControls:\s*false/);
    assert.doesNotMatch(source, /showControls:\s*!hideControls/);
    // viewportShowControls=true: the wrench button itself is still offered.
    assert.match(source, /\[PluginConfig\.Viewport\.ShowControls,\s*true\]/);
});
