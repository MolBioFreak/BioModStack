import assert from 'node:assert/strict';
import test from 'node:test';
import { buildNanoporeOperatorStageParams } from '../src/lib/nanoporeLaunchPayload';

test('NGS operator stage payload preserves independent Construct Screening assembly and FASTQ QC settings', () => {
    assert.deepEqual(buildNanoporeOperatorStageParams({
        selectedWorkflow: 'constructScreening',
        inputSource: 'fastq',
        runFastqQc: false,
        runAssembly: false,
    }), { run_fastq_qc: false, run_assembly: false });

    assert.deepEqual(buildNanoporeOperatorStageParams({
        selectedWorkflow: 'constructScreening',
        inputSource: 'fastq',
        runFastqQc: true,
        runAssembly: true,
    }), { run_fastq_qc: true, run_assembly: true });
});

test('NGS operator stage payload keeps workflow-owned rules explicit and omits inapplicable settings', () => {
    assert.deepEqual(buildNanoporeOperatorStageParams({
        selectedWorkflow: 'clone',
        inputSource: 'fastq',
        runFastqQc: false,
        runAssembly: false,
    }), { run_fastq_qc: false, run_assembly: true });

    assert.deepEqual(buildNanoporeOperatorStageParams({
        selectedWorkflow: 'bamQc',
        inputSource: 'bam',
        runFastqQc: false,
        runAssembly: false,
    }), { run_fastq_qc: false });

    assert.deepEqual(buildNanoporeOperatorStageParams({
        selectedWorkflow: 'dna',
        inputSource: 'pod5',
        runFastqQc: true,
        runAssembly: true,
    }), {});
});
