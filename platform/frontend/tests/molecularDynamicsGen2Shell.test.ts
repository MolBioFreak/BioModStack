import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it } from 'node:test';

const source = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');

describe('Molecular Dynamics Gen 2 shell compatibility', () => {
    it('preserves the source Job identity when cloning MD so the server can reopen its immutable input', () => {
        const dashboard = source('src/components/Dashboard.tsx');
        assert.match(dashboard, /source_job_id:\s*detailedJob\.id/u);
    });

    it('routes MD clones to the dedicated Gen 2 launcher and never to the generic manual form', () => {
        const submission = source('src/components/JobSubmission.tsx');
        assert.match(submission, /data\.model_id === 'molecular_dynamics'/u);
        assert.match(submission, /setSelectedTemplateId\('molecular_dynamics'\)/u);
        assert.match(submission, /source_job_id:\s*data\.source_job_id/u);
    });

    it('uses resolvable external engine references instead of a raw repository markdown path', () => {
        const registry = source('src/components/modelDocumentationRegistry.ts');
        assert.doesNotMatch(registry, /href:\s*'\/docs\/[^']+\.md'/u);
        assert.match(registry, /GROMACS 2025\.3 manual/u);
        assert.match(registry, /OpenMM user guide/u);
    });

    it('passes Project launch context and canonical Structure Prediction handoff into the MD launcher', () => {
        const submission = source('src/components/JobSubmission.tsx');
        assert.match(submission, /launchContextId=\{launchContextId\}/u);
        assert.match(submission, /onOpenStructurePrediction=\{openMdStructurePrediction\}/u);
        assert.match(submission, /const openMdStructurePrediction/u);
        assert.match(submission, /setSelectedTemplateId\('structure_prediction'\)/u);
    });
});
