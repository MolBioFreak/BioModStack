import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import {
    classifySequenceQcManifestError,
    sequenceQcManifestUnavailableLabel,
} from '../src/components/ngs/sequenceQcManifestState.js';
import { SequenceQcManifestPanel } from '../src/components/ngs/SequenceQcManifestPanel.js';
import type { SequenceQcManifest } from '../src/lib/api.js';

function readSource(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

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
    assert.equal(
        classifySequenceQcManifestError({ response: { status: 403, data: { detail: 'alignment access denied' } } }),
        'access-denied',
    );
    assert.equal(
        sequenceQcManifestUnavailableLabel('access-denied'),
        'manifest access requires browser authorization',
    );
    assert.equal(
        sequenceQcManifestUnavailableLabel('forbidden'),
        'manifest blocked by path safety',
    );
});

test('alignment access denial renders browser authorization copy instead of a path-safety failure', () => {
    Reflect.set(globalThis, 'React', React);
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
        React.createElement(
            QueryClientProvider,
            { client: queryClient },
            React.createElement(SequenceQcManifestPanel, {
                status: 'access-denied',
                manifest: null,
                message: 'alignment access denied',
            }),
        ),
    );

    assert.match(html, /manifest access requires browser authorization/u);
    assert.match(html, /alignment access denied/u);
    assert.doesNotMatch(html, /manifest blocked by path safety/u);
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
