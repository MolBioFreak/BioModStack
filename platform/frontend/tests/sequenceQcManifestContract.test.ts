import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import { classifySequenceQcManifestError } from '../src/components/ngs/sequenceQcManifestState.js';

function readSource(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

test('sequence QC manifest API helpers target the typed manifest routes', () => {
    const api = readSource('src/lib/api.ts');

    assert.match(api, /export interface SequenceQcManifest/u);
    assert.match(api, /fetchSequenceQcManifest = \(jobId: string\)/u);
    assert.match(api, /`\/api\/sequence-qc\/jobs\/\$\{jobId\}\/manifest`/u);
    assert.match(api, /fetchSequenceQcManifestByPath = \(path: string\)/u);
    assert.match(api, /'\/api\/sequence-qc\/manifest'/u);
});

test('missing sequence QC manifest is classified as an old-run unavailable state, not a workflow failure', () => {
    assert.equal(
        classifySequenceQcManifestError({ response: { status: 404, data: { detail: 'sequence-QC manifest not found for job_id: old-job' } } }),
        'unavailable-old-run',
    );
    assert.equal(
        classifySequenceQcManifestError({ response: { status: 400, data: { detail: 'manifest is not valid JSON: nope' } } }),
        'malformed',
    );
    assert.equal(
        classifySequenceQcManifestError({ response: { status: 403, data: { detail: 'Path escapes allowed root' } } }),
        'forbidden',
    );
});

test('NGSToolkit consumes useSequenceQcManifest and renders a manifest-first panel before path-scraped reports', () => {
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');
    const hook = readSource('src/components/ngs/useSequenceQcManifest.ts');
    const panel = readSource('src/components/ngs/SequenceQcManifestPanel.tsx');

    assert.match(ngsToolkit, /useSequenceQcManifest\(selectedJob\?\.id/u);
    assert.match(ngsToolkit, /<SequenceQcManifestPanel/u);
    assert.match(hook, /queryKey: \['sequence-qc-manifest', jobId, jobStatus\]/u);
    assert.match(hook, /enabled: Boolean\(jobId\) && shouldFetchSequenceQcManifest\(jobStatus\)/u);
    assert.match(hook, /status: 'unavailable-pending'/u);
    assert.match(hook, /fetchSequenceQcManifest\(jobId\)/u);
    assert.match(panel, /Sequence-QC Manifest/u);
    assert.match(panel, /manifest unavailable for older run/i);
    assert.match(panel, /fallback consensus cannot verify construct/i);
});
