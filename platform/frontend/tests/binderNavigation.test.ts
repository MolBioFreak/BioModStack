// Producer-shaped synthetic transport fixtures, not scientific publication verification.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/lib/api';
import { fetchBinderCandidateData, fetchBinderNavigationFamily, fetchBinderResultFamily, validationKind, type BinderNavigationJob } from '../src/lib/binderResultFamily';
import { fetchBC2TrajectoryNames, type BC2Navigation } from '../src/lib/bindcraft2Results';
const job = (id: string, extra: Partial<BinderNavigationJob> = {}): BinderNavigationJob => ({ id, name: id, model_id: 'bindcraft2', status: 'completed', mode: 'native', created_at: '', params: {}, ...extra });
const native = 'design_l3_abcdef_candidate2';
const source = job('source');
const child = job('child', { model_id: 'protenix', provenance: { binder_round_step: { source_ref: { job_id: source.id, native_record_id: native }, input_components: [{ sequence: 'AAA', role: 'binder' }, { sequence: 'TTT', role: 'target' }] } } });
const nav: BC2Navigation = { job_id: source.id, arm: 'arm / A', trajectories: [{ design: 'family', trajectory: '0061' }, { design: 'missing', trajectory: null }], draws: [{ design: native, arm: 'arm / A', trajectory_design: 'family', sequence: 'AAA', structures: [{ artifact_id: 'complex/exact', target_state: 'target A', binder_chains: 'B', target_chains: ['A'] }] }, ...Array.from({ length: 104 }, (_, n) => ({ design: `rejected-${n}`, arm: 'arm / A', trajectory_design: null, sequence: null, structures: [] }))], total: 105 };

test('compact metadata and paged family feed one navigation read; fresh children/status and full transport remain independent', async () => {
    const adapter = api.defaults.adapter, fetcher = globalThis.fetch;
    const signal = new AbortController().signal;
    const calls: any[] = [], reads: string[] = [];
    let family = [source, child];
    api.defaults.adapter = async config => {
        calls.push(config); assert.equal(config.signal, signal);
        if (config.url === '/api/jobs/source') { assert.equal(config.params.projection, 'binder_navigation'); return { data: source, status: 200, statusText: 'OK', headers: {}, config }; }
        assert.equal(config.url, '/api/jobs'); assert.equal(config.params.include_children, true);
        assert.equal(config.params.scientific_family_job_id, 'source');
        const compact = config.params.projection === 'binder_navigation';
        if (!compact) assert.equal(config.params.summary, false);
        const all = compact ? family : family.map(j => ({ ...j, design_count: 17, output_dir: '/full', provenance: { receipt: 'full-only' } }));
        return { data: { jobs: all.slice(config.params.offset, config.params.offset + 1), total: all.length }, status: 200, statusText: 'OK', headers: {}, config };
    };
    globalThis.fetch = async (input, init) => { reads.push(String(input)); assert.equal(init?.signal, signal); assert.equal(String(input), '/api/jobs/source/bindcraft2-navigation'); return new Response(JSON.stringify(nav)); };
    try {
        const data = await fetchBinderCandidateData([{ id: 'source', label: 'Site A' }], signal);
        assert.equal(data.sources[0].draws.length, 105); assert.equal(reads.length, 1);
        assert.deepEqual(calls.filter(c => c.url === '/api/jobs').map(c => c.params.offset), [0, 1]);
        const candidate = data.candidates[0]; assert.equal(candidate.familyLabel, 'Design 0061'); assert.equal(candidate.sourceLabel, 'Site A');
        const url = new URL(candidate.href, 'http://test');
        assert.deepEqual(JSON.parse(url.searchParams.get('native_scope')!), ['bindcraft2', 'arm / A', 'draw']);
        assert.deepEqual(JSON.parse(url.searchParams.get('native_candidate')!), ['arm / A', 'draw', native]);
        assert.equal(url.searchParams.get('artifact_id'), 'complex/exact'); assert.equal(url.searchParams.get('target_state'), 'target A');
        assert.equal(candidate.attempts[0].href, '/designs/child?include_children=false&view=structure');
        assert.equal(candidate.attempts[0].kind, 'monomer');
        family = [source, { ...child, status: 'failed' }, job('late', { selection_source_job_id: 'child' })];
        const fresh = await fetchBinderCandidateData([{ id: 'source', label: '' }], signal);
        assert.deepEqual(fresh.candidates[0].attempts.map(a => [a.id, a.status]), [['child', 'failed'], ['late', 'completed']]);
        assert.equal(fresh.sources[0].label, 'BC2 source 1'); assert.equal(reads.length, 2);
        globalThis.fetch = async () => new Response(JSON.stringify({ ...nav, draws: [{ ...nav.draws[0], structures: [...nav.draws[0].structures, { ...nav.draws[0].structures[0], artifact_id: 'second-complex' }] }], total: 1 }));
        const ambiguous = await fetchBinderCandidateData([{ id: 'source', label: '' }], signal);
        assert.equal(new URL(ambiguous.candidates[0].href, 'http://test').searchParams.has('artifact_id'), false);
        globalThis.fetch = async () => new Response(JSON.stringify({ ...nav, draws: [], total: 0 }));
        const zero = await fetchBinderCandidateData([{ id: 'source', label: '' }], signal);
        assert.equal(zero.supported, true); assert.deepEqual(zero.candidates, []); assert.deepEqual(zero.errors, []);
        const full = await fetchBinderResultFamily('source', signal); assert.equal(full[0].design_count, 17);
        const compact = await fetchBinderNavigationFamily('source', signal); assert.equal('design_count' in compact[0], false);
    } finally { api.defaults.adapter = adapter; globalThis.fetch = fetcher; }
});

