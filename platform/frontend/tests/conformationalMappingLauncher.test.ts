import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const source = (relative: string) => readFileSync(resolve(process.cwd(), 'src/components', relative), 'utf8');

test('canonical launcher has dedicated backend-aware submission', () => {
    const launcher = source('conformationalMapping/ConformationalMappingLauncher.tsx');
    assert.match(launcher, /protenix_v2_ensemble/);
    assert.match(launcher, /confornets/);
    assert.match(launcher, /external_import/);
    assert.match(launcher, /submitCmRequest/);
    assert.doesNotMatch(launcher, /serverPath|absolutePath/);
});

test('workflow display names the Protenix-first pipeline and downstream analysis', () => {
    const launcher = source('conformationalMapping/ConformationalMappingLauncher.tsx');
    const submission = source('JobSubmission.tsx');
    const template = readFileSync(
        resolve(process.cwd(), '../api/config/templates/conformational_mapping.yaml'),
        'utf8',
    );

    for (const token of [
        'Complete-complex Protenix v2 ensembles',
        'Residue mapping',
        'FrustraMPNN landscapes',
        'Support + ranking',
    ]) {
        assert.ok(`${launcher}\n${submission}\n${template}`.includes(token), `missing workflow display token: ${token}`);
    }
    assert.match(launcher, /ModelDocumentationLinks/);
    assert.match(launcher, /topics=\{\['protenix', 'confornets', 'fampnn'\]\}/);
});

test('launcher exposes lifecycle, failure, resource, and storage semantics', () => {
    const launcher = source('conformationalMapping/ConformationalMappingLauncher.tsx');
    const viewer = source('conformationalMapping/ConformationalMappingViewer.tsx');
    for (const token of ['Planning storage', 'samples_per_seed', 'ordered_seeds']) assert.match(launcher, new RegExp(token));
    for (const token of ['cancelCmRequest', 'retryCmRequest', 'getCmFailureReceipts', 'getCmLogs']) assert.match(`${launcher}\n${viewer}`, new RegExp(token));
});

test('template state round-trips canonical launcher values', () => {
    const submission = source('JobSubmission.tsx');
    const state = source('jobSubmissionTemplateState.ts');
    assert.match(submission, /ConformationalMappingLauncher/);
    assert.match(state, /conformational_mapping/);
    assert.match(state, /ordered_seeds/);
    assert.match(submission, /bms\.conformational-mapping\.launcher\.v1/);
});

test('downloads use content-addressed API identities', () => {
    const api = source('conformationalMapping/conformationalMappingApi.ts');
    const viewer = source('conformationalMapping/ConformationalMappingViewer.tsx');
    assert.match(api, /cmArtifactUrl/);
    assert.match(api, /encodeURIComponent\(artifactId\)/);
    assert.match(viewer, /cmArtifactUrl/);
});
