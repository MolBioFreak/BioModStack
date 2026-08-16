import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { resolveGovernedStructureWorkbenchContext } from '../src/structureViewer/contracts/governedStructureWorkbenchContext.js';

const JOB_ID = '9e5a5120-5663-4d28-9230-e09e0a0e7d7c';
const DOCUMENT_ID = '6726f925-9ccc-4538-a94e-1cb1cc0ae00b';
const STRUCTURE_SHA256 = 'b40555f009482f25c152f29b331596b04accb5e823f73b0575e9caca311ba7bd';

const inventory = (overrides: Record<string, unknown> = {}) => ({
    schema: 'bms.viewer.volume-list.v1',
    jobId: JOB_ID,
    volumes: [],
    segmentations: [],
    registrations: [{
        schema: 'bms.viewer.volume-registration.v1',
        registrationId: 'e3ef44aa-f24a-4566-9e88-bc260ce0cb2f',
        structureDocumentId: DOCUMENT_ID,
        structureSha256: STRUCTURE_SHA256,
        volumeId: '4adc6e66-d23c-4991-94a2-bac7c9aaac2f',
        volumeSha256: 'f5d4f21df85a48803c3ae278acc1b69e8ba74171a25486a49456603d1603bf6f',
        artifactSha256: 'b5cb539c708a1d863259b35cb99d583fa242bc9168495219c90a87ae30d36dac',
        method: 'supplied_transform_v1',
        transformRowMajor4x4: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        provenanceRef: 'viewer/provenance.json#registration',
    }],
    ...overrides,
});

const design = (overrides: Record<string, unknown> = {}) => ({
    job_id: JOB_ID,
    provenance: { sha256: STRUCTURE_SHA256 },
    ...overrides,
});

test('resolves generic workbench identities only from matching governed result and volume metadata', () => {
    assert.deepEqual(resolveGovernedStructureWorkbenchContext({
        activeJobId: JOB_ID,
        design: design(),
        inventory: inventory(),
    }), {
        jobId: JOB_ID,
        artifactJobId: JOB_ID,
        structureDocumentId: DOCUMENT_ID,
    });
});

test('fails closed when result ownership, structure hash, or document identity cannot be proven', () => {
    assert.equal(resolveGovernedStructureWorkbenchContext({
        activeJobId: '00000000-0000-4000-8000-000000000000',
        design: design(),
        inventory: inventory(),
    }), null);
    assert.equal(resolveGovernedStructureWorkbenchContext({
        activeJobId: JOB_ID,
        design: design({ provenance: { sha256: 'a'.repeat(64) } }),
        inventory: inventory(),
    }), null);
    assert.equal(resolveGovernedStructureWorkbenchContext({
        activeJobId: JOB_ID,
        design: design(),
        inventory: inventory({ registrations: [{ ...inventory().registrations[0], structureDocumentId: 'not-a-uuid' }] }),
    }), null);
});

test('the generic Results structure surface passes all governed identities to StructureWorkbench', () => {
    const pane = readFileSync(path.resolve(process.cwd(), 'src/components/StructureViewerPane.tsx'), 'utf8');
    const host = readFileSync(path.resolve(process.cwd(), 'src/structureViewer/StructureViewerHost.tsx'), 'utf8');
    assert.match(pane, /resolveGovernedStructureWorkbenchContext/);
    assert.match(pane, /jobId=\{shapeMetrics \? undefined : governedWorkbenchContext\?\.jobId \?\? activeJob\?\.id\}/);
    assert.match(pane, /artifactJobId=\{shapeMetrics \? undefined : governedWorkbenchContext\?\.artifactJobId \?\? activeJob\?\.id\}/);
    assert.match(pane, /structureDocumentId=\{governedWorkbenchContext\?\.structureDocumentId\}/);
    assert.match(host, /artifactJobId: requestedArtifactJobId/);
    assert.match(host, /const artifactJobId = requestedArtifactJobId \?\? jobId;/);
});
