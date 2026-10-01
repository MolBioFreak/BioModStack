import assert from 'node:assert/strict';
import test from 'node:test';
import { api, fetchJobs } from '../src/lib/api.ts';

test('compact summary restores legacy empty detail defaults and preserves explicit values and conditional identity', async () => {
    const original = api.defaults.adapter;
    let conditional = false;
    const wire = { id: 'row', name: 'summary', status: 'completed', model_id: 'external_import',
        mode: 'structure_import', design_count: 0, output_dir: null, awaiting_input: false,
        pinned_gpu: 0, source_selection_count: 0, execution_stages: [] };
    api.defaults.adapter = async config => ({ status: conditional ? 304 : 200, statusText: '',
        config, headers: { etag: '"summary"' }, data: conditional ? '' : { jobs: [wire], total: 1 } });
    try {
        const response = await fetchJobs({ summary: true });
        const job = response.data.jobs[0];
        assert.deepEqual(job.params, {});
        assert.equal(job.provenance, null);
        assert.equal(job.saved_selection_sets, null);
        assert.deepEqual(job.stage_outputs, {});
        assert.deepEqual(job.awaiting_payload, {});
        assert.deepEqual(job.decision_history, []);
        assert.equal(job.source_structure, null);
        assert.equal(job.sequence_design, null);
        assert.equal(job.binder_round, null);
        assert.equal(job.requested_design_count, null);
        assert.equal(job.selected_loop_scope, null);
        assert.equal(job.source_selection_manifest_path, null);
        assert.equal(job.assigned_gpu, null);
        assert.equal(job.vram_estimate_mb, null);
        assert.equal(job.launch_context_id, null);
        assert.deepEqual(job.result_summary, { stage_id: null, state: 'unavailable', partial: false,
            requested_count: null, generated_count: null, rejected_count: null, failed_count: null,
            unevaluable_count: null, expected_publication_count: null, persisted_count: null,
            reason: null, dispositions: null });
        assert.equal(job.awaiting_input, false);
        assert.equal(job.design_count, 0);
        assert.equal(job.pinned_gpu, 0);
        assert.equal(job.source_selection_count, 0);
        assert.equal(job.output_dir, null);
        assert.equal('params' in wire, false, 'transport does not mutate the wire DTO');
        conditional = true;
        const unchanged = await fetchJobs({ summary: true }, response);
        assert.equal(unchanged.data, response.data);
        conditional = false;
        const full = await fetchJobs({ summary: false });
        assert.equal(full.data.jobs[0], wire, 'complete legacy responses are not normalized');
    } finally { api.defaults.adapter = original; }
});

test('rolling old-server detail defaults and scientific values are not overwritten', async () => {
    const original = api.defaults.adapter;
    const wire = { id: 'old', params: { zero: 0, false: false, null: null }, provenance: { retained: true },
        stage_outputs: { stage: ['native.pdb'] }, awaiting_payload: { resume_direct: false },
        decision_history: [{ retained: true }], result_summary: { state: 'valid', partial: false } };
    api.defaults.adapter = async config => ({ status: 200, statusText: '', config,
        headers: {}, data: { jobs: [wire], total: 1 } });
    try {
        const response = await fetchJobs();
        for (const key of ['params', 'provenance', 'stage_outputs', 'awaiting_payload', 'decision_history', 'result_summary'] as const) {
            assert.equal(response.data.jobs[0][key], wire[key]);
        }
    } finally { api.defaults.adapter = original; }
});
