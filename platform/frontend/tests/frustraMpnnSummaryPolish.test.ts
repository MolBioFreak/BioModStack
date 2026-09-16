import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { FrustraMpnnStatisticsSummary } from '../src/components/FrustraMpnnResultAuthoritySurface.js';
import { parseFrustraMpnnStatistics } from '../src/lib/frustraMpnnApi.js';
import { backendStatistics } from './fixtures/frustraMpnnBackendContracts.js';

test('concise statistics retain every displayed quantitative value without modifying the result', () => {
    const statistics = parseFrustraMpnnStatistics(backendStatistics);
    const before = JSON.stringify(statistics);
    const html = renderToStaticMarkup(React.createElement(FrustraMpnnStatisticsSummary, { statistics }));
    for (const label of ['Selected residues', 'Residues with scores', 'Scores available', 'Mean score', 'Highly frustrated scores', 'Ranked substitutions']) assert.ok(html.includes(label), label);
    for (const value of [statistics.support.selected_residue_count, statistics.support.scoreable_residue_count, statistics.support.scoreable_slot_count, statistics.distributions.overall.mean == null ? 'missing' : statistics.distributions.overall.mean.toFixed(3), statistics.class_burden.all.counts.high, statistics.ranked_non_native_alternatives.best_to_worst.length]) assert.ok(html.includes(`>${value}</div>`), String(value));
    assert.doesNotMatch(html, /governed statistics authority|Persisted support/);
    assert.ok(!html.includes(statistics.statistics_sha256.slice(0, 10)));
    assert.equal(JSON.stringify(statistics), before);
});

test('missing-score warning survives summary polish', () => {
    const statistics = parseFrustraMpnnStatistics(backendStatistics);
    statistics.support.missing_residue_count = 1;
    statistics.support.missing_slot_count = 3;
    const html = renderToStaticMarkup(React.createElement(FrustraMpnnStatisticsSummary, { statistics }));
    assert.match(html, /role="status"/);
    assert.match(html, /Missing scores: 1 residues and 3 slots/);
});
