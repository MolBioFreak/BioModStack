import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import {
  QPCR_PLATE_COLUMNS,
  QPCR_PLATE_ROWS,
  buildQpcrAssayReviewMetrics,
  buildQpcrChannelAnalytics,
  buildQpcrDilutionWorksheetRows,
  buildQpcrPlateMap,
  buildQpcrReplicateGroupAnalytics,
  buildSelectedQpcrWellAnalytics,
  makeQpcrDilutionGroupKey,
  makeQpcrManualReplicateGroupFromSelection,
  normalizeQpcrWellPosition,
  toggleQpcrWellSelection,
} from '../src/components/qpcr/plateMap.js';

const root = process.cwd();

const sampleWells = [
  {
    well_position: 'A1',
    sample_name: 'Std 1e6',
    target_name: 'gag',
    task: 'STANDARD',
    ct: 18.23456,
    quantity: 1000000,
    reporter: 'FAM',
    threshold: 0.2,
    baseline_start: 3,
    baseline_end: 15,
  },
  {
    well_position: 'A1',
    sample_name: 'Std 1e6',
    target_name: 'pol',
    task: 'STANDARD',
    ct: 19.1,
    quantity: 1000000,
    reporter: 'VIC',
    threshold: 0.18,
    baseline_start: 3,
    baseline_end: 15,
  },
  {
    well_position: 'B2',
    sample_name: 'Spike 80%',
    target_name: 'gag',
    task: 'UNKNOWN',
    ct: null,
    quantity: undefined,
    ct_status: 'no_threshold_crossing',
  },
];

