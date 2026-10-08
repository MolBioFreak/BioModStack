import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const panel = readFileSync(new URL('../src/components/MolBioToolkit/panels/AssemblyPanel.tsx', import.meta.url), 'utf8');
const workspacePath = new URL('../src/components/MolBioToolkit/panels/GibsonDesignWorkspace.tsx', import.meta.url);
const api = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');

test('Gibson defaults to a raw-fragment design lane while retaining validation', () => {
    assert.match(panel, /Design from raw fragments/);
    assert.match(panel, /Validate pre-overlapped/);
    assert.match(panel, /preparation/);
    assert.match(panel, /Move up/);
    assert.match(panel, /Move down/);
});

test('Gibson design workspace exposes design, primer review, preview, and explicit save', () => {
    const workspace = readFileSync(workspacePath, 'utf8');
    assert.match(workspace, /Design & Simulate/);
    assert.match(workspace, /Generated primers/);
    assert.match(workspace, /Load preview/);
    assert.match(workspace, /Save as new construct/);
    assert.match(workspace, /selected_candidate_checksum/);
});

test('API client uses the typed design and design-save routes', () => {
    assert.match(api, /interface GibsonDesignRequest/);
    assert.match(api, /interface GibsonDesignResponse/);
    assert.match(api, /\/api\/molbio\/assembly\/gibson\/design'/);
    assert.match(api, /\/api\/molbio\/assembly\/gibson\/design\/save'/);
});
