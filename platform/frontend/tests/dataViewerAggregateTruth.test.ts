import assert from 'node:assert/strict';
import test from 'node:test';

import * as policy from '../src/lib/clientDerivedResultsPolicy.js';

const summary = {
    total: 1000,
    favorites: 12,
    avg_plddt: 81.5,
    avg_pae: 4.25,
    avg_ptm: 0.71,
    avg_iptm: 0.62,
    avg_ipsae: 0.55,
    avg_affinity: 2.1,
    avg_binder_probability: 0.84,
    avg_epitope_contacts: 8.5,
    avg_target_contacts: 12.5,
    avg_epitope_distance: 3.4,
    avg_target_distance: 2.7,
    avg_hotspot_coverage: 4.5,
    avg_psce: 0.92,
    high_confidence: 700,
    low_error: 640,
    high_contacts: 720,
    screen_passed: 800,
    screen_failed: 150,
};

test('backbone summaries run only for active review workspaces with a selected source', () => {
    const decide = (policy as Record<string, unknown>).shouldFetchBackboneSummary;
    assert.equal(typeof decide, 'function');
    const shouldFetch = decide as (input: {
        active: boolean;
        showReviewWorkingSetPanel: boolean;
        reviewSelectionRequired: boolean;
    }) => boolean;
    assert.equal(shouldFetch({ active: true, showReviewWorkingSetPanel: false, reviewSelectionRequired: false }), false);
    assert.equal(shouldFetch({ active: true, showReviewWorkingSetPanel: true, reviewSelectionRequired: true }), false);
    assert.equal(shouldFetch({ active: true, showReviewWorkingSetPanel: true, reviewSelectionRequired: false }), true);
    assert.equal(shouldFetch({ active: false, showReviewWorkingSetPanel: true, reviewSelectionRequired: false }), false);
});

test('server aggregates replace page samples and clear unsupported sampled metrics', () => {
    const apply = (policy as Record<string, unknown>).applyAuthoritativeDesignSummary;
    assert.equal(typeof apply, 'function');

    const sample = {
        total: 100,
        pageSize: 100,
        favorites: 1,
        avgPlddt: 10,
        avgPae: 99,
        avgPtm: 0.1,
        avgIptm: 0.1,
        avgIpsae: 0.1,
        avgAffinity: 0.1,
        avgBinderProb: 0.1,
        avgEpitopeContacts: 1,
        avgTargetContacts: 1,
        avgEpitopeDistance: 99,
        avgTargetDistance: 99,
        avgHotspotCoverage: 1,
        avgPsce: 3,
        highConfidence: 1,
        lowError: 1,
        highContacts: 1,
        screenPassed: 1,
        screenFailed: 1,
        avgPpiflowDeltaInterface: 123,
        tierA: 99,
        topScreeningReasons: [['sample', 99]],
    };

    const result = (apply as (value: typeof sample, server: typeof summary, loaded: number) => typeof sample)(sample, summary, 100);
    assert.equal(result.total, 1000);
    assert.equal(result.favorites, 12);
    assert.equal(result.avgPlddt, 81.5);
    assert.equal(result.avgPae, 4.25);
    assert.equal(result.highConfidence, 700);
    assert.equal(result.screenPassed, 800);
    assert.equal(result.avgPpiflowDeltaInterface, null);
    assert.equal(result.tierA, 0);
    assert.deepEqual(result.topScreeningReasons, []);
});
