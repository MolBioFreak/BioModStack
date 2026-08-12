import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { classifySequenceQcManifestError } from '../src/components/ngs/sequenceQcManifestState.js';
import { SequenceQcManifestPanel } from '../src/components/ngs/SequenceQcManifestPanel.js';
import type { SequenceQcManifest } from '../src/lib/api.js';

function readSource(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

test('sequence QC manifest API helpers target the typed manifest routes', () => {
    const api = readSource('src/lib/api.ts');

    assert.match(api, /export interface SequenceQcManifest/u);
    assert.match(api, /fetchSequenceQcManifest = \(jobId: string\)/u);
    assert.match(api, /`\/api\/jobs\/\$\{encodeURIComponent\(jobId\)\}\/sequence-qc-manifest`/u);
    assert.doesNotMatch(api, /fetchSequenceQcManifestByPath/u);
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
    assert.match(panel, /biomodstack\.construct_verification\.v2/u);
    assert.match(panel, /manifest\.verdict/u);
    assert.match(panel, /manifest\.reason_codes/u);
    assert.match(panel, /manifest\.execution/u);
    assert.match(panel, /calibration_status/u);
    assert.match(panel, /public_accuracy_validated/u);
    assert.match(panel, /Experimental thresholds/u);
    assert.match(panel, /manifest\.checks/u);
    assert.match(panel, /manifest\.variants/u);
    assert.match(panel, /Sequence identity/u);
    assert.match(panel, /Contamination screen/u);
    assert.match(panel, /Topology/u);
});

test('construct verification uses schema field names and exposes evidence provenance and bound navigation', () => {
    const api = readSource('src/lib/api.ts');
    const panel = readSource('src/components/ngs/SequenceQcManifestPanel.tsx');
    const toolkit = readSource('src/components/NGSToolkit.tsx');

    assert.match(api, /kind\?: string/u);
    assert.match(api, /position_1based\?: number/u);
    assert.match(api, /support_status\?:/u);
    assert.match(api, /circular_event_id\?:/u);
    assert.match(api, /declared_sequence_sha256/u);
    assert.match(api, /normalized_sequence_sha256/u);
    assert.match(panel, /Expected reference/u);
    assert.match(panel, /Observed evidence/u);
    assert.match(panel, /independent_from_expected/u);
    assert.match(panel, /semantic_validation/u);
    assert.match(panel, /onNavigateLocus/u);
    assert.match(toolkit, /selectedAlignmentSession\.session_id/u);
    assert.match(toolkit, /resolveBoundSessionLocus/u);
});

test('real verification fields render top-level provenance and variant support evidence', () => {
    Reflect.set(globalThis, 'React', React);
    const manifest: SequenceQcManifest = {
        artifact_schema_version: 2,
        schema: 'biomodstack.construct_verification.v2',
        job_id: 'job-verified',
        verdict: 'FAIL',
        reason_codes: ['VARIANTS_DETECTED'],
        threshold_profile: {
            id: 'plasmid_strict_v1',
            version: '1.0.0',
            sha256: '90fad5ea643fc6509cd174020a52563c0a0ec4d38836328cd4bdc7eed9015553',
            calibration_status: 'experimental',
            public_accuracy_validated: false,
        },
        provenance: {
            source_reads_sha256: 'reads-digest-visible',
            reference_digest_binding: 'reference-binding-visible',
        },
        variants: [{
            id: 'variant-1',
            kind: 'INS',
            position_1based: 9,
            ref: 'A',
            alt: 'AT',
            support_status: 'supported',
            depth: 264,
            support_fraction: 0.943182,
        }],
        artifacts: [],
    };
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
        React.createElement(
            QueryClientProvider,
            { client: queryClient },
            React.createElement(SequenceQcManifestPanel, {
                status: 'available',
                manifest,
                message: null,
            }),
        ),
    );

    assert.match(html, /Verification provenance/u);
    assert.match(html, /reads-digest-visible/u);
    assert.match(html, /reference-binding-visible/u);
    assert.match(html, /supported/u);
    assert.match(html, /264/u);
    assert.match(html, /0\.94318/u);
});
