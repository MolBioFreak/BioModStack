import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import {
  highlightSelectedWellAmplificationTraces,
  highlightSelectedWellStandardCurvePoints,
  resolveQpcrInitialTab,
} from '../src/components/qpcr/plotHelpers.js';

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

test('qPCR raw import wires parsed and reloaded responses through the initial-tab resolver', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');
  assert.match(source, /resolveQpcrInitialTab/);
  assert.match(source, /const applyQpcrPayload = useCallback/);
  assert.match(source, /setPreferredReviewFocus\(resolveQpcrInitialTab\(payload\)\)/);
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

test('qPCR raw import plate map is fit-to-panel and cannot create the horizontal slider/clipped 12-column failure', () => {
  const assaySource = readFileSync(join(root, 'src/components/AssayAnalytics.tsx'), 'utf8');
  const rawImportSource = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(assaySource, /max-w-\[1840px\]/);
  assert.match(rawImportSource, /data-qpcr-plate-map-fit="no-horizontal-scroll"/);
  assert.match(rawImportSource, /data-qpcr-plate-grid="fit-panel"/);
  assert.match(rawImportSource, /gridTemplateColumns: '1rem repeat\(12, minmax\(0, 1fr\)\)'/);
  assert.match(rawImportSource, /min-\[1320px\]:grid-cols-\[minmax\(320px,430px\)_minmax\(0,1fr\)\]/);
  assert.match(rawImportSource, /aspect-square w-full max-w-\[1\.72rem\] min-w-0 flex-col items-center justify-center overflow-hidden/);
  assert.match(rawImportSource, /Fit-to-panel 96-well map; all A-H rows and 1-12 columns stay visible with no horizontal slider/);
  assert.doesNotMatch(rawImportSource, /overflow-x-auto/);
  assert.doesNotMatch(rawImportSource, /min-w-\[520px\]/);
  assert.doesNotMatch(rawImportSource, /grid-cols-\[1\.35rem_repeat\(12,2\.38rem\)\]/);
  assert.doesNotMatch(rawImportSource, /compactPlateLabel/);
  assert.doesNotMatch(rawImportSource, /min-\[1720px\]:grid-cols-\[minmax\(0,1fr\)_400px\]/);
  assert.doesNotMatch(rawImportSource, /min-w-\[760px\]/);
});

test('qPCR selected-well plot helpers highlight the clicked well on standard curves and amplification traces', () => {
  const standardCurveData = [
    { type: 'scatter', mode: 'markers', name: 'E coli standards', x: [6, 5], y: [18.1, 21.4], text: ['A1 STD 1e6', 'A2 STD 1e5'] },
    { type: 'scatter', mode: 'lines', name: 'E coli fit', x: [5, 6], y: [21.4, 18.1] },
    { type: 'scatter', mode: 'markers', name: 'E coli experimentals', x: [4.15, 4.11, 3.9], y: [24.7, 24.8, 30.1], text: ['C1 Sample A', 'C2 Sample A', 'C10 Sample A'] },
    { type: 'scatter', mode: 'markers', name: 'IPC experimentals', x: [1.02], y: [27.5], text: ['C1 Sample A'] },
  ];

  const standardHighlighted = highlightSelectedWellStandardCurvePoints(standardCurveData, 'C1', 'E coli');
  const highlightTrace = standardHighlighted.at(-1) as Record<string, unknown>;
  assert.equal(highlightTrace.name, 'Selected well C1 on standard curve');
  assert.deepEqual(highlightTrace.x, [4.15]);
  assert.deepEqual(highlightTrace.y, [24.7]);
  assert.deepEqual(highlightTrace.text, ['C1 Sample A']);
  assert.notDeepEqual(highlightTrace.y, [27.5]);

  const amplificationHighlighted = highlightSelectedWellAmplificationTraces(
    [
      { type: 'scatter', mode: 'lines', name: 'C1 E coli', x: [1, 2], y: [0.01, 0.2], line: { width: 1 }, opacity: 0.55 },
      { type: 'scatter', mode: 'lines', name: 'C1 IPC', x: [1, 2], y: [0.02, 0.08], line: { width: 1 }, opacity: 0.55 },
      { type: 'scatter', mode: 'lines', name: 'A1 E coli', x: [1, 2], y: [0.01, 0.5], line: { width: 1 }, opacity: 0.55 },
      { type: 'scatter', mode: 'lines', name: 'C10 E coli', x: [1, 2], y: [0.01, 0.7], line: { width: 1 }, opacity: 0.55 },
    ],
    'C1',
    'E coli',
  ) as Array<Record<string, unknown>>;
  assert.equal((amplificationHighlighted[0].line as Record<string, unknown>).width, 4);
  assert.equal(amplificationHighlighted[0].opacity, 1);
  assert.equal(amplificationHighlighted[1].opacity, 0.12);
  assert.equal(amplificationHighlighted[2].opacity, 0.12);
  assert.equal(amplificationHighlighted[3].opacity, 0.12);
});

test('qPCR raw import source wires clicked wells into standard-curve and amplification highlighting', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /highlightSelectedWellStandardCurvePoints/);
  assert.match(source, /highlightSelectedWellAmplificationTraces/);
  assert.match(source, /Selected well spot is overlaid on the active target standard curve/);
  assert.match(source, /clicked well traces are brightened while other amplification traces are dimmed/);
});

test('qPCR raw import warns that EDS curve-derived Cq/Ct values are not authoritative', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /EDS Cq\/Ct values are computed, not authoritative/);
  assert.match(source, /ct_values_are_authoritative/);
  assert.match(source, /ct_values_calculated_from_multicomponentdata/);
  assert.match(source, /Use the QuantStudio\/StepOnePlus Excel Results export as the known-correct source/);
});

test('qPCR raw import reloads persisted datasets from BMS DB service, not browser cache', () => {
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(source, /listAnalyticalDatasets\('qpcr', 25\)/);
  assert.match(source, /loadAnalyticalDataset\(selectedDatasetId\)/);
  assert.match(source, /Saved qPCR import to BMS DB service dataset/);
  assert.match(source, /BMS DB service analytical store, not browser cache/);
  assert.match(source, /Persisted qPCR imports/);
  assert.doesNotMatch(source, /QPCR_RAW_IMPORT_CACHE_KEY/);
  assert.doesNotMatch(source, /loadAssaySnapshot/);
  assert.doesNotMatch(source, /saveAssaySnapshot/);
  assert.doesNotMatch(source, /Review cache/);
  assert.doesNotMatch(source, /Clear cached qPCR import/);
});

test('qPCR uploads default to durable server persistence', () => {
  const apiSource = readFileSync(join(root, 'src/api/client.ts'), 'utf8');
  const source = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');

  assert.match(apiSource, /formData\.append\('persist', String\(options\.persist \?\? true\)\)/);
  assert.match(source, /const response = await uploadQpcrFile\(file\) as RawQpcrImportResponse/);
});
