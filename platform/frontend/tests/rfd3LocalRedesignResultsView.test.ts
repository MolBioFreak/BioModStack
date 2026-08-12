import assert from 'node:assert/strict';
import test from 'node:test';

import type { RFD3LocalRedesignReadModel } from '../src/lib/api';
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
