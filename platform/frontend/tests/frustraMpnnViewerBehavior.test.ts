import assert from 'node:assert/strict';
import test from 'node:test';

import { api } from '../src/lib/api.js';
import * as frustraMpnnApi from '../src/lib/frustraMpnnApi.js';
import { backendHashes, backendStatistics, backendDerivedStatistics } from './fixtures/frustraMpnnBackendContracts.js';

const analysisPayload = {
    analysis_id: '11111111-1111-4111-8111-111111111111',
    parent_job_id: 'job-1',
    invocation_id: 'invoke-1',
    state: 'failed',
    attempt_count: 1,
    core_artifact_id: 'core-artifact-1',
    core_landscape_sha256: 'c'.repeat(64),
    core_manifest_sha256: 'd'.repeat(64),
    formula_version: 'frustrampnn_statistics_formula_v1',
    policy_version: 'frustrampnn_statistics_policy_v1',
    package_version: 'biomodstack_frustrampnn_statistics_v1',
    schema_version: 1,
    artifact_sha256: null,
    statistics_sha256: null,
    diagnostic: 'bounded failure',
} as const;

test('analysis parser and transport preserve schema-checked derived statistics provenance', async () => {
    const module = frustraMpnnApi as unknown as {
        parseFrustraMpnnStatisticsAnalysis?: (value: unknown, parentJobId: string, invocationId: string) => typeof analysisPayload;
        fetchFrustraMpnnStatisticsAnalysis?: (parentJobId: string, invocationId: string, signal?: AbortSignal) => Promise<typeof analysisPayload>;
        retryFrustraMpnnStatisticsAnalysis?: (parentJobId: string, invocationId: string) => Promise<typeof analysisPayload>;
    };
    assert.equal(typeof module.parseFrustraMpnnStatisticsAnalysis, 'function');
    assert.equal(typeof module.fetchFrustraMpnnStatisticsAnalysis, 'function');
    assert.equal(typeof module.retryFrustraMpnnStatisticsAnalysis, 'function');

    assert.equal(frustraMpnnApi.parseFrustraMpnnStatistics(backendStatistics).schema_version, 1);
    const parsedV2 = frustraMpnnApi.parseFrustraMpnnStatistics(backendDerivedStatistics);
    assert.equal(parsedV2.schema_version, 2);
    assert.equal(parsedV2.output_contract_version, '3.0');
    assert.deepEqual(parsedV2, backendDerivedStatistics);
    assert.deepEqual(parsedV2.comparison_compatibility_basis, backendDerivedStatistics.comparison_compatibility_basis);
    assert.equal(parsedV2.analysis_receipt.analysis_id, analysisPayload.analysis_id);
    const parsedV3Response = frustraMpnnApi.parseFrustraMpnnStatisticsResponse({
        result_id: 'result-1',
        parent_job_id: 'job-1',
        candidate_id: 'candidate-1',
        invocation_id: 'invoke-1',
        authority_version: 'v3',
        availability: true,
        missing_fields: [],
        settings_sha256: null,
        effective_settings_sha256: null,
        effective_settings_json: null,
        capability_inventory_sha256: null,
        statistics_sha256: backendHashes.f,
        statistics_json: backendDerivedStatistics,
        comparison_compatibility_id: backendHashes.e,
        statistics: backendDerivedStatistics,
    });
    assert.equal('authority_version' in parsedV3Response, false);
    assert.equal(parsedV3Response.statistics?.schema_version, 2);
    assert.equal('statistics_json' in parsedV3Response, false);
    const canonicalOnly = { ...parsedV3Response };
    assert.deepEqual(frustraMpnnApi.parseFrustraMpnnStatisticsResponse(canonicalOnly), parsedV3Response);
    assert.deepEqual(frustraMpnnApi.parseFrustraMpnnStatisticsResponse({ ...canonicalOnly, statistics_json: structuredClone(canonicalOnly.statistics) }), parsedV3Response);
    assert.throws(() => frustraMpnnApi.parseFrustraMpnnStatisticsResponse({ ...canonicalOnly, statistics_json: null }), /aliases conflict/);
    assert.throws(
        () => module.parseFrustraMpnnStatisticsAnalysis!({ ...analysisPayload, extra: true }, 'job-1', 'invoke-1'),
        /unknown or missing keys/,
    );
    assert.throws(
        () => module.parseFrustraMpnnStatisticsAnalysis!(analysisPayload, 'other-job', 'invoke-1'),
        /parent_job_id/,
    );
    assert.throws(
        () => module.parseFrustraMpnnStatisticsAnalysis!(analysisPayload, 'job-1', 'other-invocation'),
        /invocation_id/,
    );

    const getCalls: Array<{ url: string; signal: AbortSignal | undefined }> = [];
    const postCalls: string[] = [];
    const originalGet = api.get;
    const originalPost = api.post;
    api.get = (async (url: string, config?: { signal?: AbortSignal }) => {
        getCalls.push({ url, signal: config?.signal });
        return { data: analysisPayload };
    }) as typeof api.get;
    api.post = (async (url: string) => {
        postCalls.push(url);
        return { data: analysisPayload };
    }) as typeof api.post;
    try {
        const controller = new AbortController();
        await module.fetchFrustraMpnnStatisticsAnalysis!('job-1', 'invoke-1', controller.signal);
        await module.retryFrustraMpnnStatisticsAnalysis!('job-1', 'invoke-1');
        assert.deepEqual(getCalls, [{
            url: '/api/frustrampnn/results/job-1/invoke-1/statistics/analysis',
            signal: controller.signal,
        }]);
        assert.deepEqual(postCalls, ['/api/frustrampnn/results/job-1/invoke-1/statistics/retry']);
    } finally {
        api.get = originalGet;
        api.post = originalPost;
    }
});

// Mounted viewer ownership/query transitions are covered by the component suite.