test('qPCR 96-well plate map always returns an A-H by 1-12 circular-well model', () => {
  const map = buildQpcrPlateMap(sampleWells);

  assert.deepEqual(QPCR_PLATE_ROWS, ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']);
  assert.deepEqual(QPCR_PLATE_COLUMNS, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  assert.equal(map.length, 96);
  assert.equal(map[0].position, 'A1');
  assert.equal(map[95].position, 'H12');
  assert.equal(map.filter((well) => well.entries.length > 0).length, 2);
});

test('qPCR plate map normalizes wells and summarizes multiplex Ct/task labels per circular well', () => {
  assert.equal(normalizeQpcrWellPosition(' a01 '), 'A1');
  assert.equal(normalizeQpcrWellPosition('H12'), 'H12');
  assert.equal(normalizeQpcrWellPosition('I1'), null);

  const a1 = buildQpcrPlateMap(sampleWells).find((well) => well.position === 'A1');
  assert.ok(a1);
  assert.equal(a1.sampleLabel, 'Std 1e6');
  assert.equal(a1.targetLabel, 'gag +1');
  assert.equal(a1.taskLabel, 'STANDARD');
  assert.equal(a1.ctCount, 2);
  assert.equal(a1.ctMeanLabel, '18.667');
  assert.equal(a1.status, 'standard');

  const b2 = buildQpcrPlateMap(sampleWells).find((well) => well.position === 'B2');
  assert.ok(b2);
  assert.equal(b2.status, 'review');
  assert.equal(b2.ctMeanLabel, 'Undet.');
});

test('qPCR plate multi-selection supports click replacement and additive toggles without losing the last selected well', () => {
  assert.deepEqual(toggleQpcrWellSelection(['A1'], 'B02', false), ['B2']);
  assert.deepEqual(toggleQpcrWellSelection(['A1'], 'A2', true), ['A1', 'A2']);
  assert.deepEqual(toggleQpcrWellSelection(['A1', 'A2'], 'A1', true), ['A2']);
  assert.deepEqual(toggleQpcrWellSelection(['A1'], 'A1', true), ['A1']);
});

test('qPCR selected wells can be saved as a manual triplicate group and analyzed for Ct and quantity CV', () => {
  const group = makeQpcrManualReplicateGroupFromSelection('manual-1', 'Sample A triplicate', ['C1', 'C2', 'C3'], 'E coli');
  assert.ok(group);
  assert.deepEqual(group.wellPositions, ['C1', 'C2', 'C3']);

  const analytics = buildQpcrReplicateGroupAnalytics([
    { well_position: 'C1', sample_name: 'Sample A', target_name: 'E coli', task: 'UNKNOWN', ct: 24.6, quantity: 100 },
    { well_position: 'C2', sample_name: 'Sample A', target_name: 'E coli', task: 'UNKNOWN', ct: 24.8, quantity: 100 },
    { well_position: 'C3', sample_name: 'Sample A', target_name: 'E coli', task: 'UNKNOWN', ct: 24.7, quantity: 100 },
    { well_position: 'C1', sample_name: 'Sample A', target_name: 'IPC', task: 'UNKNOWN', ct: 27.4, quantity: 50 },
  ], [group], {
    assaySummary: {
      quantities: [
        { well_position: 'C1', sample_name: 'Sample A', target_name: 'E coli', estimated_quantity: 94 },
        { well_position: 'C2', sample_name: 'Sample A', target_name: 'E coli', estimated_quantity: 101 },
        { well_position: 'C3', sample_name: 'Sample A', target_name: 'E coli', estimated_quantity: 105 },
      ],
    },
  });

  assert.equal(analytics.length, 1);
  assert.equal(analytics[0].status, 'ok');
  assert.equal(analytics[0].n, 3);
  assert.equal(analytics[0].ctMeanLabel, '24.700');
  assert.equal(analytics[0].ctSdLabel, '0.100');
  assert.equal(analytics[0].ctCvPercentLabel, '0.40%');
  assert.equal(analytics[0].quantityMeanLabel, '100.000');
  assert.equal(analytics[0].quantityCvPercentLabel, '5.57%');
  assert.equal(analytics[0].recoveryPercentLabel, '100.00%');
});

test('qPCR selected-well analytics exposes labels, Ct summary, replicate QC, standard-curve quantity, and per-target rows', () => {
  const analytics = buildSelectedQpcrWellAnalytics('A1', sampleWells, {
    quantities: [
      {
        well_position: 'A1',
        sample_name: 'Std 1e6',
        target_name: 'gag',
        estimated_quantity: 981234.567,
        log10_estimated_quantity: 5.991,
      },
    ],
    replicate_qc: [
      {
        sample_name: 'Std 1e6',
        target_name: 'gag',
        task: 'STANDARD',
        quantity: 1000000,
        n: 3,
        ct_mean: 18.22,
        ct_sd: 0.08,
        ct_cv_percent: 0.44,
        ct_range: 0.2,
        well_positions: ['A1', 'A2', 'A3'],
      },
    ],
    spike_recovery: [
      {
        sample_name: 'Std 1e6',
        target_name: 'gag',
        recovery_percent: 96.4,
      },
    ],
  });

  assert.equal(analytics.position, 'A1');
  assert.equal(analytics.sampleLabel, 'Std 1e6');
  assert.equal(analytics.targetLabel, 'gag +1');
  assert.equal(analytics.ctSummary.meanLabel, '18.667');
  assert.equal(analytics.entries.length, 2);
  assert.equal(analytics.quantities.length, 1);
  assert.equal(analytics.replicateQc.length, 1);
  assert.equal(analytics.spikeRecovery.length, 1);
});

test('qPCR assay review metrics summarize parsed wells, replicate CV, and spike recovery for top-line cards', () => {
  const metrics = buildQpcrAssayReviewMetrics(sampleWells, {
    replicate_qc: [
      { sample_name: 'Std 1e6', target_name: 'gag', n: 3, ct_cv_percent: 0.44 },
      { sample_name: 'Spike 80%', target_name: 'gag', n: 3, ct_cv_percent: '1.28' },
    ],
    spike_recovery: [
      { sample_name: 'Spike 80%', target_name: 'gag', recovery_percent: 96.4 },
      { sample_name: 'Spike 120%', target_name: 'gag', recovery_percent: '104.8' },
    ],
  });

  assert.equal(metrics.parsedRows, 3);
  assert.equal(metrics.populatedWells, 2);
  assert.equal(metrics.standardRows, 2);
  assert.equal(metrics.replicateGroups, 2);
  assert.equal(metrics.replicateCvMeanLabel, '0.86%');
  assert.equal(metrics.replicateCvMaxLabel, '1.28%');
  assert.equal(metrics.spikeRecoveryCount, 2);
  assert.equal(metrics.spikeRecoveryMeanLabel, '100.60%');
  assert.equal(metrics.spikeRecoveryRangeLabel, '96.40–104.80%');
});

test('qPCR channel analytics split multiplex targets by reporter channel and ROX passive normalization', () => {
  const channelRows = buildQpcrChannelAnalytics([
    { well_position: 'C1', sample_name: 'Sample A', target_name: 'E coli', task: 'UNKNOWN', ct: 24.6, reporter: 'FAM', passive_reference: 'ROX' },
    { well_position: 'C1', sample_name: 'Sample A', target_name: 'IPC', task: 'UNKNOWN', ct: 27.4, reporter: 'VIC', passive_reference: 'ROX' },
    { well_position: 'C2', sample_name: 'Sample A', target_name: 'E coli', task: 'UNKNOWN', ct: 24.8, reporter: 'FAM', passive_reference: 'ROX' },
  ]);

  assert.equal(channelRows.length, 2);
  assert.equal(channelRows[0].targetName, 'E coli');
  assert.equal(channelRows[0].reporter, 'FAM');
  assert.equal(channelRows[0].passiveReference, 'ROX');
  assert.equal(channelRows[0].role, 'target');
  assert.equal(channelRows[0].rows, 2);
  assert.equal(channelRows[0].ctMeanLabel, '24.700');
  assert.equal(channelRows[1].targetName, 'IPC');
  assert.equal(channelRows[1].reporter, 'VIC');
  assert.equal(channelRows[1].role, 'internal_positive_control');
});

test('qPCR dilution worksheet cross-references standard-curve quantities and computes corrected % detection per triplicate group', () => {
  const quantityRows = [
    { well_position: 'C1', sample_name: 'Sample A', target_name: 'E coli', task: 'UNKNOWN', estimated_quantity: 10, log10_estimated_quantity: 1 },
    { well_position: 'C2', sample_name: 'Sample A', target_name: 'E coli', task: 'UNKNOWN', estimated_quantity: 11, log10_estimated_quantity: 1.041 },
    { well_position: 'C3', sample_name: 'Sample A', target_name: 'E coli', task: 'UNKNOWN', estimated_quantity: 9, log10_estimated_quantity: 0.954 },
    { well_position: 'D1', sample_name: 'Sample A', target_name: 'IPC', task: 'UNKNOWN', estimated_quantity: 3, log10_estimated_quantity: 0.477 },
  ];
  const key = makeQpcrDilutionGroupKey(quantityRows[0]);
  const worksheetRows = buildQpcrDilutionWorksheetRows(sampleWells, { quantities: quantityRows }, {
    [key]: { dilutionFactor: '5', expectedQuantity: '100' },
  });

  assert.equal(worksheetRows.length, 2);
  const ecoli = worksheetRows[0];
  assert.equal(ecoli.sampleName, 'Sample A');
  assert.equal(ecoli.targetName, 'E coli');
  assert.equal(ecoli.n, 3);
  assert.deepEqual(ecoli.wellPositions, ['C1', 'C2', 'C3']);
  assert.equal(ecoli.meanEstimatedQuantityLabel, '10.000');
  assert.equal(ecoli.correctedQuantityLabel, '50.000');
  assert.equal(ecoli.percentDetectionLabel, '50.00%');
  assert.equal(ecoli.quantityCvPercentLabel, '10.00%');
});

test('qPCR raw import source gives plots and result tables a wide analysis rail and exposes dilution/channel analytics', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /Channel \/ assay split/);
  assert.match(source, /Dilution-corrected DNA amount worksheet/);
  assert.match(source, /Original-sample amount/);
  assert.match(source, /% detection/);
  assert.match(source, /min-\[1320px\]:grid-cols-\[minmax\(320px,430px\)_minmax\(0,1fr\)\]/);
  assert.doesNotMatch(source, /min-\[1850px\]:grid-cols-\[minmax\(420px,0\.82fr\)_minmax\(0,1fr\)_minmax\(340px,0\.78fr\)\]/);
});

