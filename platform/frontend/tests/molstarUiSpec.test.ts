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

test('BMS Molstar UI specs exclude process-global query-symbol behaviors', () => {
    assert.doesNotMatch(source, /PluginSpec\.Behavior\(MAQualityAssessment/);
    assert.match(source, /PluginBehaviors\.CustomProps\.AccessibleSurfaceArea/);
    assert.match(source, /filter\(/);
    assert.match(source, /BmsPLDDTQualityAssessment/);
});

test('full viewers expose Molstar settings while compact viewers can explicitly hide them', () => {
    assert.match(source, /ShowSettings,\s*!hideControls/);
    assert.match(source, /ShowSelectionMode,\s*!hideControls/);
    assert.match(source, /ShowAnimation,\s*!hideControls/);
    assert.match(source, /left:\s*hideControls\s*\?\s*'none'/);
    assert.match(source, /right:\s*hideControls\s*\?\s*'none'/);
    assert.match(viewerSource, /hideControls\s*=\s*false/);
    assert.match(paneSource, /<StructureWorkbench[\s\S]*?mode="standard"[\s\S]*?height="100%"/);
    assert.doesNotMatch(paneSource, /<StructureWorkbench[\s\S]{0,500}?mode="standard"[\s\S]{0,500}?hideControls/);
});
