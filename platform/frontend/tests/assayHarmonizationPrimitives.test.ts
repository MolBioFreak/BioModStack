import { strict as assert } from 'node:assert';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const root = process.cwd();

function source(relativePath: string): string {
  return readFileSync(join(root, relativePath), 'utf8');
}

test('assay analytics defines a shared BMS workbench primitive layer', () => {
  const primitivePath = join(root, 'src/components/assay/AssayWorkbenchPrimitives.tsx');
  assert.equal(existsSync(primitivePath), true, 'Assay workbench primitives should exist for harmonized BMS styling');

  const primitives = readFileSync(primitivePath, 'utf8');
  for (const exportName of [
    'AssayPageShell',
    'AssayPageHeader',
    'AssayModeTabs',
    'AssayWorkbenchIntro',
    'AssayStatusStrip',
    'AssaySubnavGrid',
    'AssaySegmentedTabs',
    'AssayPanel',
    'AssayInputCard',
    'AssayOutputCard',
    'AssayEmptyState',
    'AssayPrimaryButton',
    'AssayErrorNotice',
    'AssayFieldLabel',
  ]) {
    assert.match(primitives, new RegExp(`export function ${exportName}\\b`), `${exportName} should be exported`);
  }

  assert.match(primitives, /rounded-xl/, 'Assay primitives should use rounded BMS workbench panels');
  assert.match(primitives, /var\(--bg-secondary\)/, 'Assay primitives should use theme CSS variables');
  assert.match(primitives, /assayPanelSurfaceStyle/, 'Assay panels should use the softened depth surface treatment');
  assert.match(primitives, /assayTileSurfaceStyle/, 'Assay nested cards should use the softened tile surface treatment');
  assert.doesNotMatch(
    primitives,
    /rounded-xl border border-\[var\(--border-primary\)\] bg-\[var\(--bg-secondary\)\] shadow-sm/,
    'Assay panels should not use the harsh high-contrast box-outline treatment',
  );
});

