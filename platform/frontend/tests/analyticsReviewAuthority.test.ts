import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const analyticsSource = readFileSync(resolve(process.cwd(), 'src/components/AnalyticsDashboard.tsx'), 'utf8');
const resultsSource = readFileSync(resolve(process.cwd(), 'src/components/ResultsViewer.tsx'), 'utf8');

test('analytics dashboard gates persisted analysis requests and actions by review authority and readiness', () => {
    assert.match(analyticsSource, /enabled: !!activeDesignId && canRunChainMetrics/);
    assert.match(analyticsSource, /enabled: !!activeDesignId && canRunPaeMatrix/);
    assert.match(analyticsSource, /supportsChainMetrics &&/);
    assert.match(analyticsSource, /supportsPaeMatrix &&/);
    assert.doesNotMatch(
        analyticsSource,
        /queryKey: \['analytics-(?:chain-metrics|pae-data)'[\s\S]{0,300}enabled: !!activeDesignId,/
    );
});

test('overview binder cards and binding distributions require declared review capabilities', () => {
    assert.match(resultsSource, /tableReviewCapabilities\.antibody \? \(/);
    assert.match(resultsSource, /tableReviewCapabilities\.antibody \|\| tableReviewCapabilities\.interface/);
    assert.match(resultsSource, /Binding-quality tiers are not applicable to this review profile/);
});

test('antibody and PPIFlow controls cannot be enabled by job names, arbitrary params, or ungated labels', () => {
    const contextBlock = resultsSource.slice(
        resultsSource.indexOf('const isAntibodyContext'),
        resultsSource.indexOf('const isProteinLocalRedesignContext'),
    );
    assert.doesNotMatch(contextBlock, /activeJob\.name|activeJob\.params|includes\('antibody'\)/);
    assert.match(resultsSource, /tableReviewCapabilities\.antibody && \(\s*<button[\s\S]{0,160}runAntibodyAnalysis/);
    assert.match(resultsSource, /value !== 'rfantibody_backbones' \|\| tableReviewCapabilities\.antibody/);
    assert.match(resultsSource, /value !== 'ppiflow_candidates' \|\| showPpiflowColumns/);
    assert.match(resultsSource, /getAuthoritativeDesignLens/);
    assert.match(resultsSource, /\.map\(sanitizeDesignForReview\)/);
});
