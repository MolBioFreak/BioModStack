import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import {
    api, fetchMolBioNgsSummaries, fetchPrimers, fetchProjectHub,
    fetchMolecularRevisionSummaries, fetchPcrExperimentSummaryPage,
} from '../src/lib/api';

const originalAdapter = api.defaults.adapter;
afterEach(() => { api.defaults.adapter = originalAdapter; });

test('hub continuation retains exact state and forwards independent section cursors', async () => {
    const abort = new AbortController();
    const fixture = { schema: 'bms.project-hub.v1', pages: {
        members: { next_cursor: 'member-next', has_more: true, total_count: 250 },
    } };
    api.defaults.adapter = async config => {
        assert.equal(config.url, '/api/projects/project%2Fone/experiments/experiment/domains/domain/project-hub');
        assert.deepEqual(config.params, {
            state_revision_id: 'state-exact', members_cursor: 'member-page', operations_cursor: 'operation-page',
        });
        assert.equal(config.signal, abort.signal);
        return { config, status: 200, statusText: 'OK', headers: {}, data: fixture };
    };
    const result = await fetchProjectHub('project/one', 'experiment', 'domain', 'state-exact', abort.signal, {
        members_cursor: 'member-page', operations_cursor: 'operation-page',
    });
    assert.deepEqual(result, fixture);
});

test('existing hub calls retain the same default request shape', async () => {
    api.defaults.adapter = async config => {
        assert.deepEqual(config.params, { state_revision_id: 'state-exact' });
        return { config, status: 200, statusText: 'OK', headers: {}, data: { plasmids: [] } };
    };
    assert.deepEqual(await fetchProjectHub('p', 'e', 'd', 'state-exact'), { plasmids: [] });
});

test('molecular history uses scalar pages rather than full snapshots', async () => {
    const abort = new AbortController();
    api.defaults.adapter = async config => {
        assert.equal(config.url, '/api/molbio/sequences/sequence%2Fa/revisions');
        assert.deepEqual(config.params, { limit: 50, offset: 100 });
        assert.equal(config.signal, abort.signal);
        return { config, status: 200, statusText: 'OK', headers: {}, data: [] };
    };
    assert.deepEqual(await fetchMolecularRevisionSummaries('sequence/a', 50, 100, abort.signal), []);
});

test('PCR history asks for summary metadata with explicit continuation', async () => {
    const fixture = { revisions: [], has_more: false, next_offset: null, summary: true };
    api.defaults.adapter = async config => {
        assert.equal(config.url, '/api/molbio/pcr-experiments/experiment%2Fa');
        assert.deepEqual(config.params, { summary: true, limit: 50, offset: 100 });
        return { config, status: 200, statusText: 'OK', headers: {}, data: fixture };
    };
    assert.deepEqual(await fetchPcrExperimentSummaryPage('experiment/a', 50, 100), fixture);
});

test('primer page and cancellation are passed without changing the array response', async () => {
    const abort = new AbortController();
    api.defaults.adapter = async config => {
        assert.equal(config.url, '/api/molbio/primers');
        assert.deepEqual(config.params, { search: 'a_%', limit: 50, offset: 50 });
        assert.equal(config.signal, abort.signal);
        return { config, status: 200, statusText: 'OK', headers: {}, data: [{ id: 'primer-two' }] };
    };
    const result = await fetchPrimers({ search: 'a_%', limit: 50, offset: 50 }, abort.signal);
    assert.deepEqual(result.data, [{ id: 'primer-two' }]);
});

test('domain summary pages expose continuation and leave scientific detail separate', async () => {
    const abort = new AbortController();
    const fixture = {
        items: [{ id: 'revision-two', created_at: '2026-09-11T00:00:00Z', revision_number: 2 }],
        next_cursor: 'revision-two', total: 150,
    };
    api.defaults.adapter = async config => {
        assert.equal(config.url, '/api/molbio-ngs/experiments/domain%2Fone/summaries/reference');
        assert.deepEqual(config.params, { resource_id: 'reference-one', cursor: 'revision-one', limit: 50 });
        assert.equal(config.signal, abort.signal);
        return { config, status: 200, statusText: 'OK', headers: {}, data: fixture };
    };
    const result = await fetchMolBioNgsSummaries('domain/one', 'reference', {
        resource_id: 'reference-one', cursor: 'revision-one', limit: 50,
    }, abort.signal);
    assert.deepEqual(result, fixture);
});
