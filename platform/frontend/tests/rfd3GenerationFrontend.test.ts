import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path: string) => readFileSync(path, 'utf8');

test('RFD3 generation result surface is routed before generic design results', () => {
    const viewer = source('src/components/ResultsViewer.tsx');
    const pane = source('src/components/RFD3GenerationResultsPane.tsx');
    assert.match(pane, /job\?\.model_id === 'protein_modification_experimental'/);
    assert.match(pane, /job\?\.mode === 'de_novo_design'/);
    assert.match(viewer, /isRFD3GenerationResultJob\(activeJob\)/);
    assert.match(viewer, /<RFD3GenerationResultsPane key=\{activeJob\.id\} jobId=\{activeJob\.id\}/);
    assert.match(viewer, /!isRFD3GenerationResultJob\(activeJob\)/);
});

test('RFD3 generation result surface renders authoritative aggregates, candidates, and structure links', () => {
    const pane = source('src/components/RFD3GenerationResultsPane.tsx');
    for (const expected of ['Requested', 'Generated', 'Accepted', 'Length', 'Radius', 'Helix', 'Strand', 'candidate.candidate_id', 'candidate.structure_url']) {
        assert.match(pane, new RegExp(expected.replace('.', '\\.')));
    }
});

test('RFD3 generation API client uses the dedicated endpoint and closed schema', () => {
    const apiSource = source('src/lib/api.ts');
    assert.match(apiSource, /schema: 'bms\.rfd3\.generation\.read-model\.v1'/);
    assert.match(apiSource, /`\/api\/jobs\/\$\{encodeURIComponent\(id\)\}\/rfd3-generation`/);
});