test('ordinary names preserve native formatting, explicit arms and cancellation', async () => {
    const original = globalThis.fetch, controller = new AbortController();
    globalThis.fetch = async (input, init) => { assert.equal(String(input), '/api/jobs/source%2Fid/bindcraft2-navigation?arm=arm+%2F+A'); assert.equal(init?.signal, controller.signal); return new Response(JSON.stringify(nav)); };
    try {
        assert.deepEqual(await fetchBC2TrajectoryNames('source/id', 'arm / A', controller.signal), { family: 'Design 0061' });
        globalThis.fetch = async () => { controller.abort(); throw new Error('cancelled'); };
        await assert.rejects(fetchBC2TrajectoryNames('source', null, controller.signal), /cancelled/);
    } finally { globalThis.fetch = original; }
});

test('partial errors and unsupported sources stay distinct; navigation abort propagates', async () => {
    const adapter = api.defaults.adapter, original = globalThis.fetch;
    api.defaults.adapter = async config => ({ data: config.url === '/api/jobs' ? { jobs: [source, child], total: 2 } : config.url?.endsWith('/unsupported') ? job('unsupported', { model_id: 'other' }) : source, status: 200, statusText: 'OK', headers: {}, config });
    globalThis.fetch = async () => new Response('', { status: 409 });
    try {
        assert.equal((await fetchBinderCandidateData([{ id: 'unsupported', label: '' }])).supported, false);
        const partial = await fetchBinderCandidateData([{ id: 'source', label: 'Site A' }]);
        assert.equal(partial.supported, true); assert.deepEqual(partial.errors, ['Site A: Native records unavailable (409)']);
        const controller = new AbortController();
        globalThis.fetch = async () => { controller.abort(); throw new Error('cancelled'); };
        await assert.rejects(fetchBinderCandidateData([{ id: 'source', label: '' }], controller.signal), /cancelled/);
        api.defaults.adapter = async () => { throw new Error('offline'); };
        await assert.rejects(fetchBinderCandidateData([{ id: 'missing', label: '' }]), /metadata unavailable/);
    } finally { api.defaults.adapter = adapter; globalThis.fetch = original; }
});

test('malformed component evidence remains an honest classification fallback', () => {
    for (const params of [null, [], 'wrong', { complex_components: [{ sequence: 'AAA', count: '3' }] }]) {
        assert.equal(validationKind(job('bad', { params, provenance: { binder_round_step: null } }), 'AAA', new Set(['TTT'])), 'other');
    }
});
