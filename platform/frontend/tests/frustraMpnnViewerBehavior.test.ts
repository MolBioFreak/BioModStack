import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { api } from '../src/lib/api.js';
import * as frustraMpnnApi from '../src/lib/frustraMpnnApi.js';
import { backendHashes, backendStatistics } from './fixtures/frustraMpnnBackendContracts.js';

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

const v2Statistics = {
    ...structuredClone(backendStatistics),
    schema_version: 2,
    output_contract_version: '3.0',
    analysis_receipt: {
        schema_name: 'frustrampnn_statistics_analysis_receipt',
        schema_version: 1,
        analysis_id: analysisPayload.analysis_id,
        core_artifact_id: analysisPayload.core_artifact_id,
        core_bundle_relative_path: 'results/core-artifact-1',
        core_landscape_sha256: analysisPayload.core_landscape_sha256,
        core_manifest_sha256: analysisPayload.core_manifest_sha256,
        formula_version: analysisPayload.formula_version,
        policy_version: analysisPayload.policy_version,
        package_version: analysisPayload.package_version,
        statistics_schema_version: 2,
        attempt_count: 1,
    },
};

test('analysis parser and transport are exact while statistics retain v1 and accept real v2', async () => {
    const module = frustraMpnnApi as unknown as {
        parseFrustraMpnnStatisticsAnalysis?: (value: unknown, parentJobId: string, invocationId: string) => typeof analysisPayload;
        fetchFrustraMpnnStatisticsAnalysis?: (parentJobId: string, invocationId: string, signal?: AbortSignal) => Promise<typeof analysisPayload>;
        retryFrustraMpnnStatisticsAnalysis?: (parentJobId: string, invocationId: string) => Promise<typeof analysisPayload>;
    };
    assert.equal(typeof module.parseFrustraMpnnStatisticsAnalysis, 'function');
    assert.equal(typeof module.fetchFrustraMpnnStatisticsAnalysis, 'function');
    assert.equal(typeof module.retryFrustraMpnnStatisticsAnalysis, 'function');

    assert.equal(frustraMpnnApi.parseFrustraMpnnStatistics(backendStatistics).schema_version, 1);
    const parsedV2 = frustraMpnnApi.parseFrustraMpnnStatistics(v2Statistics);
    assert.equal(parsedV2.schema_version, 2);
    assert.equal(parsedV2.output_contract_version, '3.0');
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
        statistics_json: v2Statistics,
        comparison_compatibility_id: backendHashes.e,
        statistics: v2Statistics,
    });
    assert.equal(parsedV3Response.authority_version, 'v3');
    assert.equal(parsedV3Response.statistics?.schema_version, 2);
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

test('FrustraMpnnResultsViewer source gates and owns v3 derived statistics queries exactly', () => {
    const source = readFileSync(new URL('../src/components/FrustraMpnnResultsViewer.tsx', import.meta.url), 'utf8');
    assert.match(source, /const hasExactV3AnalysisOwner = Boolean\([\s\S]*detail\.data\.parent_job_id === job\.id[\s\S]*detail\.data\.invocation_id === selectedInvocation[\s\S]*detail\.data\.component_contract_version === '3\.0'[\s\S]*terminal_result\.component_contract_version === '3\.0'/);
    assert.match(source, /queryKey: \['frustrampnn-statistics-analysis', job\.id, selectedInvocation\]/);
    assert.match(source, /enabled: hasExactV3AnalysisOwner/);
    assert.match(source, /analysis\.state === 'queued' \|\| analysis\.state === 'running' \? 3000 : false/);
    assert.match(source, /queryKey: \['frustrampnn-statistics', job\.id, selectedInvocation\]/);
    assert.match(source, /enabled: hasExactV3AnalysisOwner && statisticsAnalysis\.data\?\.state === 'completed'/);
    assert.match(source, /invalidateQueries\(\{ queryKey: \['frustrampnn-statistics-analysis', job\.id, selectedInvocation\] \}\)/);
    assert.match(source, /invalidateQueries\(\{ queryKey: \['frustrampnn-statistics', job\.id, selectedInvocation\] \}\)/);
    assert.match(source, /statisticsOverride=\{fetchedStatistics\}/);
    assert.match(source, /canRetry=\{resultContext\.kind === 'scheduler-child'/);
});
