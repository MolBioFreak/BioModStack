import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const repoRoot = resolve(process.cwd(), '..', '..');
const jobSubmissionSource = readFileSync(resolve(process.cwd(), 'src/components/JobSubmission.tsx'), 'utf8');
const confornetsTemplatePath = resolve(repoRoot, 'platform/api/config/templates/confornets_experimental.yaml');
const esmfold2TemplatePath = resolve(repoRoot, 'platform/api/config/templates/esmfold2_experimental.yaml');
const esmfold2ModelPath = resolve(repoRoot, 'platform/api/config/models/esmfold2_experimental.yaml');

function hardcodedWorkflowBlock(): string {
    const start = jobSubmissionSource.indexOf('const hardcodedWorkflowTemplates');
    assert.notEqual(start, -1, 'hardcodedWorkflowTemplates block should exist');
    const end = jobSubmissionSource.indexOf('const visibleApiTemplates', start);
    assert.notEqual(end, -1, 'hardcodedWorkflowTemplates block end should exist');
    return jobSubmissionSource.slice(start, end);
}

test('Conformational mapping workflow is API-template driven, main-page exposed, and hidden from raw model picker', () => {
    assert.match(
        jobSubmissionSource,
        /confornets_experimental['"]\)\s*return\s*['"]CN['"]/,
        'Conformational mapping should have a stable CN badge for the workflow card',
    );
    assert.match(
        jobSubmissionSource,
        /confornets_experimental/,
        'JobSubmission should explicitly know to hide/label the ConforNets-backed workflow',
    );
    assert.match(
        jobSubmissionSource,
        /visibleApiTemplates\.filter\(\(t(?:: [^)]+)?\) => !t\.experimental\)/,
        'main workflow cards should include non-experimental API templates',
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
        'Conformational mapping should not be duplicated as a frontend hardcoded workflow card',
    );
});

test('Conformational mapping template exposes monomer-only workflow copy, docs default, and no complex task values', () => {
    const templateYaml = readFileSync(confornetsTemplatePath, 'utf8');

    assert.match(templateYaml, /^id:\s*confornets_experimental/m);
    assert.match(templateYaml, /^experimental:\s*false/m);
    assert.match(templateYaml, /template_model_id:\s*confornets_experimental/);
    assert.match(templateYaml, /template_mode_id:\s*design/);
    assert.match(templateYaml, /workflow_model_topic:\s*confornets/);
    assert.match(templateYaml, /^name:\s*Conformational Mapping Experimental/m);
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
    assert.match(jobSubmissionSource, /data-bms-workflow-doc-hover="true"/);
    assert.doesNotMatch(jobSubmissionSource, /data-bms-workflow-doc-table="true"/);
    assert.doesNotMatch(jobSubmissionSource, /Docs available/);
    assert.doesNotMatch(jobSubmissionSource, /Hide docs/);

    const taskParamStart = templateYaml.indexOf('name: task');
    assert.notEqual(taskParamStart, -1, 'task user param should exist');
    const taskParamEnd = templateYaml.indexOf('\n  - name:', taskParamStart + 1);
    const taskParam = templateYaml.slice(taskParamStart, taskParamEnd === -1 ? undefined : taskParamEnd);
    assert.match(taskParam, /- diversity/);
    assert.match(taskParam, /- mse/);
    assert.match(taskParam, /- transfer/);
    assert.doesNotMatch(taskParam, /complex|ligand_binder|nucleic_binder|multimer/);
});

