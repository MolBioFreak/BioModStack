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

test('CM result shell uses the shared workbench with an explicit fullscreen canvas mode', () => {
    const viewer = source('conformationalMapping/ConformationalMappingViewer.tsx');
    assert.match(viewer, /from ['"]\.\.\/\.\.\/structureViewer\/StructureWorkbench['"]/);
    assert.match(viewer, /<StructureWorkbench/);
    assert.doesNotMatch(viewer, /<MolstarViewer/);
    assert.doesNotMatch(viewer, /height=\{650\}/);
    assert.match(viewer, /data-cm-viewer-fullscreen/);
    assert.match(viewer, /requestFullscreen\(\)/);
    assert.match(viewer, /height="100%"/);
    assert.match(viewer, /showMetricWorkbench=\{metricWorkbenchOpen\}/);
    assert.match(viewer, /showSequenceTrack=\{metricWorkbenchOpen\}/);
    assert.match(viewer, /Structural hypotheses in API order/);
    assert.match(viewer, /Compare as structural overlay/);
    assert.match(viewer, /firstAlternative/);
    assert.match(viewer, /not time-resolved sampling or state populations/);
});

test('downloads use content-addressed CM request-scoped API identities', () => {
    const api = source('conformationalMapping/conformationalMappingApi.ts');
    const viewer = source('conformationalMapping/ConformationalMappingViewer.tsx');
    assert.match(api, /cmArtifactUrl/);
    assert.match(api, /encodeURIComponent\(artifactId\)/);
    assert.match(viewer, /cmArtifactUrl/);
    assert.doesNotMatch(viewer, /artifactJobId=/);
});

test('launcher exposes state-conditioned FrustraMPNN comparison as an explicit typed payload option', () => {
    const launcher = source('conformationalMapping/ConformationalMappingLauncher.tsx');
    const api = source('conformationalMapping/conformationalMappingApi.ts');
    assert.match(launcher, /State-conditioned FrustraMPNN comparison target/);
    assert.match(launcher, /payload\.state_landscape_comparison/);
    assert.match(api, /state_landscape_comparison\?:/);
});

test('launcher can register a pasted canonical protein sequence into the existing immutable source registry', () => {
    const launcher = source('conformationalMapping/ConformationalMappingLauncher.tsx');
    const api = source('conformationalMapping/conformationalMappingApi.ts');
    assert.match(launcher, /Paste protein sequence/);
    assert.match(launcher, /registerPastedSequence/);
    assert.match(launcher, /new File\(\[canonicalSequence\], 'protein-sequence\.fasta'/);
    assert.match(launcher, /source_kind === 'protein_sequence'/);
    assert.match(launcher, /update\('sequenceId', source\.source_id\)/);
    assert.match(launcher, /RCSB PDB tie-in/);
    assert.match(launcher, /registerCmRcsbMmcif/);
    assert.match(launcher, /Register raw mmCIF/);
    assert.match(api, /registerCmRcsbMmcif/);
    assert.match(api, /sources\/rcsb/);
});

test('normal external import is mmCIF-only with server-derived snapshot authority', () => {
    const launcher = source('conformationalMapping/ConformationalMappingLauncher.tsx');
    assert.match(launcher, /Protein mmCIF upload/);
    assert.match(launcher, /\.cif,\.mmcif/);
    assert.match(launcher, /source\.format === 'mmcif'/);
    assert.match(launcher, /External import accepts registered mmCIF handles only/);
    assert.match(launcher, /form\.importIds\.length !== 1/);
    assert.doesNotMatch(launcher, /<select multiple value=\{form\.importIds\}/);
    assert.doesNotMatch(launcher, /\.cif,\.mmcif,\.pdb/);
    assert.match(launcher, /Snapshot and residue identity are derived server-side from immutable staged bytes/);
    assert.doesNotMatch(
        launcher,
        /if \(form\.backend === 'external_import'\) \{\s*payload\.registered_snapshot_id/,
    );
    assert.doesNotMatch(launcher, /form\.snapshotId\) errors\.push\('Select the matching ordered complete-complex snapshot bundle/);
});
