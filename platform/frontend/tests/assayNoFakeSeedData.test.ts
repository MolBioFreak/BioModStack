import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const root = process.cwd();
const assayFiles = [
  'src/components/AssayAnalytics.tsx',
  'src/api/client.ts',
  'src/components/qpcr/RawDataImport.tsx',
  'src/components/qpcr/DeltaCqPanels.tsx',
  'src/components/qpcr/QuantificationPanels.tsx',
  'src/components/hplc/EmpowerImport.tsx',
  'src/components/hplc/ChromatogramAnalysis.tsx',
  'src/components/statistics/ControlChart.tsx',
  'src/components/statistics/DoEPanel.tsx',
  'src/components/statistics/HypothesisTesting.tsx',
  'src/components/statistics/ProcessCapability.tsx',
  'src/components/statistics/RegressionAnalysis.tsx',
];

const bannedSeedPatterns = [
  /const\s+SAMPLE_[A-Z_]+\s*=/,
  /useState\('1e6\\n1e5/,
  /useState\('15\.2\\n18\.5/,
  /useState\('Sample_A\\nSample_B/,
  /useState\('Caffeine'/,
  /useState\('Sample_1\\nSample_2/,
  /useState\('\[\[10\.2, 9\.8/,
  /useState\('-25\\n-30/,
  /useState\('10\\n15\\n20/,
  /useState\('52\\n68\\n85/,
  /Example SPC dataset/,
  /Example dataset/,
  /Seed Datasets/,
  /seedDatasets/,
  /datasets\/seed/,
  /\bseedStatus\b/,
  /parseFloat\([^\n]+\) \|\| 0/,
  /values\[headers\.indexOf\('[^']+'\)\] \|\| ''/,
  /first is control/i,
];

test('assay analytics UI does not pre-seed viewers with fake/demo data', () => {
  const violations: string[] = [];
  for (const relativePath of assayFiles) {
    const source = readFileSync(join(root, relativePath), 'utf8');
    for (const pattern of bannedSeedPatterns) {
      if (pattern.test(source)) {
        violations.push(`${relativePath}: ${pattern}`);
      }
    }
  }
  assert.deepEqual(violations, []);
});

test('assay import panels expose supported real instrument exports and reject proprietary containers', () => {
  const empower = readFileSync(join(root, 'src/components/hplc/EmpowerImport.tsx'), 'utf8');
  assert.match(empower, /Empower exports \(\.cdf, \.arw, \.zip, \.csv, \.txt\)/);
  assert.match(empower, /AIA \.cdf provides raw chromatograms and native peak tables/);
  assert.match(empower, /accept="\.cdf,\.arw,\.zip,\.csv,\.txt"/);
  assert.match(empower, /Unsupported native Empower database\/RAW files/);

  const qpcr = readFileSync(join(root, 'src/components/qpcr/RawDataImport.tsx'), 'utf8');
  assert.match(qpcr, /QuantStudio \.eds/);
  assert.match(qpcr, /Large uploads are allowed through the BMS nginx proxy/);
});

test('assay sections use BMS-native workbench copy and explicit unavailable states', () => {
  const qpcrIndex = readFileSync(join(root, 'src/components/qpcr/index.tsx'), 'utf8');
  const hplcIndex = readFileSync(join(root, 'src/components/hplc/index.tsx'), 'utf8');
  const statsIndex = readFileSync(join(root, 'src/components/statistics/index.tsx'), 'utf8');

  assert.match(qpcrIndex, /BMS qPCR Workbench/);
  assert.match(qpcrIndex, /\/api\/assay-analytics qPCR routes/);
  assert.match(qpcrIndex, /BMS does not preload\s+built-in assay rows/);

  assert.match(hplcIndex, /BMS Chromatography Workbench/);
  assert.match(hplcIndex, /Proprietary Empower DB\/RAW files: export AIA \.cdf\/\.arw or CSV\/ASCII first/);
  assert.match(hplcIndex, /Waters chromatogram review/);

  assert.match(statsIndex, /BMS DOE \+ Statistics Workbench/);
  assert.match(statsIndex, /\/api\/assay-analytics DOE\/statistics routes/);
  assert.match(statsIndex, /Empty workbench until pasted values or generated DOE output exists/);

  assert.doesNotMatch(qpcrIndex + hplcIndex + statsIndex, /Data Processor|Statistical Toolkit/);
});