test('ESMFold2 model config exposes runtime settings while the template delegates UI to Structure Prediction', () => {
    const templateYaml = readFileSync(esmfold2TemplatePath, 'utf8');
    const modelYaml = readFileSync(esmfold2ModelPath, 'utf8');

    assert.match(templateYaml, /^id:\s*esmfold2_experimental/m);
    assert.match(templateYaml, /^experimental:\s*true/m);
    assert.match(templateYaml, /template_model_id:\s*esmfold2_experimental/);
    assert.match(templateYaml, /template_mode_id:\s*predict/);
    assert.match(templateYaml, /structure_launch_variant:\s*esmfold2_experimental/);
    assert.match(templateYaml, /model_variant:\s*fast/);
    assert.match(templateYaml, /user_params:\s*\[\]/, 'template should not render a duplicate generic ESMFold2 form');
    assert.doesNotMatch(
        templateYaml,
        /preset_params:[\s\S]*?esmf_/,
        'preset_params must not contain prefixed ESMFold2 defaults that shadow canonical launcher fields',
    );

    for (const name of [
        'sequence',
        'sequence_name',
        'chain_id',
        'pdb_sequence_path',
        'pdb_chain_ids',
        'msa_path',
        'msa_format',
        'msa_max_sequences',
        'msa_remove_insertions',
        'dna_sequence',
        'dna_chain_id',
        'rna_sequence',
        'rna_chain_id',
        'ligand_smiles',
        'ligand_ccd',
        'ligand_chain_id',
        'complex_components_json',
        'model_variant',
        'model_id_or_path',
        'local_files_only',
        'quality_preset',
        'num_loops',
        'num_sampling_steps',
        'num_diffusion_samples',
        'seed',
        'device',
    ]) {
        assert.match(modelYaml, new RegExp(`name:\\s*${name}\\b`), `${name} should remain in the ESMFold2 runtime model config`);
    }

    assert.match(modelYaml, /name:\s*sequence[\s\S]*?required:\s*false/);
    assert.match(modelYaml, /name:\s*pdb_sequence_path[\s\S]*?preset_type:\s*pdb[\s\S]*?file_type:\s*pdb/);
    assert.match(modelYaml, /name:\s*msa_path[\s\S]*?file_type:\s*a3m/);
    assert.match(modelYaml, /name:\s*dna_sequence[\s\S]*?preset_type:\s*dna/);
    assert.match(modelYaml, /name:\s*rna_sequence[\s\S]*?preset_type:\s*rna/);
    assert.match(modelYaml, /name:\s*complex_components_json[\s\S]*?type:\s*textarea/);
    assert.match(modelYaml, /name:\s*model_variant[\s\S]*?type:\s*string[\s\S]*?enum:[\s\S]*?- fast[\s\S]*?- full/);
    assert.match(modelYaml, /name:\s*device[\s\S]*?type:\s*string[\s\S]*?default:\s*auto[\s\S]*?enum:[\s\S]*?- auto[\s\S]*?- cuda[\s\S]*?- cpu/);
    assert.match(modelYaml, /name:\s*local_files_only[\s\S]*?type:\s*boolean[\s\S]*?default:\s*true/);
    assert.match(modelYaml, /name:\s*quality_preset[\s\S]*?type:\s*string[\s\S]*?default:\s*standard[\s\S]*?smoke[\s\S]*?thorough[\s\S]*?custom/);
    assert.match(modelYaml, /name:\s*model_id_or_path[\s\S]*?default:\s*""/);
    assert.doesNotMatch(
        modelYaml,
        /name:\s*model_id_or_path[\s\S]*?default:\s*biohub\/ESMFold2-Fast/,
        'model path field should not pin Fast when the user switches the variant to full',
    );

    for (const name of ['num_loops', 'num_sampling_steps', 'num_diffusion_samples']) {
        assert.match(
            modelYaml,
            new RegExp(`name:\\s*${name}[\\s\\S]*?type:\\s*integer[\\s\\S]*?minimum:\\s*1`),
            `${name} should stay bounded in the runtime model config`,
        );
    }
    assert.match(jobSubmissionSource, /data-bms-template-slider="compact"/);
    assert.match(jobSubmissionSource, /data-bms-template-textarea="raw"/);
    assert.match(jobSubmissionSource, /ESMFOLD2_QUALITY_PRESETS/);
    assert.match(jobSubmissionSource, /esmfold2HasInputSource/);
    assert.match(jobSubmissionSource, /pdb_sequence_path/);
    assert.match(jobSubmissionSource, /pdb_chain_ids/);
    assert.match(jobSubmissionSource, /sequenceUnit/);
    assert.match(jobSubmissionSource, /missingRequiredTemplateParams/);
    assert.match(jobSubmissionSource, /templateLaunchName/);

    const modelFilterStart = jobSubmissionSource.indexOf('const models = (modelsData?.data ?? []).filter');
    assert.notEqual(modelFilterStart, -1, 'manual model filter should exist');
    const modelFilterEnd = jobSubmissionSource.indexOf('const selectedModel', modelFilterStart);
    assert.notEqual(modelFilterEnd, -1, 'manual model filter block end should exist');
    const modelFilter = jobSubmissionSource.slice(modelFilterStart, modelFilterEnd);
    assert.match(modelFilter, /esmfold2_experimental/, 'raw ESMFold2 model should stay out of Advanced Models');
    assert.match(jobSubmissionSource, /esmfold2_experimental['"]\)\s*return\s*['"]EF['"]/, 'ESMFold2 should have a stable EF experimental card badge');
});
