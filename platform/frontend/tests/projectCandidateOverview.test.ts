// Synthetic native-contract fixtures: no private campaign identities.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildProjectCandidateData, candidateHref, projectSourceReferences, validationKind, type CandidateSource } from '../src/lib/projectCandidateOverview';
import type { Job } from '../src/lib/api';
const job = (id: string, extra: Partial<Job> = {}): Job => ({ id, name: 'Misleading trimer title', model_id: 'protenix', status: 'completed', mode: 'complex', params: {}, created_at: '', design_count: 1, output_dir: null, ...extra });
const nativeId = 'design_l3_abcdef0123456789_candidate2';
const source: CandidateSource = { id: 'source-a', label: 'Site A', job: job('source-a', { model_id: 'bindcraft2' }), draws: [{ design: nativeId, trajectory_design: 'backbone-a', sequence: 'AAA', arm: null }] };
const validation = job('mono', { provenance: { binder_round_step: { source_ref: { job_id: source.id, native_record_id: nativeId }, input_components: [{ role: 'binder', sequence: 'AAA' }, { role: 'target', sequence: 'TTT' }] } } });
test('uses project references, not narrative links', () => {
    assert.deepEqual(projectSourceReferences({ payload: { external_references: [{ value: '/designs/source-a', label: 'Site A' }, { value: 'https://example.org/designs/no' }] }, description: '[wrong](/designs/wrong)' }), [{ id: 'source-a', label: 'Site A' }]);
});
test('exact scope identity and same-name candidates stay source-separated', () => {
    const b = { ...source, id: 'source-b' };
    const second = job('other-source', { provenance: { binder_round_step: { source_ref: { job_id: b.id, native_record_id: nativeId } } } });
    const data = buildProjectCandidateData([source, b], [validation, second]);
    assert.equal(data.candidates.length, 2);
    for (const c of data.candidates) {
        const url = new URL(c.href, 'http://local');
        assert.equal(url.pathname, '/designs/' + c.sourceId);
        assert.deepEqual(JSON.parse(url.searchParams.get('native_scope')!), ['bindcraft2', null, 'draw']);
        assert.deepEqual(JSON.parse(url.searchParams.get('native_candidate')!), [null, 'draw', nativeId]);
    }
    assert.equal(candidateHref(source.id, nativeId), data.candidates[0].href);
});
test('roles classify monomer, copy counts classify trimers; titles never classify', () => {
    assert.equal(validationKind(validation, 'AAA', new Set()), 'monomer');
    for (const [count, expected] of [[1, 'trimer1'], [3, 'trimer3']] as const) {
        const j = job('copies', { params: { complex_components: [{ sequence: 'AAA', count }, { sequence: 'TTT', count: 3 }] } });
        assert.equal(validationKind(j, 'AAA', new Set(['TTT'])), expected);
        assert.equal(validationKind(j, 'AAA', new Set()), 'other');
    }
});
test('retains failed retries, verifies older exact native sequence names, rejects unknowns', () => {
    const retry = job('failed-retry', { status: 'failed', provenance: { binder_round_step: { retry_of_job_id: 'mono' } } });
    const old = job('older', { selection_source_job_id: source.id, params: { selection_source_type: 'selected_native_sequences', sequence_name: nativeId, complex_components: [{ sequence: 'AAA' }, { sequence: 'TTT', count: 3 }] } });
    const unknown = job('unknown', { selection_source_job_id: source.id, params: { sequence_name: 'not-a-native-candidate' } });
    const data = buildProjectCandidateData([source], [validation, retry, old, unknown]);
    assert.equal(data.candidates.length, 1);
    assert.equal(data.unlinkedJobCount, 1);
    assert.deepEqual(data.candidates[0].attempts.map(a => [a.id, a.kind, a.href]), [['mono', 'monomer', '/designs/mono?include_children=false'], ['failed-retry', 'other', '/jobs/failed-retry'], ['older', 'trimer1', '/designs/older?include_children=false']]);
});
test('natural variants and failed-only candidates are retained without scores', () => {
    const ids = [10, 2].map(n => nativeId.replace('candidate2', `candidate${n}`));
    const s = { ...source, draws: ids.map(design => ({ design, sequence: 'AAA', trajectory_design: 'backbone-a' })) };
    const data = buildProjectCandidateData([s], ids.map((id, i) => job(String(i), { status: 'failed', provenance: { binder_round_step: { source_ref: { job_id: s.id, native_record_id: id } } } })));
    assert.deepEqual(data.candidates.map(c => c.variant), ['Variant 2', 'Variant 10']);
});
