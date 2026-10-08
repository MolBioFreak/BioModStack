import assert from 'node:assert/strict';
import test from 'node:test';

for (const quality of ['hac', 'fast']) {
    test(`explicit quality ${quality} beats an injected hidden default in either key order`, () => {
        for (const params of [{ dorado_model: quality, dorado_quality_mode: 'sup' }, { dorado_quality_mode: 'sup', dorado_model: quality }]) {
            const values = buildNanoporeHandoff({ name: '', mode: 'basecall_dna', params, defaultOnlyParams: ['dorado_quality_mode'] }, '').values;
            assert.equal(values.doradoModel, quality);
            assert.equal(values.cloneRefusal, undefined);
        }
    });
}
test('genuinely explicit quality disagreements refuse even when one is sup', () => {
    const values = buildNanoporeHandoff({ name: '', mode: 'basecall_dna', params: { dorado_model: 'hac', dorado_quality_mode: 'sup' } }, '').values;
    assert.match(String(values.cloneRefusal), /one exact choice/);
});
test('alias-only duplex beats an injected canonical default', () => {
    const values = buildNanoporeHandoff({ name: '', mode: 'basecall_dna', params: { basecalling_mode: 'duplex', dorado_basecall_mode: 'simplex' }, defaultOnlyParams: ['dorado_basecall_mode'] }, '').values;
    assert.equal(values.doradoMode, 'duplex');
    assert.equal(values.cloneRefusal, undefined);
});

import { buildNanoporeHandoff } from '../src/lib/nanoporeHandoff';

test('unsaved generic handoff preserves pooled settings and exact URL context without clone authority', () => {
    const params = { fastq_path: '/inputs/edited.fastq', reference_set_manifest: '/inputs/frozen.json',
        pooled_assignment_min_mapq: 0, pooled_assignment_min_alignment_score_margin: 1234 };
    const handoff = buildNanoporeHandoff({ name: 'unsaved', mode: 'pooled_reference_assignment', params }, '?domain_experiment_id=domain&state_revision_id=exact&launch_context_id=prepared');
    assert.equal(handoff.to, '/ngs?domain_experiment_id=domain&state_revision_id=exact&launch_context_id=prepared&view=launch');
    assert.equal(handoff.values.selectedWorkflow, 'pooledAssignment');
    assert.equal(handoff.values.referenceSetManifest, params.reference_set_manifest);
    assert.equal(handoff.values.fastqPath, params.fastq_path);
    assert.equal(handoff.values.pooledAssignmentMinMapq, 0);
    assert.equal(handoff.values.pooledAssignmentMinAlignmentScoreMargin, 1234);
    assert.equal(handoff.values.cloneRefusal, undefined);
    assert.deepEqual(handoff.values.genericHandoffParams, params);
    assert.deepEqual(params, { fastq_path: '/inputs/edited.fastq', reference_set_manifest: '/inputs/frozen.json', pooled_assignment_min_mapq: 0, pooled_assignment_min_alignment_score_margin: 1234 });
});

test('current generic workflow beats stale saved ont_workflow_id without mutating the snapshot', () => {
    const params = { ont_workflow_id: 'ont_plasmid_qc' };
    const values = buildNanoporeHandoff({ name: '', mode: 'construct_screening', params }, '').values;
    assert.equal(values.selectedWorkflow, 'constructScreening');
    assert.deepEqual(values.genericHandoffParams, params);
});

test('all eight generic modes retain their workflow and false/zero expert values', () => {
    const modes = { basecall_dna: 'dna', basecall_rna: 'rna', plasmid_qc: 'plasmidQc', construct_screening: 'constructScreening',
        methylation_analysis: 'modified', fastq_qc: 'fastqQc', pooled_reference_assignment: 'pooledAssignment', clone_validation: 'clone' };
    for (const [mode, workflow] of Object.entries(modes)) {
        const values = buildNanoporeHandoff({ name: '', mode, params: { pinned_gpu: 2, trim_adapters: false, emit_summary: false,
            min_qscore: 0, run_assembly: false, wf_clone_primers: '/inputs/primer.fa', wf_clone_cutsite_mismatch: 0 } }, '').values;
        assert.equal(values.selectedWorkflow, workflow);
        assert.deepEqual(values.pinnedGpus, [2]);
        assert.equal(values.trimAdapters, false);
        assert.equal(values.emitSummary, false);
        assert.equal(values.minQscore, 0);
        assert.equal(values.runAssembly, false);
        assert.equal(values.wfClonePrimers, '/inputs/primer.fa');
        assert.equal(values.wfCloneCutsiteMismatch, 0);
    }
});

for (const mode of ['basecall_rna', 'basecall_dna']) {
    test(`${mode} owns injected molecule defaults but refuses explicit conflicts`, () => {
        const expected = mode === 'basecall_rna' ? 'rna' : 'dna';
        const conflict = expected === 'rna' ? 'dna' : 'rna';
        const params = { ont_molecule_type: conflict };
        const request = { name: '', mode, params };
        const defaults = buildNanoporeHandoff({ ...request, defaultOnlyParams: ['ont_molecule_type'] }, '').values;
        assert.equal(defaults.doradoMolecule, expected);
        assert.equal(defaults.cloneRefusal, undefined);
        assert.deepEqual(defaults.genericHandoffParams, params);
        const explicit = buildNanoporeHandoff(request, '').values;
        assert.equal(explicit.doradoMolecule, conflict);
        assert.match(String(explicit.cloneRefusal), /ont_molecule_type/);
    });
}
