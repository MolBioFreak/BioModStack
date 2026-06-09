import assert from 'node:assert/strict';
import test from 'node:test';

import {
    getMetricDescriptor,
    getMetricDisplayLabel,
    getMetricTooltip,
    resolveDesignMetricCompletenessStatus,
} from '../src/lib/metricRegistry.js';

test('FA-MPNN pSCE descriptor labels sidechain QC and blocks binding-score wording', () => {
    const descriptor = getMetricDescriptor('fampnn_psce');

    assert.equal(descriptor?.label, 'FA-MPNN avg pSCE');
    assert.equal(descriptor?.direction, 'lower_is_better');
    assert.match(descriptor?.caveat || '', /sidechain confidence\/error/i);
    assert.match(descriptor?.caveat || '', /not binding evidence/i);
    assert.equal(getMetricDisplayLabel('fampnn_psce'), 'FA-MPNN avg pSCE');
});

test('PPIFlow objective descriptor is explicitly BMS-local not paper rank', () => {
    const descriptor = getMetricDescriptor('ppiflow_objective_score');

    assert.equal(descriptor?.label, 'BMS local PPIFlow objective');
    assert.equal(descriptor?.group, 'PPIFlow local maturation');
    assert.equal(descriptor?.recommendedUse, 'Local triage only');
    assert.match(getMetricTooltip('ppiflow_objective_score'), /not upstream PPIFlow paper final rank/i);
});

test('Rosetta descriptor preserves raw REU sign convention', () => {
    const descriptor = getMetricDescriptor('rosetta_interface_score');

    assert.equal(descriptor?.label, 'Rosetta interface score');
    assert.equal(descriptor?.direction, 'more_negative_is_better');
    assert.match(getMetricTooltip('rosetta_interface_score'), /Raw Rosetta InterfaceAnalyzerMover dG/i);
});

test('metric completeness resolver exposes partial rows and missing metrics', () => {
    const status = resolveDesignMetricCompletenessStatus({
        metric_completeness: {
            overall_status: 'partial',
            missing: ['fampnn_seq_probs', 'ppiflow_validator_confidence'],
        },
    });

    assert.equal(status.status, 'partial');
    assert.deepEqual(status.missing, ['fampnn_seq_probs', 'ppiflow_validator_confidence']);
    assert.equal(status.label, 'Metric coverage: partial');
});
