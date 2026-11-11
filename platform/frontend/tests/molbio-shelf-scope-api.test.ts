import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, fetchNucleotideSequences } from '../src/lib/api';

const originalAdapter = api.defaults.adapter;
afterEach(() => { api.defaults.adapter = originalAdapter; });

test('the unassigned shelf requires the server to confirm membership filtering', async () => {
    api.defaults.adapter = async config => ({ config, status: 200, statusText: 'OK', headers: {}, data: [{ id: 'project-sequence' }] });
    await assert.rejects(fetchNucleotideSequences({ project_membership: 'unassigned' }), /updated Project membership API/);
    // Other consumers can still explicitly use the ordinary global library.
    assert.deepEqual((await fetchNucleotideSequences()).data, [{ id: 'project-sequence' }]);
});

test('confirmed unassigned responses preserve the bounded request and array shape', async () => {
    const params = { project_membership: 'unassigned' as const, sequence_ids: 'open-sequence', limit: 24 };
    api.defaults.adapter = async config => {
        assert.deepEqual(config.params, params);
        return { config, status: 200, statusText: 'OK', headers: { 'x-bns-sequence-scope': 'unassigned' }, data: [{ id: 'open-sequence' }] };
    };
    assert.deepEqual((await fetchNucleotideSequences(params)).data, [{ id: 'open-sequence' }]);
});
