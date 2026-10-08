import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const apiSource = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
const landingSource = readFileSync(new URL('../src/components/DataViewerLanding.tsx', import.meta.url), 'utf8');

test('API client exposes typed Boltz external import preview, create, and status calls', () => {
    assert.match(apiSource, /previewExternalImport/);
    assert.match(apiSource, /createExternalImport/);
    assert.match(apiSource, /fetchExternalImport/);
    assert.match(apiSource, /\/api\/jobs\/imports\/external\/preview/);
    assert.match(apiSource, /\/api\/jobs\/imports\/external/);
});

test('Data Hub offers a server-authoritative Boltz API downloaded-run import path', () => {
    assert.match(landingSource, /boltz_api_run/);
    assert.match(landingSource, /Boltz API downloaded run/);
    assert.match(landingSource, /previewExternalImport/);
    assert.match(landingSource, /preview_fingerprint/);
    assert.match(landingSource, /createExternalImport/);
    assert.match(landingSource, /fetchExternalImport/);
    assert.match(landingSource, /RESOURCE_UNSUPPORTED/);
});

test('existing ProteinBase upload and import handoff remains present', () => {
    assert.match(landingSource, /uploadFile\(/);
    assert.match(landingSource, /importProteinBaseBundle/);
    assert.match(landingSource, /onImportComplete\(job\)/);
});
