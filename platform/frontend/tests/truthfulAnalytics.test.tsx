import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
Object.assign(globalThis, {React});
import { renderToStaticMarkup } from 'react-dom/server';
import { parseScientificPoint } from '../src/lib/scientificAnalytics';
import { ScientificAnalytics } from '../src/components/ScientificAnalytics';

const fixture = () => ({ id: 'a', name: 'A', contract_revision: 1, source_job_id: 'j', cohort_key: 'v1:profile:j',
    metrics: { plddt_overall: 0, pae_overall: 2 },
    metric_states: { plddt_overall: {state:'ok',value:0,reason_code:null}, pae_overall: {state:'ok',value:2,reason_code:null}, rmsd_overall: {state:'unavailable',value:null,reason_code:'not_reported'} },
    metric_sources: Object.fromEntries(['plddt_overall','pae_overall','rmsd_overall'].map(metric_id => [metric_id, {artifact_sha256:'a'.repeat(64),candidate_id:'a',document_id:'a'}])),
    metric_descriptors: Object.fromEntries(['plddt_overall','pae_overall','rmsd_overall'].map(metric_id => [metric_id, {metric_id,source:'canonical_artifact',producer_version:'ui-fixture-v1',derivation_version:'ui-fixture-v1', scope:'overall', unit:metric_id === 'plddt_overall' ? 'pLDDT' : 'angstrom', direction:metric_id === 'plddt_overall' ? 'higher' : 'lower'}])) });

test('runtime scalar parser rejects coercion, incoherent absence, and synthetic measurements', () => {
    assert.equal(parseScientificPoint(fixture()).metrics.plddt_overall, 0);
    for (const value of [true, '0', NaN, Infinity]) {
        const row = fixture();
        (row.metric_states.plddt_overall as {value:unknown}).value = value;
        assert.throws(() => parseScientificPoint(row));
    }
    const row = fixture(); row.metric_states.rmsd_overall.reason_code = '';
    assert.throws(() => parseScientificPoint(row));
    const extra = fixture(); Object.assign(extra.metrics, { seed_mean: 10 });
    assert.throws(() => parseScientificPoint(extra));
});

test('UI-only scalar fixture renders zero and absence; omitted server pairs stay absent', () => {
    const point = parseScientificPoint(fixture());
    const html = renderToStaticMarkup(<ScientificAnalytics points={[point]} cohorts={[]} />);
    assert.match(html, /<td>A \(a\)<\/td>/);
    assert.doesNotMatch(html, /data-x=/);
    assert.match(html, /Paired statistics are unavailable for this response/);
    assert.match(html, />0</);
    assert.match(html, /not_reported/);
    assert.match(html, /pLDDT/);
    assert.match(html, /pae_overall.*angstrom/);
    assert.doesNotMatch(html, /data-rmsd="0"/);
});
