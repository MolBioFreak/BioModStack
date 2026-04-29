import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import {
  QPCR_PLATE_COLUMNS,
  QPCR_PLATE_ROWS,
  buildQpcrPlateMap,
  buildSelectedQpcrWellAnalytics,
  normalizeQpcrWellPosition,
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

test('qPCR raw import renders a software-style circular plate map with selected-well analytics instead of a Plotly heatmap tab', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /96-well Plate Map/);
  assert.match(source, /Selected well analytics/);
  assert.match(source, /buildQpcrPlateMap/);
  assert.match(source, /buildSelectedQpcrWellAnalytics/);
  assert.match(source, /rounded-full/);
  assert.doesNotMatch(source, /Plate Heatmap/);
});
