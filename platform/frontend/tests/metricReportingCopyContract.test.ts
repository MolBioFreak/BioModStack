import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const resultsViewer = readFileSync('src/components/ResultsViewer.tsx', 'utf8');
const analyticsDashboard = readFileSync('src/components/AnalyticsDashboard.tsx', 'utf8');

test('ResultsViewer uses explicit FA-MPNN and BMS-local PPIFlow labels', () => {
    assert.match(resultsViewer, /FA-MPNN avg pSCE/);
    assert.match(resultsViewer, /BMS local PPIFlow objective/);
    assert.doesNotMatch(resultsViewer, /label: 'PPIFlow Objective'/);
});

test('AnalyticsDashboard plot copy does not present pSCE or PPIFlow objective as complete rank', () => {
    assert.match(analyticsDashboard, /pSCE sidechain QC/);
    assert.match(analyticsDashboard, /BMS-local maturation/);
    assert.doesNotMatch(analyticsDashboard, /unknown additional flattened FAMPNN sidechain signals/);
});
