import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

function readSource(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

test('Nanopore settings state is the authority for the submitted launch payload', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    const payload = readSource('src/lib/nanoporeLaunchPayload.ts');

    assert.match(template, /\.\.\.buildNanoporeOperatorStageParams\(\{/u);
    assert.match(payload, /params\.run_fastq_qc = runFastqQc/u);
    assert.match(payload, /if \(selectedWorkflow === 'clone'\) params\.run_assembly = true/u);
    assert.match(payload, /if \(selectedWorkflow === 'constructScreening'\) params\.run_assembly = runAssembly/u);
    assert.match(template, /onChange=\{\(e\) => setRunFastqQc\(e\.target\.checked\)\}/u);
    assert.match(template, /if \(selectedWorkflow === 'constructScreening'\) setRunAssembly\(e\.target\.checked\)/u);
    assert.doesNotMatch(template, /run_multimer_qc\s*:/u);
});

test('Nanopore settings expose the supported combinations without fake mutual exclusion', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');
    const workflowChooser = readSource('src/components/ngs/NanoporeWorkflowChooser.tsx');

    assert.match(template, /const fastqQcSettingAvailable =/u);
    assert.match(template, /selectedWorkflow === 'clone' \|\| selectedWorkflow === 'constructScreening'/u);
    assert.match(template, /Consensus assembly \(wf-clone-validation\)/u);
    assert.match(template, /FASTQ plasmid QC/u);
    assert.doesNotMatch(template, /if \(value\) setRunAssembly\(false\)/u);
    assert.doesNotMatch(template, /if \(value && inputSource === 'fastq'\) setRunFastqQc\(false\)/u);
    assert.match(workflowChooser, /key: 'constructScreening'/u);
});

test('Construct Screening assembly authority reaches API normalization and Nextflow execution', () => {
    const contract = readSource('../../platform/api/services/ont_ngs_contract.py');
    const workflow = readSource('../../workflows/ngs/ont_construct_screening.nf');

    assert.match(contract, /"ont_construct_screening": \{[\s\S]*?"run_assembly": False/u);
    assert.match(contract, /if canonical_id == "ont_construct_screening":/u);
    assert.match(contract, /normalized\["run_assembly"\] = run_assembly/u);
    assert.match(workflow, /def runAssembly = params\.run_assembly == true/u);
    assert.match(workflow, /if \(runAssembly\) \{/u);
    assert.doesNotMatch(workflow, /run_multimer_qc/u);
});
