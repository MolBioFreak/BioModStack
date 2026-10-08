import assert from 'node:assert/strict';
import test from 'node:test';

import {
    analyzeAssayQcTable,
    detectAssayQcColumns,
    exportCleanedAssayRowsCsv,
    parseDelimitedTable,
    parseGroupRules,
    sanitizeNumericValue,
} from '../src/components/assay/analyticalQc.js';

test('sanitizes assay numeric strings without fabricating values', () => {
    assert.equal(sanitizeNumericValue('1,234.50 ug/mL'), 1234.5);
    assert.equal(sanitizeNumericValue('98.7%'), 98.7);
    assert.equal(sanitizeNumericValue('undetermined'), null);
    assert.equal(sanitizeNumericValue('not detected'), null);
});

test('parses group/bunch rules in both lab shorthand directions', () => {
    assert.deepEqual(parseGroupRules('Spike 5% = 5% spike, spike5'), [
        { target: 'Spike 5%', tokens: ['5% spike', 'spike5'] },
    ]);
    assert.deepEqual(parseGroupRules('LIN-01, LIN-03 -> LIN'), [
        { target: 'LIN', tokens: ['LIN-01', 'LIN-03'] },
    ]);
});

test('computes manual cleaning, bunching, grouped stats, and cross-run stats', () => {
    const parsed = parseDelimitedTable(`sample,run,condition,area,include
A1,R1,5% spike,"1,000",yes
A2,R1,spike5,1100,yes
A3,R2,5% spike,900,no
A4,R2,spike5,950,yes
L1,R1,LIN-01,800,yes
L2,R2,LIN-03,820,yes
BAD,R2,LIN-03,not detected,yes`);
    const columns = detectAssayQcColumns(parsed.headers);

    assert.equal(columns.value, 'area');
    assert.equal(columns.group, 'condition');
    assert.equal(columns.run, 'run');
    assert.equal(columns.sample, 'sample');
    assert.equal(columns.include, 'include');

    const result = analyzeAssayQcTable(parsed, {
        columns,
        groupRulesText: 'Spike 5% = 5% spike, spike5\nLIN = LIN-01, LIN-03',
        cvWarningThreshold: 12,
        zScoreThreshold: 3,
    });

    assert.equal(result.summary.totalRows, 7);
    assert.equal(result.summary.includedRows, 5);
    assert.equal(result.summary.excludedRows, 2);
    assert.equal(result.summary.groupCount, 2);
    assert.equal(result.summary.runCount, 2);
    assert.equal(result.excludedRows.some((row) => row.exclusionReason?.includes('include')), true);
    assert.equal(result.excludedRows.some((row) => row.flags.includes('non_numeric_value')), true);

    const spike = result.groupStats.find((row) => row.key === 'Spike 5%');
    assert.ok(spike);
    assert.equal(spike.n, 3);
    assert.equal(Math.round(spike.mean * 100) / 100, 1016.67);

    const lin = result.groupStats.find((row) => row.key === 'LIN');
    assert.ok(lin);
    assert.equal(lin.n, 2);
    assert.equal(lin.mean, 810);

    const spikeCrossRun = result.crossRunStats.find((row) => row.groupId === 'Spike 5%');
    assert.ok(spikeCrossRun);
    assert.equal(spikeCrossRun.nRuns, 2);
    assert.deepEqual(spikeCrossRun.runMeans.map((row) => `${row.runId}:${row.mean}`), ['R1:1050', 'R2:950']);
    assert.equal(Math.round(spikeCrossRun.meanOfRunMeans), 1000);
});

test('manual exclude terms are applied before summaries and export stays auditable', () => {
    const parsed = parseDelimitedTable(`sample\trun\tgroup\tvalue
keep-1\trunA\tA\t10
rerun-bad\trunA\tA\t100
keep-2\trunB\tA\t14`);
    const result = analyzeAssayQcTable(parsed, {
        columns: { value: 'value', sample: 'sample', run: 'run', group: 'group' },
        excludeTermsText: 'rerun-bad',
    });

    assert.equal(result.summary.includedRows, 2);
    assert.equal(result.groupStats[0].mean, 12);
    assert.equal(result.excludedRows[0].exclusionReason, 'Manual exclude term: rerun-bad');

    const exported = exportCleanedAssayRowsCsv(result.rows);
    assert.match(exported, /rowNumber,included,sampleId,runId,rawGroupId,groupId/);
    assert.match(exported, /rerun-bad/);
    assert.match(exported, /manual_exclude/);
});
