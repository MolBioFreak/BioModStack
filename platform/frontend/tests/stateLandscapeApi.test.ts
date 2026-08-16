import assert from 'node:assert/strict';
import test from 'node:test';

import { api } from '../src/lib/api.js';
import * as cmApi from '../src/components/conformationalMapping/conformationalMappingApi.js';

test('state landscape summary and selected-pair rows use the authenticated CM axios client with bounded params', async () => {
    const calls: Array<{ url: string; config?: unknown }> = [];
    const originalGet = api.get;
    (api as unknown as { get: (url: string, config?: unknown) => Promise<{ data: unknown }> }).get = async (url, config) => {
        calls.push({ url, config });
        return { data: { request_id: 'request / 1', rows: [], pairs: [] } };
    };
    try {
        const getSummary = (cmApi as unknown as { getCmStateLandscapeAnalysis?: (requestId: string) => Promise<unknown> }).getCmStateLandscapeAnalysis;
        const getRows = (cmApi as unknown as {
            getCmStateLandscapeAnalysisRows?: (requestId: string, analysisId: string, pairId: string, offset: number, limit: number) => Promise<unknown>;
        }).getCmStateLandscapeAnalysisRows;
        assert.equal(typeof getSummary, 'function');
        assert.equal(typeof getRows, 'function');

        await getSummary!('request / 1');
        await getRows!('request / 1', 'analysis / 1', 'pair / 1', 40, 50);

        assert.deepEqual(calls, [
            { url: '/api/conformational-mapping/requests/request%20%2F%201/state-landscape-analysis', config: undefined },
            {
                url: '/api/conformational-mapping/requests/request%20%2F%201/state-landscape-analysis/rows',
                config: { params: { analysis_id: 'analysis / 1', pair_id: 'pair / 1', offset: 40, limit: 50 } },
            },
        ]);
    } finally {
        (api as unknown as { get: typeof originalGet }).get = originalGet;
    }
});
