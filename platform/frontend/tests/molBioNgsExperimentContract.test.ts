import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import * as path from 'node:path';
import { test } from 'node:test';
import * as ts from 'typescript';

const frontendRoot = path.resolve(process.cwd(), 'src');
const apiRoot = path.resolve(process.cwd(), '../api');

function source(relative: string): string {
    return readFileSync(path.join(frontendRoot, relative), 'utf8');
}

function backend(relative: string): string {
    return readFileSync(path.join(apiRoot, relative), 'utf8');
}

function assertTypeScriptSyntax(relative: string): void {
    const input = source(relative);
    const result = ts.transpileModule(input, {
        fileName: relative,
        reportDiagnostics: true,
        compilerOptions: {
            target: ts.ScriptTarget.ES2022,
            module: ts.ModuleKind.ESNext,
            jsx: ts.JsxEmit.ReactJSX,
        },
    });
    const errors = (result.diagnostics ?? []).filter(
        (diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error,
    );
    assert.deepEqual(
        errors.map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n')),
        [],
        `${relative} must parse without TypeScript syntax errors`,
    );
}

const changedTypeScript = [
    'App.tsx',
    'components/Layout.tsx',
    'components/experiments/GlobalExperimentContext.tsx',
    'components/molbio-ngs/DomainExperimentWorkspace.tsx',
    'components/NanoporeTemplate.tsx',
    'components/NGSToolkit.tsx',
    'components/ngs/OntInstrumentPanel.tsx',
    'components/MolBioToolkit/MolBioToolkitV2.tsx',
    'components/MolBioToolkit/panels/HistoryPanel.tsx',
    'components/MolBioToolkit/panels/PCRPanel.tsx',
    'lib/api.ts',
    'lib/nanoporeCloneState.ts',
];

test('changed MolBio and NGS frontend sources are syntactically valid TypeScript', () => {
    for (const relative of changedTypeScript) assertTypeScriptSyntax(relative);
});

test('shared context preserves every identity and forbids Global-to-Domain substitution', () => {
    const context = source('components/experiments/GlobalExperimentContext.tsx');
    const workspace = source('components/molbio-ngs/DomainExperimentWorkspace.tsx');

    for (const key of ['workspace_id', 'global_experiment_id', 'domain_experiment_id', 'state_revision_id']) {
        assert.match(context, new RegExp(key));
    }
    assert.match(context, /domainExperiment\.global_experiment_id === globalExperimentId/);
    assert.match(context, /domainExperiment\.project_id === workspaceId/);
    assert.doesNotMatch(context, /domainExperimentId\s*:\s*globalExperimentId/);

    for (const label of [
        'Project / workspace ID',
        'Global Experiment ID',
        'NGS/MolBio Domain Experiment ID',
        'Global Domain Experiment revision ID',
        'Local state revision ID',
    ]) assert.match(workspace, new RegExp(label));
});

test('frontend local read routes and immutable reopen surfaces match backend routes', () => {
    const client = source('lib/api.ts');
    const domainRouter = backend('routers/molbio_ngs_experiments.py');
    const molbioRouter = backend('routers/molbio_ops.py');
    const sequenceRouter = backend('routers/nucleotide_sequences.py');
    const ontRouter = backend('routers/ont_runs.py');

    for (const route of [
        '/api/molbio-ngs/experiments/',
        '/api/molbio-ngs/projects/',
        '/samples',
        '/references',
        '/evidence',
    ]) assert.match(client, new RegExp(route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));

    assert.match(domainRouter, /"\/projects\/\{project_id\}\/experiments"/);
    assert.match(domainRouter, /"\/projects\/\{project_id\}\/summary"/);
    assert.match(domainRouter, /"\/experiments\/\{domain_experiment_id\}"/);
    assert.match(sequenceRouter, /"\/\{sequence_id\}\/revisions\/\{revision_id\}"/);
    assert.match(molbioRouter, /"\/pcr-experiments\/\{experiment_id\}\/revisions\/\{revision_id\}"/);
    assert.match(ontRouter, /"\/runs\/\{run_id\}\/generations\/\{observed_generation\}"/);

    assert.match(client, /issueReferenceRevisionMemberReceipt = \(\s*domainExperimentId: string,/);
    assert.match(client, /issueDomainMemberReceipt\(domainExperimentId, 'ngs-reference-revisions', payload\)/);
    assert.match(client, /issueEvidenceAssessmentMemberReceipt = \(\s*domainExperimentId: string,/);
    assert.match(client, /issueDomainMemberReceipt\(domainExperimentId, 'ngs-evidence-assessments', payload\)/);
    assert.match(client, /attachMolBioNgsInstrumentRunEvidence[\s\S]{0,250}state_revision_id: string/);
    for (const removed of [
        'issueOntRunMemberReceipt',
        'issueNgsJobMemberReceipt',
        'issueNgsResultManifestMemberReceipt',
    ]) assert.doesNotMatch(client, new RegExp(removed));
});

test('Nanopore launch uses immutable managed reference identity and full job detail is authoritative', () => {
    const nanopore = source('components/NanoporeTemplate.tsx');
    const ngs = source('components/NGSToolkit.tsx');
    const ontRouter = backend('routers/ont_runs.py');

    assert.match(nanopore, /managed_reference:\s*\{/);
    assert.match(nanopore, /global_domain_experiment_id:\s*exactDomainExperimentId/);
    assert.match(nanopore, /molbio_ngs_state_revision_id:\s*exactStateRevisionId/);
    assert.match(nanopore, /ngs_reference_revision_id:\s*selectedManagedReference\.revision\.id/);
    assert.doesNotMatch(nanopore, /params:\s*\{[\s\S]{0,300}reference_fasta:\s*effectiveReferencePath/);
    assert.match(nanopore, /untrusted import hints only/);

    assert.match(ontRouter, /class OntManagedReferenceRequest\(BaseModel\)/);
    assert.match(ontRouter, /managed_reference:\s*OntManagedReferenceRequest \| None/);
    assert.match(ontRouter, /resolve_managed_reference_for_launch/);

    assert.match(ngs, /fetchFullJob\(selectedJobId as string\)/);
    assert.match(ngs, /const selectedJob = fullJobQuery\.data && isNgsJob\(fullJobQuery\.data\) \? fullJobQuery\.data : null/);
    assert.match(ngs, /disabled=\{!fullJobQuery\.isSuccess/);
    assert.match(ngs, /updateQueryParams\(\{ job_id:/);
});

test('history, PCR, runs, and evidence remain immutable and typed', () => {
    const history = source('components/MolBioToolkit/panels/HistoryPanel.tsx');
    const pcr = source('components/MolBioToolkit/panels/PCRPanel.tsx');
    const toolkit = source('components/MolBioToolkit/MolBioToolkitV2.tsx');
    const instrument = source('components/ngs/OntInstrumentPanel.tsx');
    const workspace = source('components/molbio-ngs/DomainExperimentWorkspace.tsx');
    const pcrRouter = backend('routers/molbio_ops.py');

    assert.match(history, /Server immutable revision history/);
    assert.match(history, /Local edit\/undo history/);
    assert.match(toolkit, /Open latest editable version/);
    assert.match(toolkit, /isExactMolecularAuthority/);

    assert.match(pcr, /Persist immutable PCR revision/);
    assert.match(pcr, /fetchPcrExperimentRevision/);
    assert.match(pcr, /payload_sha256/);
    assert.match(pcrRouter, /payload_sha256:\s*str/);
    assert.match(pcrRouter, /pcr_experiment_revision_payload_sha256/);

    assert.match(instrument, /fetchOntInstrumentRuns/);
    assert.match(instrument, /fetchOntInstrumentRunGeneration/);
    assert.match(instrument, /Durable BMS ONT run ledger/);
    assert.match(workspace, /Immutable scientific evidence assessments/);
    assert.match(workspace, /Manifest integrity/);
    assert.match(workspace, /Job lifecycle/);
});

test('receipt-owned exact reopen uses validated aggregate/revision pairs and observed generation keys', () => {
    const workspace = source('components/molbio-ngs/DomainExperimentWorkspace.tsx');
    const instrument = source('components/ngs/OntInstrumentPanel.tsx');

    assert.match(workspace, /parseExactReceiptReopenDestination/);
    assert.match(workspace, /surface:\s*'molbio-sequence-revision'/);
    assert.match(workspace, /aggregateKey:\s*'sequence_id'/);
    assert.match(workspace, /surface:\s*'molbio-pcr-experiment-revision'/);
    assert.match(workspace, /aggregateKey:\s*'experiment_id'/);
    assert.match(workspace, /fetchMolecularRevision\(destination\.aggregateId, destination\.revisionId\)/);
    assert.match(workspace, /fetchPcrExperimentRevision\(destination\.aggregateId, destination\.revisionId\)/);
    assert.doesNotMatch(workspace, /fetchMolecularRevision\(member\.entity_id, member\.source_generation_or_revision\)/);
    assert.doesNotMatch(workspace, /fetchPcrExperimentRevision\(member\.entity_id, member\.source_generation_or_revision\)/);
    assert.match(workspace, /molbio_sequence_id:\s*destination\.aggregateId/);
    assert.match(workspace, /molbio_revision_id:\s*destination\.revisionId/);
    assert.match(workspace, /pcr_experiment_id:\s*destination\.aggregateId/);
    assert.match(workspace, /pcr_revision_id:\s*destination\.revisionId/);
    assert.match(workspace, /observed_generation:\s*String\(run\.observed_generation\)/);
    assert.doesNotMatch(workspace, /run_generation:/);

    for (const key of ['sample_id', 'sample_revision_id', 'reference_id', 'reference_revision_id']) {
        assert.match(instrument, new RegExp(`params\\.get\\('${key}'\\)`));
    }
    assert.match(instrument, /fetchMolBioNgsSampleRevision\([\s\S]{0,200}requestedSampleRevision\.resourceId[\s\S]{0,120}requestedSampleRevision\.revisionId/);
    assert.match(instrument, /fetchMolBioNgsReferenceRevision\([\s\S]{0,160}requestedReferenceRevision\.resourceId[\s\S]{0,120}requestedReferenceRevision\.revisionId/);
    assert.match(instrument, /Exact pinned sample revision/);
    assert.match(instrument, /Exact pinned reference revision/);
    assert.match(instrument, /Current-head browsing remains separate/);
});
