import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const repoRoot = resolve(process.cwd(), '..', '..');
const jobSubmissionSource = readFileSync(resolve(process.cwd(), 'src/components/JobSubmission.tsx'), 'utf8');
const confornetsTemplatePath = resolve(repoRoot, 'platform/api/config/templates/confornets_experimental.yaml');

function hardcodedWorkflowBlock(): string {
    const start = jobSubmissionSource.indexOf('const hardcodedWorkflowTemplates');
    assert.notEqual(start, -1, 'hardcodedWorkflowTemplates block should exist');
    const end = jobSubmissionSource.indexOf('const visibleApiTemplates', start);
    assert.notEqual(end, -1, 'hardcodedWorkflowTemplates block end should exist');
    return jobSubmissionSource.slice(start, end);
}

test('ConforNets experimental workflow is API-template driven and hidden from raw model picker', () => {
    assert.match(
        jobSubmissionSource,
        /confornets_experimental['"]\)\s*return\s*['"]CN['"]/,
        'ConforNets should have a stable CN badge for the experimental card',
    );
    assert.match(
        jobSubmissionSource,
        /confornets_experimental/,
        'JobSubmission should explicitly know to hide/label ConforNets experimental workflow',
    );
    assert.match(
        jobSubmissionSource,
        /visibleApiTemplates\.filter\(\(t(?:: any)?\) => t\.experimental\)/,
        'experimental cards should still come from the API template registry',
    );

    const modelFilterStart = jobSubmissionSource.indexOf('const models = (modelsData?.data ?? []).filter');
    assert.notEqual(modelFilterStart, -1, 'manual model filter should exist');
    const modelFilterEnd = jobSubmissionSource.indexOf('const selectedModel', modelFilterStart);
    assert.notEqual(modelFilterEnd, -1, 'manual model filter block end should exist');
    const modelFilter = jobSubmissionSource.slice(modelFilterStart, modelFilterEnd);
    assert.match(modelFilter, /confornets_experimental/, 'raw ConforNets model should stay out of Advanced Models');

    assert.doesNotMatch(
        hardcodedWorkflowBlock(),
        /confornets_experimental/,
        'ConforNets should not be duplicated as a frontend hardcoded workflow card',
    );
});

test('ConforNets template exposes monomer-only experimental copy and no complex task values', () => {
    const templateYaml = readFileSync(confornetsTemplatePath, 'utf8');

    assert.match(templateYaml, /^id:\s*confornets_experimental/m);
    assert.match(templateYaml, /^experimental:\s*true/m);
    assert.match(templateYaml, /template_model_id:\s*confornets_experimental/);
    assert.match(templateYaml, /template_mode_id:\s*design/);
    assert.match(templateYaml.toLowerCase(), /monomer/);
    assert.match(templateYaml.toLowerCase(), /single-chain/);
    assert.match(templateYaml.toLowerCase(), /of3p2|openfold3/);
    assert.match(templateYaml.toLowerCase(), /two reference|2 reference/);
    assert.match(templateYaml, /name:\s*chain_id[\s\S]*?type:\s*enum/, 'chain ID should be a dropdown, not a raw text box');
    assert.match(templateYaml, /name:\s*save_steps[\s\S]*?type:\s*enum/, 'save steps should use presets, not a comma-entry blank box');
    assert.match(templateYaml, /name:\s*source_test_cases[\s\S]*?type:\s*enum/, 'transfer source case should use a dropdown/preset');
    assert.doesNotMatch(templateYaml, /type:\s*string/, 'ConforNets template should avoid generic string blank boxes');
    for (const name of ['num_runs', 'k_confornets', 'num_samples', 'max_steps', 'num_recycles', 'num_diffusion_steps']) {
        assert.match(
            templateYaml,
            new RegExp(`name:\\s*${name}[\\s\\S]*?ui_control:\\s*slider[\\s\\S]*?step:`),
            `${name} should render as a slider with a defined step`,
        );
    }

    assert.match(jobSubmissionSource, /param\.type === ['"]boolean['"]/);
    assert.match(jobSubmissionSource, /param\.ui_control === ['"]slider['"]/);
    assert.match(jobSubmissionSource, /type=['"]range['"]/);

    const taskParamStart = templateYaml.indexOf('name: task');
    assert.notEqual(taskParamStart, -1, 'task user param should exist');
    const taskParamEnd = templateYaml.indexOf('\n  - name:', taskParamStart + 1);
    const taskParam = templateYaml.slice(taskParamStart, taskParamEnd === -1 ? undefined : taskParamEnd);
    assert.match(taskParam, /- diversity/);
    assert.match(taskParam, /- mse/);
    assert.match(taskParam, /- transfer/);
    assert.doesNotMatch(taskParam, /complex|ligand_binder|nucleic_binder|multimer/);
});