test('qPCR raw import renders a fit-to-panel compact circular plate map with selected-well analytics instead of a Plotly heatmap tab', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /96-well Plate Map/);
  assert.match(source, /Selected well analytics/);
  assert.match(source, /Selected wells \/ manual triplicate analytics/);
  assert.match(source, /data-qpcr-manual-triplicate-workbench="true"/);
  assert.match(source, /Ctrl\/Shift-click plate wells/);
  assert.match(source, /Save selected wells as triplicate group/);
  assert.match(source, /toggleQpcrWellSelection/);
  assert.match(source, /buildQpcrReplicateGroupAnalytics/);
  assert.match(source, /buildQpcrPlateMap/);
  assert.match(source, /buildSelectedQpcrWellAnalytics/);
  assert.match(source, /rounded-full/);
  assert.match(source, /data-qpcr-plate-map-fit="no-horizontal-scroll"/);
  assert.match(source, /gridTemplateColumns: '1rem repeat\(12, minmax\(0, 1fr\)\)'/);
  assert.match(source, /aspect-square w-full max-w-\[1\.72rem\] min-w-0 flex-col items-center justify-center overflow-hidden/);
  assert.doesNotMatch(source, /overflow-x-auto/);
  assert.doesNotMatch(source, /h-\[1\.95rem\] w-\[1\.95rem\]/);
  assert.doesNotMatch(source, /min-w-\[520px\]/);
  assert.doesNotMatch(source, /min-w-\[760px\]/);
  assert.doesNotMatch(source, /Plate Heatmap/);
});
