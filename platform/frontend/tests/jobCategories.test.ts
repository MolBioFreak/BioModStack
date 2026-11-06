import assert from 'node:assert/strict';
import test from 'node:test';
import { isCategoryVisible, jobCategory } from '../src/lib/jobCategories';

const jobs = {
    ngs: { model_id: 'ont_fastq_qc', mode: 'read' },
    protein: { model_id: ' FAMILY_MSA_PROTEIN ', mode: 'family_msa' },
    nucleic: { model_id: 'family_msa_nucleic', mode: 'family_msa' },
    folding: { model_id: 'msa_batch', mode: 'msa_generation' },
    other: { model_id: 'boltz2', mode: 'prediction' },
};

test('category identity separates family alignment, NGS and folding MSA', () => {
    assert.equal(jobCategory(jobs.ngs), 'ngs');
    assert.equal(jobCategory(jobs.protein), 'alignments');
    assert.equal(jobCategory(jobs.nucleic), 'alignments');
    assert.equal(jobCategory(jobs.folding), 'other');
    assert.equal(jobCategory(jobs.other), 'other');
    assert.equal(jobCategory({ model_id: '', mode: 'fastq_qc' }), 'ngs');
});

test('both categories can be toggled independently', () => {
    for (const ngs of [false, true]) {
        for (const alignments of [false, true]) {
            const visibility = { ngs, alignments };
            assert.equal(isCategoryVisible(jobs.ngs, visibility), ngs);
            assert.equal(isCategoryVisible(jobs.protein, visibility), alignments);
            assert.equal(isCategoryVisible(jobs.nucleic, visibility), alignments);
            assert.equal(isCategoryVisible(jobs.folding, visibility), true);
            assert.equal(isCategoryVisible(jobs.other, visibility), true);
        }
    }
});
