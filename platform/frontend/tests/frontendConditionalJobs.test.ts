import assert from 'node:assert/strict';
import test from 'node:test';
import { api, fetchJobs } from '../src/lib/api.ts';

test('conditional list transport uses only each query entry and never conceals errors or changed/removed rows', async () => {
    const original = api.defaults.adapter;
    const calls: Array<{ params: unknown; etag: unknown }> = [];
    let version = 1;
    let failure = 0;
    api.defaults.adapter = async config => {
        calls.push({ params: config.params, etag: config.headers.get('If-None-Match') });
        const filter = config.params.status ?? 'all';
        const etag = `"${filter}-${version}"`;
        const status = failure || (config.headers.get('If-None-Match') === etag ? 304 : 200);
        if (!config.validateStatus!(status)) throw new Error(`HTTP ${status}`);
        return { status, statusText: '', config, headers: { etag }, data: status === 304 ? '' : { jobs: version === 3 ? [] : [{ id: filter, status: version === 2 ? 'completed' : 'running' }], total: version === 3 ? 0 : 1 } };
    };
    try {
        const all = await fetchJobs({ limit: 100, summary: true });
        const completed = await fetchJobs({ status: 'completed', limit: 100, summary: true });
        assert.notDeepEqual(all.data, completed.data);
        const unchanged = await fetchJobs({ limit: 100, summary: true }, all);
        assert.equal(unchanged.status, 304);
        assert.equal(unchanged.data, all.data);
        const other = await fetchJobs({ status: 'completed', limit: 100, summary: true }, completed);
        assert.equal(other.data, completed.data);
        assert.equal(calls[2].etag, '"all-1"');
        assert.equal(calls[3].etag, '"completed-1"');
        version = 2;
        const changed = await fetchJobs({ limit: 100, summary: true }, unchanged);
        assert.equal(changed.status, 200);
        assert.equal(changed.data.jobs[0].status, 'completed');
        for (const status of [401, 403, 500]) {
            failure = status;
            await assert.rejects(fetchJobs({ limit: 100 }, changed), new RegExp(`HTTP ${status}`));
        }
        failure = 0;
        version = 3;
        const removed = await fetchJobs({ limit: 100, summary: true }, changed);
        assert.deepEqual(removed.data, { jobs: [], total: 0 });
        await fetchJobs({ status: 'completed', limit: 100 });
        assert.equal(calls.at(-1)!.etag, undefined, 'new query entry has no singleton validator');
    } finally { api.defaults.adapter = original; }
});
