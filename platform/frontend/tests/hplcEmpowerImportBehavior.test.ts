import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const root = process.cwd();

test('Empower import UI exposes batch QC, composition plots, and flattened peak review', () => {
  const source = readFileSync(join(root, 'src/components/hplc/EmpowerImport.tsx'), 'utf8');

  assert.match(source, /empowerSummary/);
  assert.match(source, /Batch QC/);
  assert.match(source, /Peak Composition/);
  assert.match(source, /Flattened Peak Table/);
  assert.match(source, /qc_plotly_json/);
  assert.match(source, /composition_plotly_json/);
  assert.match(source, /peak_table/);
  assert.match(source, /flagged_injection_count/);
});

test('chromatography workbench opens on the Waters Empower import workflow by default', () => {
  const source = readFileSync(join(root, 'src/components/hplc/index.tsx'), 'utf8');
  assert.match(source, /useState<AnalysisType>\('empower'\)/);
});

test('Empower import reloads persisted batch reviews from BMS DB service, not browser cache', () => {
  const source = readFileSync(join(root, 'src/components/hplc/EmpowerImport.tsx'), 'utf8');

  assert.match(source, /listAnalyticalDatasets\('chromatography', 25\)/);
  assert.match(source, /loadAnalyticalDataset\(selectedDatasetId\)/);
  assert.match(source, /Saved Empower import to BMS DB service dataset/);
  assert.match(source, /Reload from BMS DB/);
  assert.match(source, /Clear current Empower review/);
  assert.doesNotMatch(source, /EMPOWER_IMPORT_CACHE_KEY/);
  assert.doesNotMatch(source, /loadAssaySnapshot/);
  assert.doesNotMatch(source, /saveAssaySnapshot/);
  assert.doesNotMatch(source, /Review cache/);
  assert.doesNotMatch(source, /Clear cached Empower import/);
});

test('assay tab surfaces stay mounted while hidden so switching workbenches does not wipe state', () => {
  const assaySource = readFileSync(join(root, 'src/components/AssayAnalytics.tsx'), 'utf8');
  const qpcrSource = readFileSync(join(root, 'src/components/qpcr/index.tsx'), 'utf8');
  const hplcSource = readFileSync(join(root, 'src/components/hplc/index.tsx'), 'utf8');

  assert.match(assaySource, /hidden=\{activeTab !== 'qpcr'\}/);
  assert.match(assaySource, /hidden=\{activeTab !== 'chromatography'\}/);
  assert.match(qpcrSource, /hidden=\{activeAnalysis !== 'import'\}/);
  assert.match(hplcSource, /hidden=\{activeAnalysis !== 'empower'\}/);
});
