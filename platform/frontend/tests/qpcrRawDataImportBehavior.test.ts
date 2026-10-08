import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import { QPCR_RAW_IMPORT_CACHE_KEY, makeAssaySnapshot } from '../src/components/assayPersistence.js';
import { resolveQpcrInitialTab } from '../src/components/qpcr/plotHelpers.js';

const root = process.cwd();

test('qPCR raw import opens the standard-curve view when a parsed upload has curve data', () => {
  assert.equal(
    resolveQpcrInitialTab({ standard_curve_plotly_json: { data: [{ type: 'scatter' }] } }),
    'stdcurve',
  );
  assert.equal(
    resolveQpcrInitialTab({ amplification_plotly_json: { data: [{ type: 'scatter' }] } }),
    'curves',
  );
  assert.equal(resolveQpcrInitialTab({ results_plotly_json: { data: [{ type: 'heatmap' }] } }), 'heatmap');
});

test('qPCR raw import wires the parsed response through the initial-tab resolver', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');
  assert.match(source, /resolveQpcrInitialTab/);
  assert.match(source, /setPreferredReviewFocus\(resolveQpcrInitialTab\(response\)\)/);
});

test('qPCR standard-curve tab renders a larger QC-focused Plotly review surface', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /MIQE standard curve review/);
  assert.match(source, /Standard curve fit \+ sample quantity calls/);
  assert.match(source, /standard_curve_stats_by_target/);
  assert.match(source, /height: 620/);
  assert.match(source, /Residual SD/);
  assert.match(source, /Curve quality flags/);
});

test('qPCR raw import renders a unified review workbench so plate, plots, and result rows are visible together', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /qPCR instrument review workbench/);
  assert.match(source, /Always-visible amplification curves/);
  assert.match(source, /Always-visible standard curve/);
  assert.match(source, /Full parsed results/);
  assert.match(source, /Right-side readable table/);
  assert.match(source, /buildQpcrAssayReviewMetrics/);
  assert.match(source, /Spike recovery/);
  assert.match(source, /Replicate CV/);
  assert.doesNotMatch(source, /activeTab === 'curves'/);
  assert.doesNotMatch(source, /activeTab === 'table'/);
  assert.doesNotMatch(source, /activeTab === 'stdcurve'/);
  assert.doesNotMatch(source, /AssaySegmentedTabs/);
});

test('qPCR raw import uses the widened assay canvas and avoids clipping plate-map analytics beside the 96-well grid', () => {
  const assaySource = readFileSync(join(root, 'src/components/AssayAnalytics.tsx'), 'utf8');
  const rawImportSource = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(assaySource, /max-w-\[1840px\]/);
  assert.match(rawImportSource, /min-\[1500px\]:grid-cols-\[minmax\(280px,360px\)_minmax\(0,1fr\)\]/);
  assert.match(rawImportSource, /min-\[1650px\]:grid-cols-\[minmax\(520px,0\.74fr\)_minmax\(0,1fr\)_minmax\(360px,0\.86fr\)\]/);
  assert.match(rawImportSource, /min-w-\[520px\]/);
  assert.doesNotMatch(rawImportSource, /min-\[1720px\]:grid-cols-\[minmax\(0,1fr\)_400px\]/);
  assert.doesNotMatch(rawImportSource, /min-w-\[760px\]/);
});

test('qPCR raw import warns that EDS curve-derived Cq/Ct values are not authoritative', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /EDS Cq\/Ct values are computed, not authoritative/);
  assert.match(source, /ct_values_are_authoritative/);
  assert.match(source, /ct_values_calculated_from_multicomponentdata/);
  assert.match(source, /Use the QuantStudio\/StepOnePlus Excel Results export as the known-correct source/);
});

test('qPCR raw import persists and restores the last real parsed upload', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');
  assert.match(source, /QPCR_RAW_IMPORT_CACHE_KEY/);
  assert.match(source, /loadAssaySnapshot/);
  assert.match(source, /saveAssaySnapshot/);
  assert.match(source, /clearAssaySnapshot/);
  assert.match(source, /Restored cached qPCR import/);
  assert.match(source, /Clear cached qPCR import/);
});

test('assay persistence snapshots carry a schema, saved timestamp, label, and real payload only', () => {
  const payload = { filename: 'real-run.eds', n_wells: 132 };
  const snapshot = makeAssaySnapshot(payload, 'real-run.eds', () => '2026-04-28T21:15:00.000Z');
  assert.equal(QPCR_RAW_IMPORT_CACHE_KEY, 'bms.assay.qpcr.rawImport.v1');
  assert.equal(snapshot.schemaVersion, 1);
  assert.equal(snapshot.label, 'real-run.eds');
  assert.equal(snapshot.savedAt, '2026-04-28T21:15:00.000Z');
  assert.deepEqual(snapshot.payload, payload);
});
