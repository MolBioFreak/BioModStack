import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import type { Job, RFD3LocalRedesignReadModel } from '../src/lib/api';
import { resolveRFD3LocalRedesignRequestView } from '../src/components/rfd3LocalRedesignResultsView';

test('RFD3 result view keeps envelope provenance separate from canonical request fields', () => {
    const canonicalRequest = {
        redesign_mode: 'partial_diffusion',
        sequence_policy: 'skip',
        contig_dialect: 2,
        input: { path: '1AKI_chainA_polymer.pdb', sha256: 'a'.repeat(64) },
    };
    const result = {
        request: {
            request_sha256: 'b'.repeat(64),
            profile_id: 'generic_local_redesign_v1',
            profile_registry_sha256: 'c'.repeat(64),
            status: 'completed',
            request: canonicalRequest,
        },
    } as unknown as RFD3LocalRedesignReadModel;

    assert.deepEqual(resolveRFD3LocalRedesignRequestView(result), {
        request: canonicalRequest,
        status: 'completed',
        requestSha256: 'b'.repeat(64),
        profileId: 'generic_local_redesign_v1',
        profileRegistrySha256: 'c'.repeat(64),
    });
});

test('RFD3 result view remains render-safe before data is available', () => {
    assert.deepEqual(resolveRFD3LocalRedesignRequestView(undefined), {
        request: undefined,
        status: undefined,
        requestSha256: undefined,
        profileId: undefined,
        profileRegistrySha256: undefined,
    });
});

test('native RFD3 jobs have an exact typed result discriminator and candidate label', async () => {
    const module = await import('../src/components/rfd3LocalRedesignResultsView');
    const isNative = (module as typeof module & {
        isRFD3LocalRedesignResultJob?: (job: Job | null | undefined) => boolean;
    }).isRFD3LocalRedesignResultJob;
    const label = (module as typeof module & {
        getRFD3LocalRedesignCandidateLabel?: (job: Job | null | undefined) => string | null;
    }).getRFD3LocalRedesignCandidateLabel;
    assert.equal(typeof isNative, 'function');
    assert.equal(typeof label, 'function');

    const nativeJob = {
        model_id: 'protein_local_redesign',
        mode: 'local_redesign',
        requested_design_count: 8,
        params: { num_designs: 8 },
    } as unknown as Job;
    const validatedJob = {
        model_id: 'protein_modification_experimental',
        mode: 'region_redesign',
        requested_design_count: 1,
        params: { num_designs: 1 },
    } as unknown as Job;

    assert.equal(isNative?.(nativeJob), true);
    assert.equal(label?.(nativeJob), '8 RFD3 candidates');
    assert.equal(isNative?.(validatedJob), false);
    assert.equal(label?.(validatedJob), null);
});

test('Results Viewer routes native RFD3 to its typed pane before generic redesign data', () => {
    const source = readFileSync('src/components/ResultsViewer.tsx', 'utf8');
    assert.match(source, /import RFD3LocalRedesignResultsPane/);
    const nativeBranch = source.indexOf('isRFD3LocalRedesignResultJob(activeJob)');
    const genericBranch = source.indexOf('isProteinLocalRedesignResultJob(activeJob)');
    assert.ok(nativeBranch >= 0 && nativeBranch < genericBranch);
    assert.match(source, /<RFD3LocalRedesignResultsPane key=\{activeJob\.id\} jobId=\{activeJob\.id\}/);
    assert.match(source, /!isRFD3LocalRedesignResultJob\(activeJob\)/);
});