test('top-level assay page uses the shared shell/header/tabs instead of a bespoke hero', () => {
  const assay = source('src/components/AssayAnalytics.tsx');
  assert.match(assay, /AssayPageShell/);
  assert.match(assay, /AssayPageHeader/);
  assert.match(assay, /AssayModeTabs/);
  assert.match(assay, /AssayPanel/);
  assert.doesNotMatch(assay, /function TabButton/);
  assert.doesNotMatch(assay, /Std curve \/ ΔΔCq/);
  assert.doesNotMatch(assay, /own product surface/);

  for (const hiddenContract of [
    "hidden={activeTab !== 'qpcr'}",
    "hidden={activeTab !== 'chromatography'}",
    "hidden={activeTab !== 'statistics'}",
  ]) {
    assert.match(assay, new RegExp(hiddenContract.replace(/[{}=!']/g, '\\$&')), `Assay should preserve mounted-hidden tab contract: ${hiddenContract}`);
  }
});

test('qPCR, chromatography, and statistics use consistent workbench intro and subnav primitives', () => {
  const files = [
    'src/components/qpcr/index.tsx',
    'src/components/hplc/index.tsx',
    'src/components/statistics/index.tsx',
  ];

  for (const relativePath of files) {
    const fileSource = source(relativePath);
    assert.match(fileSource, /AssayWorkbenchIntro/, `${relativePath} should use the shared intro`);
    assert.match(fileSource, /AssayStatusStrip/, `${relativePath} should use shared status cards`);
    assert.match(fileSource, /AssaySubnavGrid/, `${relativePath} should use shared analysis selectors`);
    assert.match(fileSource, /AssayPanel/, `${relativePath} should wrap active tools in the shared panel`);
    assert.doesNotMatch(fileSource, /export \{/, `${relativePath} should not re-export private assay subpanels through a duplicate barrel API`);
    assert.doesNotMatch(fileSource, /bg-accent-primary text-white/, `${relativePath} should not use the old statistics-only active state`);
  }
});

test('assay subpanels do not keep duplicated legacy active-tab or primary-button styling', () => {
  const assayFiles = [
    'src/components/qpcr/DeltaCqPanels.tsx',
    'src/components/qpcr/QuantificationPanels.tsx',
    'src/components/qpcr/RawDataImport.tsx',
    'src/components/hplc/ChromatogramAnalysis.tsx',
    'src/components/hplc/EmpowerImport.tsx',
    'src/components/statistics/ControlChart.tsx',
    'src/components/statistics/DoEPanel.tsx',
    'src/components/statistics/HypothesisTesting.tsx',
    'src/components/statistics/ProcessCapability.tsx',
    'src/components/statistics/RegressionAnalysis.tsx',
  ];

  for (const relativePath of assayFiles) {
    const fileSource = source(relativePath);
    assert.doesNotMatch(
      fileSource,
      /bg-accent-primary hover:bg-accent-secondary text-white px-4 py-2 font-medium/,
      `${relativePath} should use AssayPrimaryButton instead of duplicating the legacy CTA class string`,
    );
    assert.doesNotMatch(
      fileSource,
      /bg-accent-primary text-white|border-b-2 border-accent-primary/,
      `${relativePath} should use AssaySegmentedTabs instead of bespoke active-tab styling`,
    );
  }

  assert.doesNotMatch(
    source('src/components/assay/AssayWorkbenchPrimitives.tsx'),
    /export function AssaySecondaryButton\b/,
    'Unused secondary button primitive should stay pruned until a real assay workflow needs it',
  );
});

test('HPLC sample quantification is grouped as a BMS input/result workbench', () => {
  const chromatogram = source('src/components/hplc/ChromatogramAnalysis.tsx');

  for (const marker of [
    'AssayInputCard',
    'AssayOutputCard',
    'AssayEmptyState',
    'AssayPrimaryButton',
    'Calibration standards',
    'Unknown samples',
    'Quantification Results',
    'No quantification run yet',
    'Calibration concentrations',
    'Sample peak areas',
  ]) {
    assert.match(chromatogram, new RegExp(marker), `Sample quantification should include ${marker}`);
  }

  assert.match(chromatogram, /grid-cols-1 xl:grid-cols-\[minmax\(0,0\.95fr\)_minmax\(0,1\.05fr\)\]/);
  assert.doesNotMatch(chromatogram, /Calibration Conc\./);
  assert.match(chromatogram, /fetch\(`\$\{API_URL\}\/analysis\/hplc\/quantify`/);
});

test('HPLC quantification renders the backend calibration plot instead of exposing implementation copy', () => {
  const chromatogram = source('src/components/hplc/ChromatogramAnalysis.tsx');
  const quantification = chromatogram.slice(chromatogram.indexOf('export function HplcQuantification'));

  assert.match(quantification, /plotly_json\?: \{ data: Plotly\.Data\[\]; layout: Partial<Plotly\.Layout> \}/);
  assert.match(quantification, /r\.plotly_json && \(/);
  assert.match(quantification, /<Plot/);
  assert.doesNotMatch(quantification, /API contract remains/);
});

test('HPLC chromatogram baseline selector labels only methods implemented by the backend', () => {
  const chromatogram = source('src/components/hplc/ChromatogramAnalysis.tsx');

  assert.match(chromatogram, /useState\('mocca2_flatfit'\)/);
  assert.match(chromatogram, /<option value="mocca2_flatfit">MOCCA2 flatfit/);
  assert.match(chromatogram, /<option value="mocca2_arpls">MOCCA2 arPLS/);
  assert.match(chromatogram, /<option value="mocca2_asls">MOCCA2 asLS/);
  assert.doesNotMatch(chromatogram, /SNIP/);
  assert.doesNotMatch(chromatogram, /value="snip"/);
});
